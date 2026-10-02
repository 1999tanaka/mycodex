"""HIL test: MCU の UART 出力を読み、TEST 行を標準出力へ中継する。

MCU 側は次の形式で結果を出力する:
    TEST:<NAME>:<PASS|FAIL|SKIP>[:detail]
    TEST:END

    python tools/hil_test.py --port COM5            # 実機 (pyserial 不要)
    python tools/hil_test.py --simulate             # デモ用 MCU シミュレーション

UART を読むだけなら、このスクリプトを使わずに config.yaml の test.mode: uart でも同じことができる。
"""

import argparse
import re
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def stamp(line: str) -> str:
    return f"[{datetime.now():%H:%M:%S}] {line}"


def open_serial(port: str, baud: int):
    """pyserial があれば使い、無ければ Harness 内蔵の標準ライブラリ版を使う。"""
    try:
        import serial
        return serial.Serial(port, baud, timeout=0.2)
    except ImportError:
        sys.path.insert(0, str(ROOT / ".copilot-harness"))
        from copilot_harness.serialport import Serial
        return Serial(port, baud, timeout=0.2)


def run_real(port: str, baud: int, timeout: float) -> int:
    failed = False
    with open_serial(port, baud) as ser:
        ser.reset_input_buffer()
        deadline = time.monotonic() + timeout
        buf = b""
        while time.monotonic() < deadline:
            buf += ser.read(ser.in_waiting or 1)
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                line = raw.decode("utf-8", errors="replace").rstrip("\r")
                print(stamp(line), flush=True)
                failed |= ":FAIL" in line
                if line.startswith("TEST:END"):
                    return 1 if failed else 0
    print(stamp("RX timeout: TEST:END not received"))
    return 1


def run_simulated() -> int:
    """ソースを見て MCU の振る舞いを模擬する (デモ専用)。"""
    can = (ROOT / "src" / "can.cpp").read_text(encoding="utf-8")
    body = re.search(r"void\s+can_init\s*\([^)]*\)\s*\{(.*?)\n\}", can, re.S)
    init = re.sub(r"//.*|/\*.*?\*/", "", body.group(1) if body else "", flags=re.S)
    rx_ok = "can_hw_enable_rx_interrupt()" in init

    print(stamp("BOOT"))
    print(stamp("CAN init 500kbps"))
    print(stamp("TEST:UART:PASS"))
    print(stamp("TEST:ADC:PASS:value=2015"))
    print(stamp("TX OK id=0x123"))
    print(stamp("TEST:CAN_TX:PASS"))
    if rx_ok:
        print(stamp("RX id=0x321 dlc=2"))
        print(stamp("TEST:CAN_RX:PASS"))
    else:
        print(stamp("RX timeout (100ms) g_rx_pending=0"))
        print(stamp("TEST:CAN_RX:FAIL:RX_TIMEOUT"))
    print(stamp("TEST:END"))
    return 0 if rx_ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM5")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--timeout", type=float, default=30)
    ap.add_argument("--simulate", action="store_true")
    a = ap.parse_args()
    return run_simulated() if a.simulate else run_real(a.port, a.baud, a.timeout)


if __name__ == "__main__":
    sys.exit(main())
