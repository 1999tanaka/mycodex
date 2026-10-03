"""Build / Flash / Hardware Test Runner。

実行するのは config.yaml に事前定義されたコマンドのみ。Copilot 出力由来のコマンドは実行しない。
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .state import FAIL, NOT_RUN, PASS, SKIPPED
from .util import write_text


@dataclass
class CommandResult:
    stage: str
    status: str
    command: str = ""
    returncode: int | None = None
    output: str = ""
    duration: float = 0.0
    reason: str = ""


def _decode(data: bytes) -> str:
    for enc in ("utf-8", "cp932"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def to_argv(command) -> list[str] | str:
    """config のコマンドを subprocess 引数へ。shell は使わない。

    先頭が ``python`` / ``python3`` の場合は Harness と同じ Python で実行する。
    """
    if isinstance(command, (list, tuple)):
        argv = [str(x) for x in command]
        if argv and argv[0] in ("python", "python3", "py"):
            argv[0] = sys.executable
        return argv
    s = " ".join(str(command).split())  # YAML の折り返し (>) を 1 行へ
    first, _, rest = s.partition(" ")
    if os.name == "nt":
        if first.lower() in ("python", "python3", "py", "python.exe"):
            s = f'"{sys.executable}" {rest}'.strip()
        return s  # Windows は CreateProcess に文字列をそのまま渡す
    argv = shlex.split(s)
    if argv and argv[0] in ("python", "python3"):
        argv[0] = sys.executable
    return argv


def _kill_tree(proc: subprocess.Popen) -> None:
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
        else:
            proc.kill()
    except OSError:
        pass


def run_command(stage: str, command, cwd: Path, timeout: float, env_extra: dict[str, str] | None = None) -> CommandResult:
    if not command or (isinstance(command, str) and not command.strip()):
        return CommandResult(stage, SKIPPED, reason="command 未設定")
    argv = to_argv(command)
    shown = argv if isinstance(argv, str) else " ".join(shlex.quote(a) for a in argv)
    env = dict(os.environ, PYTHONIOENCODING="utf-8", **(env_extra or {}))
    start = time.monotonic()
    try:
        proc = subprocess.Popen(argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, env=env, shell=False)
    except (FileNotFoundError, PermissionError, OSError) as e:
        return CommandResult(stage, FAIL, shown, None, "", 0.0, f"コマンドを起動できません: {e}")
    try:
        out, _ = proc.communicate(timeout=timeout)
        reason = "" if proc.returncode == 0 else f"exit code {proc.returncode}"
        status = PASS if proc.returncode == 0 else FAIL
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        out, _ = proc.communicate()
        status, reason = FAIL, f"TIMEOUT ({timeout}s)"
    return CommandResult(stage, status, shown, proc.returncode, _decode(out or b""),
                         time.monotonic() - start, reason)


def write_log(path: Path, res: CommandResult) -> None:
    head = (
        f"# stage: {res.stage}\n# command: {res.command}\n# status: {res.status} {res.reason}\n"
        f"# returncode: {res.returncode}\n# duration: {res.duration:.1f}s\n# at: {datetime.now().isoformat(timespec='seconds')}\n\n"
    )
    write_text(path, head + res.output)


# ---------------------------------------------------------------- ログ抜粋
ERROR_RE = re.compile(r"error|fatal|undefined reference|multiple definition|not declared|エラー|failed|failure|timeout|assert|panic|fault|exception", re.I)


def excerpt(text: str, max_lines: int = 40, context: int = 2, tail: int = 10) -> str:
    """生ログ全体ではなく、エラー周辺と末尾を抜粋する。"""
    lines = [ln.rstrip() for ln in (text or "").replace("\r\n", "\n").split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    if len(lines) <= max_lines:
        return "\n".join(lines)
    keep: set[int] = set()
    for i, ln in enumerate(lines):
        if ERROR_RE.search(ln):
            keep.update(range(max(0, i - context), min(len(lines), i + context + 1)))
    keep.update(range(max(0, len(lines) - tail), len(lines)))
    idx = sorted(keep)
    if len(idx) > max_lines:  # 先頭側のエラー (根本原因) を優先しつつ末尾も残す
        idx = idx[: max_lines - tail] + idx[-tail:]
        idx = sorted(set(idx))
    out: list[str] = []
    prev = -1
    for i in idx:
        if i != prev + 1:
            out.append("...")
        out.append(lines[i])
        prev = i
    return "\n".join(out)


# ---------------------------------------------------------------- MCU テストプロトコル
TEST_LINE_RE = re.compile(r"TEST:([A-Za-z0-9_.\-]+):(PASS|FAIL|SKIP|ERROR)(?::([^\r\n]*))?", re.I)


@dataclass
class TestOutcome:
    status: str = NOT_RUN
    boot: str = NOT_RUN
    tests: dict[str, dict[str, str]] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    output: str = ""


def parse_test_output(text: str, required: list[str], boot_marker: str = "") -> TestOutcome:
    """``TEST:<NAME>:<PASS|FAIL|SKIP>[:detail]`` 形式を解析する。"""
    oc = TestOutcome(output=text)
    for m in TEST_LINE_RE.finditer(text or ""):
        name, status, detail = m.group(1).upper(), m.group(2).upper(), (m.group(3) or "").strip()
        if name == "BOOT":
            oc.boot = PASS if status == "PASS" else FAIL
            continue
        oc.tests[name] = {"status": status, "detail": detail}

    if oc.boot == NOT_RUN:
        if boot_marker and boot_marker in (text or ""):
            oc.boot = PASS
        elif oc.tests:
            oc.boot = PASS
        elif not (text or "").strip():
            oc.boot = FAIL
        else:
            oc.boot = "UNKNOWN"

    req = [r.upper() for r in required]
    for r in req:
        if r not in oc.tests:
            oc.tests[r] = {"status": "FAIL", "detail": "MISSING (結果が出力されなかった)"}
    judged = req or list(oc.tests)
    for name, t in oc.tests.items():
        if t["status"] in ("FAIL", "ERROR"):
            msg = f"{name}: {t['detail'] or t['status']}"
            if name in judged:
                oc.failures.append(msg)
            else:
                oc.warnings.append(f"(required 外) {msg}")
    if oc.boot == FAIL:
        oc.failures.insert(0, "BOOT: MCU からの出力がありません")
    if not oc.tests:
        oc.failures.append("TEST 行が 1 件もありません")
    oc.status = PASS if not oc.failures else FAIL
    return oc


# ---------------------------------------------------------------- 終了コード判定 (pytest / unittest など)
# "FAILED tests/x.py::test_a - msg" (pytest)。unittest の集計行 "FAILED (failures=2)" は除く
PYTEST_FAIL_RE = re.compile(r"^(FAILED|ERROR) ([^\s(]\S*?)(?: - (.*))?\s*$", re.M)
UNITTEST_FAIL_RE = re.compile(r"^(FAIL|ERROR): (\S+) \(([^)]*)\)", re.M)
PYTEST_SUMMARY_RE = re.compile(r"^=+ (.*?\d+ (?:passed|failed|error|errors|skipped).*?) in [\d.]+s", re.M)
UNITTEST_RAN_RE = re.compile(r"^Ran (\d+) tests? in", re.M)
_EXC_LINE_RE = re.compile(r"^\s*[\w.]*(Error|Exception|Failure)\b.*")


def _unittest_detail(lines: list[str], start: int) -> str:
    """unittest の FAIL/ERROR ブロックから最後の例外行を取り出す。"""
    detail = ""
    for ln in lines[start + 1:]:
        if ln.startswith(("=====", "Ran ")) or (ln.startswith(("FAIL: ", "ERROR: ")) and detail):
            break
        if _EXC_LINE_RE.match(ln):
            detail = ln.strip()
    return detail


def parse_exitcode_output(text: str, returncode: int | None, required: list[str]) -> TestOutcome:
    """終了コードで成否を判定し、pytest / unittest の失敗テスト名をログから抽出する。

    出力に TEST:<NAME>:<PASS|FAIL> 行があればそれも使う。
    """
    text = text or ""
    if TEST_LINE_RE.search(text):
        oc = parse_test_output(text, required)
        if returncode not in (0, None) and oc.status == PASS:
            oc.failures.append(f"TEST_COMMAND: exit code {returncode}")
            oc.status = FAIL
        return oc
    oc = TestOutcome(output=text, boot=NOT_RUN)
    m = PYTEST_SUMMARY_RE.search(text)
    ran = UNITTEST_RAN_RE.search(text)
    summary = m.group(1) if m else (f"{ran.group(1)} tests" if ran else "")
    if returncode == 0:
        oc.tests["ALL"] = {"status": "PASS", "detail": summary or "exit code 0"}
        oc.status = PASS
        return oc
    lines = text.replace("\r\n", "\n").split("\n")
    for m in PYTEST_FAIL_RE.finditer(text):
        oc.tests[m.group(2)] = {"status": "FAIL", "detail": (m.group(3) or m.group(1)).strip()}
    for i, ln in enumerate(lines):
        um = UNITTEST_FAIL_RE.match(ln)
        if um:
            oc.tests[um.group(3)] = {"status": "FAIL", "detail": _unittest_detail(lines, i) or um.group(1)}
    if not oc.tests:
        oc.tests["TEST_COMMAND"] = {"status": "FAIL", "detail": f"exit code {returncode}"}
    oc.failures = [f"{name}: {t['detail']}" for name, t in oc.tests.items()]
    oc.status = FAIL
    return oc


def _pulse_reset(ser, mode: str) -> None:
    """MCU をリセットする (USB-UART の DTR/RTS は True で信号 LOW)。

    - dtr: DTR を OFF→ON。Arduino Uno/Nano 等の自動リセット回路 (DTR─コンデンサ─RESET)
    - rts: DTR=OFF のまま RTS を ON→OFF。ESP32/ESP8266 等 (RTS→EN)
    """
    if mode == "dtr":
        ser.dtr = False
        time.sleep(0.1)
        ser.dtr = True
    elif mode == "rts":
        ser.dtr = False
        ser.rts = True
        time.sleep(0.1)
        ser.rts = False


def uart_session(uart: dict, *, duration: float | None = None, until: str | None = None,
                 echo=None, opener=None) -> tuple[str, str]:
    """UART を読み ``[HH:MM:SS] line`` 形式のログを返す。(log, error)

    pyserial が無くても標準ライブラリ版 (serialport.py) で動作する。
    duration=0 は時間無制限 (Ctrl+C / until で終了)。
    """
    from .serialport import open_port

    opener = opener or open_port
    port = str(uart.get("port") or "")
    baud = int(uart.get("baudrate", 115200))
    limit = float(uart.get("timeout", 30) if duration is None else duration)
    end_marker = str(uart.get("end_marker") or "") if until is None else until
    enc = str(uart.get("encoding") or "utf-8")
    lines: list[str] = []
    buf = b""

    def emit(text: str) -> None:
        line = f"[{datetime.now():%H:%M:%S}] {text}"
        lines.append(line)
        if echo:
            echo(line)

    if not port:
        return "", "test.uart.port が未設定です"
    try:
        ser = opener(port, baud, timeout=0.2, dtr=bool(uart.get("dtr", True)), rts=bool(uart.get("rts", True)),
                     backend=str(uart.get("backend") or "auto"), retry=float(uart.get("open_retry", 5)))
    except Exception as e:
        return "", f"UART を開けません: {e}"
    try:
        _pulse_reset(ser, str(uart.get("reset") or "none").lower())
        send = str(uart.get("send") or "")
        if send:
            time.sleep(float(uart.get("send_delay", 0.5)))
            ser.write(send.encode(enc))
        deadline = time.monotonic() + limit if limit > 0 else float("inf")
        while time.monotonic() < deadline:
            buf += ser.read(ser.in_waiting or 1)
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                text = raw.decode(enc, errors="replace").rstrip("\r")
                emit(text)
                if end_marker and end_marker in text:
                    return "\n".join(lines), ""
        if buf:
            emit(buf.decode(enc, errors="replace"))
        if limit > 0:
            emit(f"harness: UART timeout ({limit:g}s)")
    except KeyboardInterrupt:
        if buf:
            emit(buf.decode(enc, errors="replace"))
        emit("harness: 中断されました")
    except Exception as e:
        return "\n".join(lines), f"UART 読み取りエラー: {e}"
    finally:
        ser.close()
    return "\n".join(lines), ""


def capture_uart(uart: dict, timeout_override: float | None = None) -> tuple[str, str]:
    """hardware test 用: end_marker (既定 TEST:END) か timeout まで UART を読む。"""
    return uart_session(uart, duration=timeout_override)
