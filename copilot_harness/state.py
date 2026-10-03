"""Harness 内部 State (state.json) の管理。

STATE.md はこの内部 State から毎回生成される。Chat 履歴は状態の正としない。
"""

from __future__ import annotations

import copy
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .util import HarnessError, now_iso, read_text, write_text

# フェーズ
INIT = "INIT"
WAITING_COPILOT = "WAITING_COPILOT"
PATCH_APPLIED = "PATCH_APPLIED"
BUILD_FAILED = "BUILD_FAILED"
FLASH_FAILED = "FLASH_FAILED"
TEST_FAILED = "TEST_FAILED"
WAITING_TEST = "WAITING_TEST_RESULT"
DONE = "DONE"
HUMAN_REVIEW_REQUIRED = "HUMAN_REVIEW_REQUIRED"

# ステージ結果
PASS = "PASS"
FAIL = "FAIL"
NOT_RUN = "NOT_RUN"
SKIPPED = "SKIPPED"

NEXT_ACTION = {
    INIT: "タスクを開始してください: python harness.py start \"<task>\"",
    WAITING_COPILOT: "handoff/ の3ファイルと NEXT_PROMPT.txt を Copilot へ渡し、回答を inbox/copilot_response.txt へ保存",
    PATCH_APPLIED: "python harness.py run を実行 (build → flash → test)",
    BUILD_FAILED: "handoff/ を Copilot へ渡す (build error)",
    FLASH_FAILED: "書込み環境 (ポート/接続) を確認し python harness.py flash を再実行",
    TEST_FAILED: "handoff/ を Copilot へ渡す (test failure)",
    WAITING_TEST: "テストを実行し、結果を python harness.py result --paste / --pass / --fail で入力",
    DONE: "python harness.py diff (または git diff) で変更をレビューし、人が commit してください",
    HUMAN_REVIEW_REQUIRED: "人によるレビューが必要です (status の理由を確認)",
}

DEFAULT_STATE: dict[str, Any] = {
    "version": 1,
    "run_id": "",
    "run_date": "",
    "run_seq": 0,
    "task": "",
    "task_id": "",
    "keywords": [],
    "phase": INIT,
    "phase_reason": "",
    "iteration": 0,
    # 直近の実行結果 (results_run_id の RUN で計測)
    "results_run_id": "",
    "build": NOT_RUN,
    "flash": NOT_RUN,
    "boot": NOT_RUN,
    "tests": {},
    "failures": [],
    "build_error_files": [],
    "test_failure_files": [],
    "test_failure_keywords": [],
    "build_excerpt": "",
    "flash_excerpt": "",
    "uart_excerpt": "",
    # 人 / Copilot による知見
    "verified": [],
    "finding": "",
    "suspects": [],
    "constraints": [],
    "next_objective": "",
    # Context 関連
    "requested_files": [],
    "missing_files": [],
    "last_response_note": "",
    # Patch
    "last_patch": None,
    "harness_changed_files": [],
    "processed_responses": [],
    "history": [],
    "updated_at": "",
}


class State:
    def __init__(self, path: Path, data: dict[str, Any]):
        self.path = path
        self.data = data

    @classmethod
    def load(cls, path: Path) -> "State":
        data = copy.deepcopy(DEFAULT_STATE)
        if path.exists():
            try:
                loaded = json.loads(read_text(path))
            except json.JSONDecodeError as e:
                raise HarnessError(f"state.json が壊れています: {e}") from e
            data.update(loaded)
        return cls(path, data)

    def save(self) -> None:
        self.data["updated_at"] = now_iso()
        write_text(self.path, json.dumps(self.data, ensure_ascii=False, indent=2) + "\n")

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.data[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    # ---- RUN_ID ----
    def new_run_id(self, today: datetime | None = None) -> str:
        """``YYYYMMDD-NNNN`` 形式で日毎に連番を振る。"""
        date = (today or datetime.now()).strftime("%Y%m%d")
        if self.data.get("run_date") != date:
            self.data["run_date"] = date
            self.data["run_seq"] = 0
        self.data["run_seq"] = int(self.data.get("run_seq", 0)) + 1
        self.data["run_id"] = f"{date}-{self.data['run_seq']:04d}"
        return self.data["run_id"]

    def ensure_run_id(self) -> str:
        return self.data["run_id"] or self.new_run_id()

    # ---- フェーズ ----
    def set_phase(self, phase: str, reason: str = "") -> None:
        self.data["phase"] = phase
        self.data["phase_reason"] = reason

    def next_action(self) -> str:
        return NEXT_ACTION.get(self.data["phase"], "")

    # ---- 結果 ----
    def reset_results(self, run_id: str) -> None:
        self.data.update(
            results_run_id=run_id, build=NOT_RUN, flash=NOT_RUN, boot=NOT_RUN,
            tests={}, failures=[], build_error_files=[], test_failure_files=[],
            test_failure_keywords=[], build_excerpt="", flash_excerpt="", uart_excerpt="",
        )

    def add_history(self, entry: dict[str, Any], keep: int = 50) -> None:
        entry = {"at": now_iso(), **entry}
        self.data["history"].append(entry)
        self.data["history"] = self.data["history"][-keep:]

    def update_last_history(self, **fields: Any) -> None:
        """現在 RUN の履歴エントリへ結果を追記する。"""
        run_id = self.data["run_id"]
        for entry in reversed(self.data["history"]):
            if entry.get("run_id") == run_id:
                entry.update(fields)
                return
        self.add_history({"run_id": run_id, "action": "RUN", **fields})

    def start_task(self, task: str, keywords: list[str], constraints: list[str]) -> None:
        keep = {k: self.data[k] for k in ("run_date", "run_seq")}
        self.data = copy.deepcopy(DEFAULT_STATE)
        self.data.update(keep)
        self.data["task"] = task
        self.data["keywords"] = keywords
        self.data["constraints"] = constraints
        self.data["task_id"] = datetime.now().strftime("%Y%m%d-%H%M%S")
