"""UART (pyserial 不要) のテスト。実機の代わりに出力を再生する偽ポートを使う。"""

import sys
import time
import unittest
from unittest import mock

from helpers import TempProject

from copilot_harness import serialport
from copilot_harness.runner import uart_session

MCU_OUTPUT = [
    b"BOOT\r\n", b"TEST:UART:PA", b"SS\r\nTEST:ADC:PASS:value=2015\r\n",
    b"TEST:CAN_TX:PASS\r\nTEST:CAN_RX:PASS\r\n", b"TEST:END\r\n", b"after end\r\n",
]


class FakeSerial:
    def __init__(self, chunks, **kw):
        self.chunks = list(chunks)
        self.signals: list[tuple[str, bool]] = []
        self.written = b""
        self.closed = False
        self.kw = kw

    @property
    def in_waiting(self):
        return len(self.chunks[0]) if self.chunks else 0

    def read(self, size=1):
        if not self.chunks:
            time.sleep(0.01)
            return b""
        return self.chunks.pop(0)

    def write(self, data):
        self.written += data
        return len(data)

    def __setattr__(self, name, value):
        if name in ("dtr", "rts"):
            self.signals.append((name, value))
        else:
            super().__setattr__(name, value)

    def close(self):
        self.closed = True


def opener_for(fake):
    def opener(port, baud, **kw):
        fake.kw = {"port": port, "baud": baud, **kw}
        return fake
    return opener


UART = {"port": "COM9", "baudrate": 115200, "timeout": 5, "end_marker": "TEST:END"}


class UartSessionTest(unittest.TestCase):
    def test_reads_until_end_marker(self):
        fake = FakeSerial(MCU_OUTPUT)
        log, err = uart_session(UART, opener=opener_for(fake))
        self.assertEqual(err, "")
        texts = [ln.split("] ", 1)[1] for ln in log.splitlines()]
        self.assertEqual(texts, ["BOOT", "TEST:UART:PASS", "TEST:ADC:PASS:value=2015",
                                 "TEST:CAN_TX:PASS", "TEST:CAN_RX:PASS", "TEST:END"])
        self.assertTrue(fake.closed)
        self.assertEqual((fake.kw["port"], fake.kw["baud"], fake.kw["dtr"], fake.kw["rts"]), ("COM9", 115200, True, True))

    def test_timeout_and_partial_line(self):
        fake = FakeSerial([b"partial without newline"])
        log, err = uart_session(UART, duration=0.3, opener=opener_for(fake))
        self.assertEqual(err, "")
        self.assertIn("partial without newline", log)
        self.assertIn("harness: UART timeout (0.3s)", log)

    def test_reset_modes_and_send(self):
        fake = FakeSerial([b"TEST:END\n"])
        uart_session({**UART, "reset": "dtr", "send": "RUN\n", "send_delay": 0}, opener=opener_for(fake))
        self.assertEqual(fake.signals, [("dtr", False), ("dtr", True)])
        self.assertEqual(fake.written, b"RUN\n")
        fake = FakeSerial([b"TEST:END\n"])
        uart_session({**UART, "reset": "rts"}, opener=opener_for(fake))
        self.assertEqual(fake.signals, [("dtr", False), ("rts", True), ("rts", False)])

    def test_open_error_and_missing_port(self):
        def failing(*a, **kw):
            raise serialport.SerialError(5, "COM9: open 失敗 (アクセス拒否)")
        log, err = uart_session(UART, opener=failing)
        self.assertIn("アクセス拒否", err)
        self.assertEqual(uart_session({**UART, "port": ""})[1], "test.uart.port が未設定です")

    def test_cp932_output(self):
        fake = FakeSerial(["温度=25℃\r\n".encode("cp932"), b"TEST:END\n"])
        log, _ = uart_session({**UART, "encoding": "cp932"}, opener=opener_for(fake))
        self.assertIn("温度=25℃", log)


class UartHardwareTestModeTest(TempProject):
    """test.mode: uart で build → flash → UART テスト → 判定まで通ること。"""

    def test_run_with_uart_mode(self):
        from copilot_harness.execute import do_run
        from copilot_harness.workflow import Harness
        self.write("src/main.c", "int main(void) { return 0; }\n")
        py = sys.executable.replace("\\", "/")
        self.write_config(
            "project:\n  root: ..\n  languages: [c]\n"
            f"build:\n  command: [\"{py}\", \"-c\", \"print('build ok')\"]\n"
            f"flash:\n  command: [\"{py}\", \"-c\", \"print('flash ok')\"]\n"
            "test:\n  mode: uart\n  required: [UART, ADC, CAN_TX, CAN_RX]\n"
            "  uart:\n    port: COM9\n    timeout: 5\n"
        )
        fake = FakeSerial(MCU_OUTPUT)
        with mock.patch("copilot_harness.serialport.open_port", opener_for(fake)):
            h = Harness(self.cfg)
            h.start("UART test", ["main"], [])
            self.assertTrue(do_run(h))
        st = Harness(self.cfg).state
        self.assertEqual(st["boot"], "PASS")
        self.assertEqual(st["tests"]["ADC"]["detail"], "value=2015")
        self.assertIn("TEST:CAN_RX:PASS", (self.hdir / "handoff" / "TEST_RESULT.md").read_text(encoding="utf-8"))


@unittest.skipUnless(sys.platform == "win32", "Windows API のテスト")
class WindowsBackendTest(unittest.TestCase):
    def test_struct_layout(self):
        import ctypes
        self.assertEqual(ctypes.sizeof(serialport._DCB), 28)
        self.assertEqual(ctypes.sizeof(serialport._COMMTIMEOUTS), 20)

    def test_missing_port(self):
        with self.assertRaises(serialport.SerialError) as cm:
            serialport.Serial("COM250", 115200)
        self.assertEqual(cm.exception.errno, 2)
        self.assertIn("存在しません", str(cm.exception))

    def test_list_ports(self):
        for name, desc in serialport.list_ports():
            self.assertTrue(name.upper().startswith("COM"))

    def test_builtin_backend_selected(self):
        self.assertEqual(serialport.backend_name("builtin"), "builtin")


if __name__ == "__main__":
    unittest.main()
