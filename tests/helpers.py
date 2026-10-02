"""テスト用ヘルパ: 一時プロジェクトの生成。"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from copilot_harness.config import Config  # noqa: E402
from copilot_harness.templates import STATIC_FILES  # noqa: E402

DEMO = REPO / "examples" / "arduino_can_demo"

BASE_CONFIG = """\
project:
  root: ..
  languages: [cpp, ino]
"""


class TempProject(unittest.TestCase):
    """setUp で空の一時プロジェクト (.copilot-harness 付き) を作る。"""

    config_text = BASE_CONFIG

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="harness_test_")
        self.root = Path(self._tmp.name) / "project"
        self.root.mkdir()
        self.hdir = self.root / ".copilot-harness"
        (self.hdir / "static").mkdir(parents=True)
        (self.hdir / "inbox").mkdir()
        for name, text in STATIC_FILES.items():
            (self.hdir / "static" / name).write_text(text, encoding="utf-8")
        self.write_config(self.config_text)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write_config(self, text: str) -> Config:
        (self.hdir / "config.yaml").write_text(text, encoding="utf-8")
        return self.cfg

    @property
    def cfg(self) -> Config:
        return Config.load(self.hdir)

    def write(self, rel: str, text: str, newline: str = "\n") -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.replace("\n", newline).encode("utf-8"))
        return p

    def copy_demo(self) -> None:
        for item in DEMO.iterdir():
            if item.name in ("sample_responses", "harness_config.yaml", "__pycache__"):
                continue
            dst = self.root / item.name
            if item.is_dir():
                shutil.copytree(item, dst, ignore=shutil.ignore_patterns("__pycache__"))
            else:
                shutil.copy2(item, dst)
        shutil.copy2(DEMO / "harness_config.yaml", self.hdir / "config.yaml")

    def git_init(self) -> None:
        git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "core.autocrlf=false"]
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "core.autocrlf", "false"], cwd=self.root, check=True)
        subprocess.run(["git", "add", "-A"], cwd=self.root, check=True)
        subprocess.run([*git, "commit", "-q", "-m", "init"], cwd=self.root, check=True)
