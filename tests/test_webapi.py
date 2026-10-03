"""ブラウザ版 (web/) から呼ぶ webapi と、ブラウザ内実行の振る舞い。ブラウザ内は IN_BROWSER を差し替えて再現する。"""

import json
import shutil
import unittest
from pathlib import Path
from unittest import mock

from helpers import REPO, TempProject

from copilot_harness import webapi
from copilot_harness.runner import parse_exitcode_output

DEMO = REPO / "examples" / "python_tool_demo"


class WebApiTest(TempProject):
    def setUp(self):
        super().setUp()
        shutil.rmtree(self.hdir)  # init から始める
        for p in DEMO.rglob("*"):
            if p.is_file() and p.name != "sample_response.txt" and "__pycache__" not in p.parts:
                dst = self.root / p.relative_to(DEMO)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, dst)
        webapi.configure(str(self.root))
        self.browser = mock.patch("copilot_harness.execute.IN_BROWSER", True)
        self.browser.start()

    def tearDown(self):
        self.browser.stop()
        webapi.configure("/project")
        super().tearDown()

    def call(self, name, *args):
        return json.loads(getattr(webapi, name)(*args))

    def test_full_cycle_like_browser(self):
        st = self.call("ui_state")
        self.assertFalse(st["initialized"])
        self.assertEqual(st["guess"], "python")
        self.assertEqual(self.call("init", "auto")["code"], 0)
        self.assertEqual(self.call("ui_state")["phase"], "NO_TASK")

        r = self.call("run_cli", json.dumps(["start", "消費税を 10% にする", "-k", "tax"]))
        self.assertEqual(r["code"], 0, r)
        r = self.call("run_cli", json.dumps(["run"]))
        self.assertIn("builtin:python-unittest", r["output"])  # ブラウザ内で unittest に置き換え
        st = self.call("ui_state")
        self.assertEqual(st["phase"], "TEST_FAILED")
        self.assertEqual(sorted(t["name"] for t in st["tests"]),
                         ["tests.test_calc.CalcTest.test_price_with_tax", "tests.test_calc.CalcTest.test_total_with_tax"])
        self.assertIn("NEXT_PROMPT.txt", st["handoff"])
        bundle = webapi.handoff_bundle()
        self.assertIn("===== SOURCE_CONTEXT.md =====", bundle)
        self.assertIn("以下に貼り付けた", bundle)

        answer = (DEMO / "sample_response.txt").read_text(encoding="utf-8").replace("{RUN_ID}", st["run_id"])
        r = self.call("paste_text", answer, True)
        self.assertEqual(r["code"], 0, r)
        st = self.call("ui_state")
        self.assertEqual(st["phase"], "DONE")  # 修正後のコードでテストが再実行され合格 (モジュールの読み直し)
        self.assertIn("+TAX_RATE = 0.10", webapi.diff_text())

        r = self.call("rollback_with_reason", "定数ではなく設定で", "関数名は変えない")
        st = self.call("ui_state")
        self.assertEqual((st["phase"], st["finding"], st["constraints"]), ("TEST_FAILED", "定数ではなく設定で", ["関数名は変えない"]))

    def test_external_commands_become_manual(self):
        self.call("init", "mcu")
        self.call("run_cli", json.dumps(["start", "CAN RX", "-k", "can"]))
        self.call("run_cli", json.dumps(["run"]))
        st = self.call("ui_state")
        self.assertEqual((st["phase"], st["build"]), ("WAITING_TEST_RESULT", "MANUAL"))
        self.assertTrue(any("arduino-cli compile" in c for c in st["local_commands"]))

        self.call("manual_result", False, "build", "コンパイルエラー", "calc.py:3:1: error: 'x' was not declared\n")
        st = self.call("ui_state")
        self.assertEqual((st["phase"], st["build"]), ("BUILD_FAILED", "FAIL"))
        self.assertIn("calc.py — P2 build error", st["handoff"]["SOURCE_CONTEXT.md"])

        self.call("run_cli", json.dumps(["run"]))
        self.call("manual_result", False, "flash", "COM4 が見つからない", "")
        self.assertEqual(self.call("ui_state")["phase"], "FLASH_FAILED")

        self.call("run_cli", json.dumps(["run"]))
        self.call("manual_result", True, "test", "", "")
        st = self.call("ui_state")
        self.assertEqual((st["phase"], st["build"], st["flash"]), ("DONE", "PASS", "PASS"))

    def test_file_editing_keeps_encoding(self):
        self.call("init", "auto")
        p = self.root / "sjis.bas"
        p.write_bytes("' 日本語\r\nSub A()\r\nEnd Sub\r\n".encode("cp932"))
        f = self.call("get_file", "sjis.bas")
        self.assertEqual((f["encoding"], f["eol"]), ("cp932", "\r\n"))
        self.assertTrue(self.call("put_file", "sjis.bas", f["text"] + "' 追加\n", f["encoding"], f["eol"])["ok"])
        self.assertEqual(p.read_bytes(), "' 日本語\r\nSub A()\r\nEnd Sub\r\n' 追加\r\n".encode("cp932"))
        self.assertFalse(self.call("put_file", "sjis.bas", "絵文字😀", "cp932", "\r\n")["ok"])
        files = self.call("list_files")
        self.assertEqual(files[0], ".copilot-harness/config.yaml")
        self.assertIn("calc.py", files)
        self.assertNotIn(".copilot-harness/state.json", files)


class UnittestSummaryTest(unittest.TestCase):
    def test_summary_line_is_not_a_test(self):
        out = "test_a (t.T.test_a) ... FAIL\n\n" + "=" * 70 + "\nFAIL: test_a (t.T.test_a)\n" + "-" * 70 + \
              "\nAssertionError: 1 != 2\n\n" + "-" * 70 + "\nRan 1 test in 0.001s\n\nFAILED (failures=1)\n"
        oc = parse_exitcode_output(out, 1, [])
        self.assertEqual(list(oc.tests), ["t.T.test_a"])


if __name__ == "__main__":
    unittest.main()
