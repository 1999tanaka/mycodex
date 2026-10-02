"""実機・Copilot 無しで Harness の 1 タスク分のサイクルを通すデモ。

    python examples/run_demo.py [--dest <dir>]

1. デモプロジェクトを作業ディレクトリへコピーし git 初期化
2. harness init → start → run (baseline: CAN_RX FAIL)
3. Copilot の回答 (sample_responses/) を inbox へ保存したものとして apply
   - NEED_CONTEXT → handoff 再生成
   - PATCH → 適用 (git apply または内蔵エンジン) → build → flash → test → TASK COMPLETE
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
DEMO = HERE / "arduino_can_demo"


def sh(*args: str, cwd: Path, check: bool = True) -> int:
    print(f"\n$ {' '.join(args)}", flush=True)
    rc = subprocess.run(list(args), cwd=cwd).returncode
    if check and rc != 0:
        sys.exit(f"command failed ({rc})")
    return rc


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", help="デモプロジェクトの作成先 (省略時は一時ディレクトリ)")
    a = ap.parse_args()
    dest = Path(a.dest).resolve() if a.dest else Path(tempfile.mkdtemp(prefix="harness_demo_")) / "project"
    if dest.exists():
        sys.exit(f"既に存在します: {dest}")
    shutil.copytree(DEMO, dest, ignore=shutil.ignore_patterns("sample_responses", "harness_config.yaml", "__pycache__"))
    print(f"demo project: {dest}")

    use_git = shutil.which("git") is not None and os.environ.get("HARNESS_NO_GIT", "") in ("", "0")
    if use_git:
        git = ["git", "-c", "user.name=harness-demo", "-c", "user.email=demo@example.invalid"]
        sh("git", "init", "-q", cwd=dest)
        sh("git", "add", "-A", cwd=dest)
        sh(*git, "commit", "-q", "-m", "demo baseline", cwd=dest)
    else:
        print("git なし: Harness は内蔵エンジンとスナップショットで動作します")

    py = sys.executable
    sh(py, str(REPO / "harness.py"), "init", "--project", str(dest), cwd=dest)
    hdir = dest / ".copilot-harness"
    shutil.copy2(DEMO / "harness_config.yaml", hdir / "config.yaml")
    harness = [py, str(hdir / "harness.py")]

    sh(*harness, "start", "CAN RX timeoutを修正する", "-k", "CAN,RX",
       "-c", "動的メモリ禁止", "-c", "bitrate変更禁止", "-c", "public API変更禁止", cwd=hdir)
    sh(*harness, "note", "-v", "CAN clock OK", "-v", "GPIO AF OK", "-v", "CAN bitrate 500kbps", "-v", "Physical bus OK", cwd=hdir)
    sh(*harness, "run", cwd=hdir, check=False)  # baseline: CAN_RX FAIL

    def respond(sample: str) -> None:
        run_id = re.search(r"RUN_ID: (\S+)", (hdir / "handoff" / "STATE.md").read_text(encoding="utf-8")).group(1)
        text = (DEMO / "sample_responses" / sample).read_text(encoding="utf-8").replace("{RUN_ID}", run_id)
        (hdir / "inbox" / "copilot_response.txt").write_text(text, encoding="utf-8")
        print(f"\n(Copilot の回答 {sample} を inbox/copilot_response.txt に保存 — RUN_ID {run_id})")

    respond("01_need_context.txt")
    sh(*harness, "apply", cwd=hdir)
    respond("02_patch.txt")
    rc = sh(*harness, "apply", "--run", cwd=hdir, check=False)
    sh(*harness, "status", cwd=hdir)
    sh(*harness, "diff", cwd=hdir)
    print(f"\nhandoff: {hdir / 'handoff'}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
