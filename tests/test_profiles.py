"""プロファイル (mcu / python / vba / tool)、手動テストモード、VBA (Excel 不要の範囲) のテスト。"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from helpers import TempProject

from copilot_harness import miniyaml, profiles
from copilot_harness import state as S
from copilot_harness.cli import main
from copilot_harness.config import Config
from copilot_harness.execute import do_run
from copilot_harness.templates import agent_rules, config_yaml
from copilot_harness.workflow import Harness

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


class DetectTest(unittest.TestCase):
    def detect(self, *files: str) -> str:
        with tempfile.TemporaryDirectory() as d:
            for f in files:
                p = Path(d) / f
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(b"x")
            return profiles.detect(Path(d))[0]

    def test_detect(self):
        self.assertEqual(self.detect("main.ino", "src/can.cpp"), "mcu")
        self.assertEqual(self.detect("tool.py", "tests/test_tool.py"), "python")
        self.assertEqual(self.detect("集計.xlsm"), "vba")
        self.assertEqual(self.detect("src/vba/Module1.bas", "tool.py"), "vba")
        self.assertEqual(self.detect("backup.ps1", "run.bat"), "tool")
        self.assertEqual(self.detect("README.txt"), "tool")


class TemplatesTest(unittest.TestCase):
    def test_configs_parse_same_with_both_parsers(self):
        for name in profiles.PROFILES:
            with self.subTest(profile=name):
                text = config_yaml(name, "集計 ブック.xlsm")
                data = miniyaml.load(text)
                self.assertEqual(data["project"]["profile"], name)
                if yaml is not None:
                    self.assertEqual(data, yaml.safe_load(text))
        self.assertEqual(miniyaml.load(config_yaml("vba", "集計 ブック.xlsm"))["vba"]["workbook"], "集計 ブック.xlsm")

    def test_agent_rules_persona(self):
        self.assertIn("組み込みソフトウェア", agent_rules("mcu"))
        self.assertIn("Excel VBA", agent_rules("vba"))
        self.assertIn("Python", agent_rules("python"))
        self.assertIn("ACTION: NEED_CONTEXT", agent_rules("tool"))


class InitProfileTest(unittest.TestCase):
    def test_init_auto_detects_and_force_switches(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "tool.py").write_text("print(1)\n", encoding="utf-8")
            self.assertEqual(main(["init", "--project", str(root)]), 0)
            cfg = Config.load(root / ".copilot-harness")
            self.assertEqual(cfg.get("project.profile"), "python")
            self.assertIn("Python", (root / ".copilot-harness" / "static" / "AGENT_RULES.md").read_text(encoding="utf-8"))
            self.assertEqual(main(["init", "--project", str(root), "--profile", "tool", "--force"]), 0)
            self.assertEqual(Config.load(root / ".copilot-harness").get("project.profile"), "tool")


class ManualModeTest(TempProject):
    config_text = (
        "project:\n  root: ..\n  profile: tool\n  languages: [powershell]\n"
        "build:\n  command: \"\"\n  required: false\n"
        "test:\n  mode: manual\n"
    )

    def test_manual_result_flow(self):
        self.write("tools/backup.ps1", "Write-Output 'backup'\n")
        h = Harness(self.cfg)
        h.start("バックアップスクリプトの改善", ["backup"], [])
        self.assertFalse(do_run(Harness(self.cfg)))
        self.assertEqual(Harness(self.cfg).state["phase"], S.WAITING_TEST)

        self.assertEqual(main(["--harness-dir", str(self.hdir), "result", "--fail", "出力先フォルダが作られない"]), 1)
        st = Harness(self.cfg).state
        self.assertEqual(st["phase"], S.TEST_FAILED)
        self.assertIn("出力先フォルダが作られない", st["failures"][0])

        clip = "TEST:BACKUP:PASS\r\nTEST:LOG:PASS:ok\r\n"
        with mock.patch("copilot_harness.clipboard.read_clipboard", return_value=clip):
            self.assertEqual(main(["--harness-dir", str(self.hdir), "result", "--paste"]), 0)
        st = Harness(self.cfg).state
        self.assertEqual(st["phase"], S.DONE)
        state_md = (self.hdir / "handoff" / "STATE.md").read_text(encoding="utf-8")
        self.assertIn("# TEST\n", state_md)
        self.assertNotIn("# FLASH", state_md)
        result_md = (self.hdir / "handoff" / "TEST_RESULT.md").read_text(encoding="utf-8")
        self.assertNotIn("# BOOT", result_md)

    def test_result_without_test_lines(self):
        Harness(self.cfg).start("task", [], [])
        with mock.patch("copilot_harness.clipboard.read_clipboard", return_value="こんにちは"):
            self.assertEqual(main(["--harness-dir", str(self.hdir), "result"]), 1)


SJIS_MODULE = "Attribute VB_Name = \"Calc\"\r\nOption Explicit\r\n' 税込み計算\r\nPublic Function Tax(ByVal v As Long) As Long\r\n    Tax = v * 1.08\r\nEnd Function\r\n"


class VbaProfileTest(TempProject):
    """Excel を使わない範囲: Shift-JIS モジュールへの patch、新規モジュールの文字コード。"""

    def setUp(self):
        super().setUp()
        self.write_config(config_yaml("vba", "集計.xlsm"))
        (self.root / "src" / "vba").mkdir(parents=True)
        (self.root / "src" / "vba" / "Calc.bas").write_bytes(SJIS_MODULE.encode("cp932"))
        (self.root / "集計.xlsm").write_bytes(b"PK\x03\x04 dummy workbook")
        shutil.which("git") and self.git_init()

    def respond(self, body: str) -> None:
        rid = Harness(self.cfg).state["run_id"]
        (self.hdir / "inbox" / "copilot_response.txt").write_text(
            f"ACTION: PATCH\nRUN_ID: {rid}\nBEGIN_PATCH\n{body}END_PATCH\nSUMMARY:\n税率修正\n", encoding="utf-8")

    def test_patch_sjis_module_and_new_module(self):
        Harness(self.cfg).start("税率を 10% にする", ["Tax"], [])
        ctx = (self.hdir / "handoff" / "SOURCE_CONTEXT.md").read_text(encoding="utf-8")
        self.assertIn("# FILE: src/vba/Calc.bas\n\n```vb\n", ctx)
        self.assertIn("' 税込み計算", ctx)  # Shift-JIS を正しく読めている
        self.respond(
            "--- a/src/vba/Calc.bas\n+++ b/src/vba/Calc.bas\n@@ -3,4 +3,4 @@\n ' 税込み計算\n"
            " Public Function Tax(ByVal v As Long) As Long\n-    Tax = v * 1.08\n+    Tax = v * 1.1\n End Function\n"
            "--- /dev/null\n+++ b/src/vba/Util.bas\n@@ -0,0 +1,2 @@\n+Attribute VB_Name = \"Util\"\n+' 共通処理\n"
        )
        h = Harness(self.cfg)
        self.assertEqual(h.apply(), "PATCH")
        data = (self.root / "src" / "vba" / "Calc.bas").read_bytes()
        self.assertEqual(data.decode("cp932"), SJIS_MODULE.replace("v * 1.08", "v * 1.1"))
        self.assertEqual((self.root / "src" / "vba" / "Util.bas").read_bytes(),
                         "Attribute VB_Name = \"Util\"\r\n' 共通処理\r\n".encode("cp932"))

    def test_workbook_is_never_patched(self):
        Harness(self.cfg).start("task", [], [])
        self.respond("--- a/集計.xlsm\n+++ b/集計.xlsm\n@@ -1 +1 @@\n-PK\n+XX\n")
        with self.assertRaises(Exception):
            Harness(self.cfg).apply()

    @unittest.skipUnless(sys.platform == "win32" and shutil.which("powershell"), "Windows PowerShell が必要")
    def test_vba_build_reports_missing_workbook(self):
        (self.root / "集計.xlsm").unlink()
        Harness(self.cfg).start("task", [], [])
        self.assertFalse(do_run(Harness(self.cfg)))
        st = Harness(self.cfg).state
        self.assertEqual(st["build"], "FAIL")
        self.assertIn("ブックが見つかりません", st["build_excerpt"])


if __name__ == "__main__":
    unittest.main()
