"""標準ライブラリだけのシリアルポート (pyserial 不要)。

- Windows : ctypes で Win32 API (CreateFile / SetCommState / ReadFile / EscapeCommFunction)
- Linux/macOS : termios + select

pyserial と同じ形の最小 API (read / write / in_waiting / dtr / rts / reset_input_buffer / close)
を提供する。pyserial がインストールされていれば backend="auto" でそちらを使う。
"""

from __future__ import annotations

import glob
import os
import sys
import time


class SerialError(OSError):
    def __str__(self) -> str:
        return self.strerror or super().__str__()


# ====================================================================== Windows
if sys.platform == "win32":
    import ctypes
    import winreg
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class _DCB(ctypes.Structure):
        _fields_ = [
            ("DCBlength", wintypes.DWORD),
            ("BaudRate", wintypes.DWORD),
            ("fBinary", wintypes.DWORD, 1),
            ("fParity", wintypes.DWORD, 1),
            ("fOutxCtsFlow", wintypes.DWORD, 1),
            ("fOutxDsrFlow", wintypes.DWORD, 1),
            ("fDtrControl", wintypes.DWORD, 2),
            ("fDsrSensitivity", wintypes.DWORD, 1),
            ("fTXContinueOnXoff", wintypes.DWORD, 1),
            ("fOutX", wintypes.DWORD, 1),
            ("fInX", wintypes.DWORD, 1),
            ("fErrorChar", wintypes.DWORD, 1),
            ("fNull", wintypes.DWORD, 1),
            ("fRtsControl", wintypes.DWORD, 2),
            ("fAbortOnError", wintypes.DWORD, 1),
            ("fDummy2", wintypes.DWORD, 17),
            ("wReserved", wintypes.WORD),
            ("XonLim", wintypes.WORD),
            ("XoffLim", wintypes.WORD),
            ("ByteSize", wintypes.BYTE),
            ("Parity", wintypes.BYTE),
            ("StopBits", wintypes.BYTE),
            ("XonChar", ctypes.c_char),
            ("XoffChar", ctypes.c_char),
            ("ErrorChar", ctypes.c_char),
            ("EofChar", ctypes.c_char),
            ("EvtChar", ctypes.c_char),
            ("wReserved1", wintypes.WORD),
        ]

    class _COMMTIMEOUTS(ctypes.Structure):
        _fields_ = [(n, wintypes.DWORD) for n in (
            "ReadIntervalTimeout", "ReadTotalTimeoutMultiplier", "ReadTotalTimeoutConstant",
            "WriteTotalTimeoutMultiplier", "WriteTotalTimeoutConstant")]

    class _COMSTAT(ctypes.Structure):
        _fields_ = [("flags", wintypes.DWORD), ("cbInQue", wintypes.DWORD), ("cbOutQue", wintypes.DWORD)]

    _k32.CreateFileW.restype = wintypes.HANDLE
    _k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                 wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    _k32.ReadFile.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
    _k32.WriteFile.argtypes = [wintypes.HANDLE, wintypes.LPCVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    for _fn in ("GetCommState", "SetCommState"):
        getattr(_k32, _fn).argtypes = [wintypes.HANDLE, ctypes.POINTER(_DCB)]
    _k32.SetCommTimeouts.argtypes = [wintypes.HANDLE, ctypes.POINTER(_COMMTIMEOUTS)]
    _k32.ClearCommError.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(_COMSTAT)]
    _k32.EscapeCommFunction.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _k32.PurgeComm.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _k32.SetupComm.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD]

    _INVALID = wintypes.HANDLE(-1).value
    _MAXDWORD = 0xFFFFFFFF
    _SETRTS, _CLRRTS, _SETDTR, _CLRDTR = 3, 4, 5, 6
    _PURGE_ALL = 0x0001 | 0x0002 | 0x0004 | 0x0008

    def _win_error(what: str, port: str) -> SerialError:
        code = ctypes.get_last_error()
        hint = {
            2: "ポートが存在しません (接続・ポート番号を確認)",
            5: "アクセス拒否: 他のプログラム (シリアルモニタ等) が使用中の可能性",
            31: "デバイスが応答しません (USB 再接続中の可能性)",
            121: "タイムアウト",
        }.get(code, ctypes.FormatError(code).strip())
        return SerialError(code, f"{port}: {what} 失敗 ({hint})")

    class _WinSerial:
        def __init__(self, port: str, baudrate: int, timeout: float, dtr: bool, rts: bool):
            self.port = port
            path = port if port.startswith("\\\\.\\") else "\\\\.\\" + port  # COM10 以上にも対応
            h = _k32.CreateFileW(path, 0x80000000 | 0x40000000, 0, None, 3, 0, None)
            if h in (None, _INVALID):
                raise _win_error("open", port)
            self._h = h
            try:
                _k32.SetupComm(h, 65536, 4096)
                dcb = _DCB()
                dcb.DCBlength = ctypes.sizeof(_DCB)
                if not _k32.GetCommState(h, ctypes.byref(dcb)):
                    raise _win_error("GetCommState", port)
                dcb.BaudRate = int(baudrate)
                dcb.ByteSize, dcb.Parity, dcb.StopBits = 8, 0, 0  # 8N1
                dcb.fBinary, dcb.fParity = 1, 0
                dcb.fOutxCtsFlow = dcb.fOutxDsrFlow = dcb.fDsrSensitivity = 0
                dcb.fOutX = dcb.fInX = dcb.fNull = dcb.fAbortOnError = dcb.fErrorChar = 0
                dcb.fDtrControl = 1 if dtr else 0
                dcb.fRtsControl = 1 if rts else 0
                if not _k32.SetCommState(h, ctypes.byref(dcb)):
                    raise _win_error(f"SetCommState (baudrate={baudrate})", port)
                self._set_read_timeout(timeout)
                _k32.PurgeComm(h, _PURGE_ALL)
            except Exception:
                _k32.CloseHandle(h)
                raise

        def _set_read_timeout(self, timeout: float) -> None:
            # MAXDWORD / MAXDWORD / N: 受信済みなら即返り、無ければ最大 N ms 待つ
            t = _COMMTIMEOUTS(_MAXDWORD, _MAXDWORD, max(1, int(timeout * 1000)), 0, 1000)
            if not _k32.SetCommTimeouts(self._h, ctypes.byref(t)):
                raise _win_error("SetCommTimeouts", self.port)

        @property
        def in_waiting(self) -> int:
            err, stat = wintypes.DWORD(), _COMSTAT()
            if not _k32.ClearCommError(self._h, ctypes.byref(err), ctypes.byref(stat)):
                raise _win_error("ClearCommError", self.port)
            return stat.cbInQue

        def read(self, size: int = 1) -> bytes:
            size = max(1, size)
            buf = ctypes.create_string_buffer(size)
            n = wintypes.DWORD()
            if not _k32.ReadFile(self._h, buf, size, ctypes.byref(n), None):
                raise _win_error("ReadFile", self.port)
            return buf.raw[: n.value]

        def write(self, data: bytes) -> int:
            n = wintypes.DWORD()
            if not _k32.WriteFile(self._h, data, len(data), ctypes.byref(n), None):
                raise _win_error("WriteFile", self.port)
            return n.value

        def _escape(self, func: int) -> None:
            if not _k32.EscapeCommFunction(self._h, func):
                raise _win_error("EscapeCommFunction", self.port)

        dtr = property(fset=lambda self, on: self._escape(_SETDTR if on else _CLRDTR))
        rts = property(fset=lambda self, on: self._escape(_SETRTS if on else _CLRRTS))

        def reset_input_buffer(self) -> None:
            _k32.PurgeComm(self._h, 0x0002 | 0x0008)

        def close(self) -> None:
            if getattr(self, "_h", None):
                _k32.CloseHandle(self._h)
                self._h = None

    def _list_ports() -> list[tuple[str, str]]:
        out = []
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DEVICEMAP\SERIALCOMM") as k:
                i = 0
                while True:
                    try:
                        dev, com, _ = winreg.EnumValue(k, i)
                    except OSError:
                        break
                    out.append((str(com), _describe(dev)))
                    i += 1
        except OSError:
            pass
        return sorted(out, key=lambda x: (len(x[0]), x[0]))

    def _describe(dev: str) -> str:
        d = dev.lower()
        for key, name in (("usbser", "USB CDC (Arduino/STM32 VCP/RP2040 等)"), ("silabser", "CP210x USB-UART"),
                          ("ch341", "CH340/CH341 USB-UART"), ("vcp", "FTDI USB-UART"), ("ftdi", "FTDI USB-UART"),
                          ("prolific", "PL2303 USB-UART"), ("ser2pl", "PL2303 USB-UART"), ("jlink", "J-Link VCOM"),
                          ("serial", "内蔵シリアルポート")):
            if key in d:
                return f"{name}  [{dev}]"
        return dev

# ====================================================================== POSIX
else:
    import fcntl
    import select
    import struct
    import termios

    class _PosixSerial:
        def __init__(self, port: str, baudrate: int, timeout: float, dtr: bool, rts: bool):
            self.port = port
            self.timeout = timeout
            speed = getattr(termios, f"B{int(baudrate)}", None)
            if speed is None:
                raise SerialError(22, f"{port}: 未対応の baudrate {baudrate}")
            try:
                self._fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
            except OSError as e:
                raise SerialError(e.errno, f"{port}: open 失敗 ({e.strerror})") from e
            try:
                iflag, oflag, cflag, lflag, _, _, cc = termios.tcgetattr(self._fd)
                iflag &= ~(termios.IGNBRK | termios.BRKINT | termios.PARMRK | termios.ISTRIP | termios.INLCR
                           | termios.IGNCR | termios.ICRNL | termios.IXON | termios.IXOFF | getattr(termios, "IXANY", 0))
                oflag &= ~termios.OPOST
                lflag &= ~(termios.ECHO | termios.ECHONL | termios.ICANON | termios.ISIG | termios.IEXTEN)
                cflag &= ~(termios.CSIZE | termios.PARENB | termios.CSTOPB | getattr(termios, "CRTSCTS", 0))
                cflag |= termios.CS8 | termios.CLOCAL | termios.CREAD
                cc[termios.VMIN], cc[termios.VTIME] = 0, 0
                termios.tcsetattr(self._fd, termios.TCSANOW, [iflag, oflag, cflag, lflag, speed, speed, cc])
                self.dtr, self.rts = dtr, rts
                termios.tcflush(self._fd, termios.TCIOFLUSH)
            except Exception:
                os.close(self._fd)
                raise

        @property
        def in_waiting(self) -> int:
            buf = fcntl.ioctl(self._fd, getattr(termios, "TIOCINQ", termios.FIONREAD), struct.pack("I", 0))
            return struct.unpack("I", buf)[0]

        def read(self, size: int = 1) -> bytes:
            r, _, _ = select.select([self._fd], [], [], self.timeout)
            if not r:
                return b""
            try:
                return os.read(self._fd, max(1, size))
            except BlockingIOError:
                return b""

        def write(self, data: bytes) -> int:
            return os.write(self._fd, data)

        def _modem(self, bit: int, on: bool) -> None:
            fcntl.ioctl(self._fd, termios.TIOCMBIS if on else termios.TIOCMBIC, struct.pack("I", bit))

        dtr = property(fset=lambda self, on: self._modem(termios.TIOCM_DTR, on))
        rts = property(fset=lambda self, on: self._modem(termios.TIOCM_RTS, on))

        def reset_input_buffer(self) -> None:
            termios.tcflush(self._fd, termios.TCIFLUSH)

        def close(self) -> None:
            if getattr(self, "_fd", None) is not None:
                os.close(self._fd)
                self._fd = None

    def _list_ports() -> list[tuple[str, str]]:
        pats = ["/dev/ttyUSB*", "/dev/ttyACM*", "/dev/ttyAMA*", "/dev/cu.usb*", "/dev/cu.SLAB*", "/dev/cu.wchusb*"]
        return [(p, "") for pat in pats for p in sorted(glob.glob(pat))]


# ====================================================================== 公開 API
class Serial:
    """標準ライブラリ版シリアルポート (pyserial の serial.Serial と同じ使い方)。"""

    def __init__(self, port: str, baudrate: int = 115200, timeout: float = 0.2, dtr: bool = True, rts: bool = True):
        impl = _WinSerial if sys.platform == "win32" else _PosixSerial
        self._impl = impl(port, baudrate, timeout, dtr, rts)
        self.port = port

    def __enter__(self) -> "Serial":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @property
    def in_waiting(self) -> int:
        return self._impl.in_waiting

    def read(self, size: int = 1) -> bytes:
        return self._impl.read(size)

    def write(self, data: bytes) -> int:
        return self._impl.write(data)

    @property
    def dtr(self):  # pragma: no cover - pyserial 互換のため
        raise AttributeError("write-only")

    @dtr.setter
    def dtr(self, on: bool) -> None:
        self._impl.dtr = on

    @property
    def rts(self):  # pragma: no cover
        raise AttributeError("write-only")

    @rts.setter
    def rts(self, on: bool) -> None:
        self._impl.rts = on

    def reset_input_buffer(self) -> None:
        self._impl.reset_input_buffer()

    def close(self) -> None:
        self._impl.close()


def list_ports() -> list[tuple[str, str]]:
    """(ポート名, 説明) の一覧。ポートは開かない。"""
    return _list_ports()


def backend_name(backend: str = "auto") -> str:
    if backend == "builtin":
        return "builtin"
    try:
        import serial  # noqa: F401
        return "pyserial"
    except ImportError:
        if backend == "pyserial":
            raise SerialError(0, "test.uart.backend: pyserial ですが pyserial がインストールされていません")
        return "builtin"


def open_port(port: str, baudrate: int, *, timeout: float = 0.2, dtr: bool = True, rts: bool = True,
              backend: str = "auto", retry: float = 0.0):
    """ポートを開く。flash 直後の USB 再接続に備え、retry 秒まで再試行する。"""
    deadline = time.monotonic() + max(0.0, retry)
    while True:
        try:
            if backend_name(backend) == "pyserial":
                import serial
                s = serial.Serial()
                s.port, s.baudrate, s.timeout = port, int(baudrate), timeout
                s.dtr, s.rts = dtr, rts
                s.open()
                return s
            return Serial(port, int(baudrate), timeout, dtr, rts)
        except Exception as e:  # SerialError / serial.SerialException
            if time.monotonic() >= deadline:
                if isinstance(e, SerialError):
                    raise
                raise SerialError(getattr(e, "errno", 0) or 0, f"{port}: {e}") from e
            time.sleep(0.5)
