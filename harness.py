#!/usr/bin/env python3
"""Local Coding Harness エントリポイント。

使い方:
    python harness.py init --project <project_dir>
    python harness.py start "タスク内容" --keywords CAN,RX
    python harness.py prepare
    python harness.py apply
    python harness.py run
    python harness.py status
"""

from __future__ import annotations

import sys
from pathlib import Path

if sys.version_info < (3, 11):
    sys.stderr.write("Python 3.11 以降が必要です。\n")
    sys.exit(2)

sys.path.insert(0, str(Path(__file__).resolve().parent))

from copilot_harness.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
