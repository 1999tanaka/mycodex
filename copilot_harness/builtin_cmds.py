"""config の build / test に書ける内蔵コマンド (``builtin:<名前>``)。

- builtin:python-syntax : 全 .py ファイルの構文チェック (コンパイルのみ。__pycache__ は作らない)
- builtin:vba-build     : src/vba のモジュールを Excel ブックへ取り込んで保存 (PowerShell + Excel COM)
- builtin:vba-test      : ブックのテスト用マクロを実行し TEST 行を出力
- builtin:vba-export    : ブックのモジュールを src/vba へ書き出す (CLI: python harness.py vba-export)
"""

from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

from .repo import scan_repository
from .runner import CommandResult, run_command
from .state import FAIL, PASS
from .util import read_text

PREFIX = "builtin:"
ASSETS = Path(__file__).resolve().parent / "assets"


def is_builtin(command) -> bool:
    return isinstance(command, str) and command.strip().startswith(PREFIX)


def run_builtin(command: str, h, stage: str) -> CommandResult:
    name = command.strip()[len(PREFIX):].strip()
    if name == "python-syntax":
        return python_syntax(h, stage)
    if name in ("vba-build", "vba-test", "vba-export"):
        return vba(h, name[len("vba-"):], stage)
    return CommandResult(stage, FAIL, command, reason=f"不明な内蔵コマンドです: {name}")


def python_syntax(h, stage: str) -> CommandResult:
    start = time.monotonic()
    errors: list[str] = []
    files = [rel for rel in scan_repository(h.cfg).source_files if rel.endswith(".py")]
    for rel in files:
        try:
            compile(read_text(h.root / rel), rel, "exec", dont_inherit=True)
        except SyntaxError as e:
            errors.append(f"{rel}:{e.lineno or 0}:{e.offset or 0}: error: SyntaxError: {e.msg}")
        except (ValueError, OSError) as e:
            errors.append(f"{rel}:0:0: error: {e}")
    out = "\n".join(errors + [f"python-syntax: {len(files)} files checked, {len(errors)} error(s)"])
    return CommandResult(
        stage, FAIL if errors else PASS, PREFIX + "python-syntax", 1 if errors else 0, out,
        time.monotonic() - start, f"構文エラー {len(errors)} 件" if errors else "",
    )


def vba(h, action: str, stage: str) -> CommandResult:
    cfg = h.cfg
    if sys.platform != "win32":
        return CommandResult(stage, FAIL, PREFIX + "vba-" + action, reason="Excel VBA 連携は Windows のみ対応です")
    shell = shutil.which("powershell") or shutil.which("pwsh")
    if not shell:
        return CommandResult(stage, FAIL, PREFIX + "vba-" + action, reason="PowerShell が見つかりません")
    timeout = int(cfg.get("vba.timeout", 180))
    argv = [
        shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-File", str(ASSETS / "vba_bridge.ps1"),
        "-Action", action,
        "-Workbook", str((h.root / str(cfg.get("vba.workbook", "Book1.xlsm"))).resolve()),
        "-Src", str((h.root / str(cfg.get("vba.src", "src/vba"))).resolve()),
        "-Macro", str(cfg.get("vba.test_macro", "HarnessTests.RunAll")),
        "-BackupDir", str(cfg.backup_dir / "vba"),
        "-TimeoutSec", str(timeout),
    ]
    res = run_command(stage, argv, h.root, timeout + 30)
    res.command = f"{PREFIX}vba-{action} ({cfg.get('vba.workbook')})"
    return res
