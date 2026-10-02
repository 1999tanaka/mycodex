"""config.yaml の読み込みと既定値。"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from . import miniyaml
from .util import HarnessError, read_text

try:
    import yaml  # PyYAML (任意。無ければ内蔵の miniyaml を使う)
except ImportError:  # pragma: no cover
    yaml = None


def yaml_backend() -> str:
    return "PyYAML" if yaml is not None else "built-in (miniyaml)"


def parse_yaml(text: str):
    if yaml is not None:
        try:
            return yaml.safe_load(text)
        except yaml.YAMLError as e:
            raise HarnessError(f"config.yaml の構文エラー: {e}") from e
    try:
        return miniyaml.load(text)
    except miniyaml.MiniYAMLError as e:
        raise HarnessError(
            f"config.yaml の構文エラー (内蔵 YAML パーサ): {e}\n"
            "  内蔵パーサは config.yaml で使う基本構文のみ対応です。高度な YAML 構文を使う場合は pip install pyyaml"
        ) from e


DEFAULT_CONFIG: dict[str, Any] = {
    "project": {
        "root": "..",
        "languages": ["cpp", "ino"],
        # 空なら languages から自動決定
        "source_extensions": [],
        "include_dirs": ["include", "src"],
        "exclude_dirs": [
            ".git", ".copilot-harness", ".pio", ".vscode", ".idea", "build",
            "Build", "Debug", "Release", "node_modules", "__pycache__", ".venv", "venv",
        ],
    },
    "security": {
        "allowed_paths": ["src", "include", "test", "*.ino"],
        "forbidden_paths": [
            ".git/**", ".copilot-harness/**", ".env", "*.key", "*.pem",
            "credential/**", "secret/**",
        ],
        "excluded_patterns": [
            ".env", "*.key", "*.pem", "password*", "credential*", "*secret*", "private*",
        ],
    },
    "limits": {
        "max_changed_files": 5,
        "max_added_lines": 500,
        "max_deleted_lines": 500,
        "max_iterations": 5,
    },
    "context": {
        "keywords": [],
        "pinned_files": [],
        "max_files": 12,
        "max_file_chars": 40000,
        "max_total_chars": 150000,
        "include_repo_map": True,
        "repo_map_max_lines": 120,
    },
    "patch": {
        # auto: git があれば git apply、無ければ内蔵エンジン / git / python
        "engine": "auto",
        # LLM の diff は hunk 行数がずれやすいため --recount を既定にする
        "git_apply_args": ["--recount"],
        # 対象ファイルが CRLF の場合に patch 側も CRLF へ合わせる
        "match_line_endings": True,
    },
    "build": {"command": "", "timeout": 120, "cwd": "."},
    "flash": {"command": "", "timeout": 60, "cwd": "."},
    "test": {
        # command: 外部テストスクリプトの出力を解析 / uart: Harness が直接 UART を読む
        "mode": "command",
        "command": "",
        "timeout": 120,
        "cwd": ".",
        "required": [],
        "log_excerpt_lines": 40,
        "uart": {
            "port": "COM5",
            "baudrate": 115200,
            "timeout": 30,
            "end_marker": "TEST:END",
            "boot_marker": "",
            "encoding": "utf-8",
            "dtr": True,
            "rts": True,
            "reset": "none",
            "send": "",
            "open_retry": 5,
            "backend": "auto",
        },
    },
    "handoff": {
        # Notebook を使わない場合は static/ の内容を handoff に埋め込む
        "use_notebook": False,
        "history_entries": 10,
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


class Config:
    def __init__(self, harness_dir: Path, data: dict[str, Any]):
        self.harness_dir = harness_dir.resolve()
        self.data = data
        self.project_root = (self.harness_dir / str(self.get("project.root", ".."))).resolve()

    @classmethod
    def load(cls, harness_dir: Path) -> "Config":
        path = harness_dir / "config.yaml"
        if not path.exists():
            raise HarnessError(f"config.yaml が見つかりません: {path}\n  先に `python harness.py init` を実行してください。")
        loaded = parse_yaml(read_text(path)) or {}
        if not isinstance(loaded, dict):
            raise HarnessError("config.yaml の最上位はマッピングである必要があります。")
        return cls(harness_dir, _deep_merge(DEFAULT_CONFIG, loaded))

    def get(self, dotted: str, default: Any = None) -> Any:
        cur: Any = self.data
        for key in dotted.split("."):
            if not isinstance(cur, dict) or key not in cur:
                return default
            cur = cur[key]
        return cur

    def list(self, dotted: str) -> list:
        v = self.get(dotted, [])
        if v is None:
            return []
        if isinstance(v, str):
            return [v]
        return list(v)

    # ---- 派生値 ----
    def source_extensions(self) -> set[str]:
        exts = [e.lower() if e.startswith(".") else "." + e.lower() for e in self.list("project.source_extensions")]
        if exts:
            return set(exts)
        lang_map = {
            "c": [".c", ".h"],
            "cpp": [".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx", ".h", ".c"],
            "ino": [".ino", ".pde"],
            "asm": [".s", ".S", ".asm"],
            "python": [".py"],
            "py": [".py"],
            "rust": [".rs"],
        }
        out: set[str] = set()
        for lang in self.list("project.languages"):
            out.update(e.lower() for e in lang_map.get(str(lang).lower(), []))
        return out or {".c", ".h", ".cpp", ".hpp", ".ino"}

    def context_exclusions(self) -> list[str]:
        return self.list("security.excluded_patterns") + self.list("security.forbidden_paths")

    def patch_denylist(self) -> list[str]:
        return self.list("security.forbidden_paths") + self.list("security.excluded_patterns")

    # ---- ディレクトリ ----
    @property
    def static_dir(self) -> Path:
        return self.harness_dir / "static"

    @property
    def handoff_dir(self) -> Path:
        return self.harness_dir / "handoff"

    @property
    def inbox_dir(self) -> Path:
        return self.harness_dir / "inbox"

    @property
    def logs_dir(self) -> Path:
        return self.harness_dir / "logs"

    @property
    def history_dir(self) -> Path:
        return self.harness_dir / "history"

    @property
    def backup_dir(self) -> Path:
        return self.harness_dir / "backup"

    @property
    def state_path(self) -> Path:
        return self.harness_dir / "state.json"

    @property
    def response_path(self) -> Path:
        return self.inbox_dir / "copilot_response.txt"
