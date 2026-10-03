"""Python / ローカルツール向け: 終了コード判定、構文チェック、依存解析、end-to-end。"""

import unittest

from helpers import TempProject

from copilot_harness import state as S
from copilot_harness.context import build_context, files_from_log
from copilot_harness.execute import do_run
from copilot_harness.repo import scan_repository
from copilot_harness.runner import parse_exitcode_output
from copilot_harness.state import State
from copilot_harness.templates import config_yaml
from copilot_harness.workflow import Harness

PYTEST_OUT = """\
============================= test session starts =============================
collected 3 items

tests/test_calc.py .F.                                                   [100%]

================================== FAILURES ===================================
______________________________ test_tax ______________________________
    def test_tax():
>       assert calc.tax(1000) == 1100
E       assert 1080 == 1100

tests/test_calc.py:7: AssertionError
=========================== short test summary info ===========================
FAILED tests/test_calc.py::test_tax - assert 1080 == 1100
========================= 1 failed, 2 passed in 0.05s =========================
"""

UNITTEST_OUT = """\
test_add (tests.test_calc.CalcTest.test_add) ... ok
test_tax (tests.test_calc.CalcTest.test_tax) ... FAIL

======================================================================
FAIL: test_tax (tests.test_calc.CalcTest.test_tax)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "C:\\proj\\tests\\test_calc.py", line 9, in test_tax
    self.assertEqual(calc.tax(1000), 1100)
AssertionError: 1080 != 1100

----------------------------------------------------------------------
Ran 2 tests in 0.001s

FAILED (failures=1)
"""


class ExitcodeParseTest(unittest.TestCase):
    def test_pytest(self):
        oc = parse_exitcode_output(PYTEST_OUT, 1, [])
        self.assertEqual(oc.status, "FAIL")
        self.assertEqual(oc.tests["tests/test_calc.py::test_tax"]["detail"], "assert 1080 == 1100")

    def test_unittest(self):
        oc = parse_exitcode_output(UNITTEST_OUT, 1, [])
        self.assertEqual(oc.tests["tests.test_calc.CalcTest.test_tax"]["detail"], "AssertionError: 1080 != 1100")

    def test_pass_and_unknown_failure(self):
        oc = parse_exitcode_output("Ran 5 tests in 0.1s\n\nOK\n", 0, [])
        self.assertEqual((oc.status, oc.tests["ALL"]["detail"]), ("PASS", "5 tests"))
        oc = parse_exitcode_output("something broke\n", 2, [])
        self.assertEqual(oc.tests["TEST_COMMAND"]["detail"], "exit code 2")

    def test_test_lines_still_supported(self):
        oc = parse_exitcode_output("TEST:A:PASS\nTEST:B:FAIL:x\n", 1, [])
        self.assertEqual(oc.failures, ["B: x"])


CALC_BUGGY = "def tax(value):\n    return int(value * 1.08)\n"
TEST_CALC = """\
import unittest

from calc import tax


class CalcTest(unittest.TestCase):
    def test_tax(self):
        self.assertEqual(tax(1000), 1100)


if __name__ == "__main__":
    unittest.main()
"""


class PythonProfileTest(TempProject):
    def setUp(self):
        super().setUp()
        self.write_config(config_yaml("python"))
        self.write("calc.py", CALC_BUGGY)
        self.write("tests/__init__.py", "")
        self.write("tests/test_calc.py", TEST_CALC)
        self.git_init()

    def respond(self, body: str) -> None:
        rid = Harness(self.cfg).state["run_id"]
        (self.hdir / "inbox" / "copilot_response.txt").write_text(
            f"ACTION: PATCH\nRUN_ID: {rid}\nBEGIN_PATCH\n{body}END_PATCH\nSUMMARY:\n税率を 10% に修正\n", encoding="utf-8")

    def test_cycle(self):
        Harness(self.cfg).start("消費税を 10% にする", ["tax"], [])
        self.assertFalse(do_run(Harness(self.cfg)))
        st = Harness(self.cfg).state
        self.assertEqual((st["build"], st["phase"]), ("PASS", S.TEST_FAILED))
        self.assertIn("tests.test_calc.CalcTest.test_tax", st["tests"])
        self.assertIn("tests/test_calc.py", st["test_failure_files"])  # トレースバックから抽出
        result = (self.hdir / "handoff" / "TEST_RESULT.md").read_text(encoding="utf-8")
        self.assertIn("# TEST LOG (抜粋)", result)
        self.assertNotIn("# FLASH", result)

        # 構文エラーの patch → build (builtin:python-syntax) で FAIL
        self.respond("--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n def tax(value):\n-    return int(value * 1.08)\n+    return int(value * 1.1\n")
        h = Harness(self.cfg)
        h.apply()
        self.assertFalse(do_run(h))
        st = Harness(self.cfg).state
        self.assertEqual(st["build"], "FAIL")
        self.assertIn("calc.py", st["build_error_files"])
        Harness(self.cfg).rollback()

        Harness(self.cfg).prepare()
        self.respond("--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n def tax(value):\n-    return int(value * 1.08)\n+    return round(value * 1.1)\n")
        h = Harness(self.cfg)
        h.apply()
        self.assertTrue(do_run(h))
        st = Harness(self.cfg).state
        self.assertEqual(st["phase"], S.DONE)
        self.assertIn("Test:", Harness(self.cfg).status_text())


class DependencyTest(TempProject):
    config_text = "project:\n  root: ..\n  languages: [python, powershell, javascript]\n  include_dirs: [src]\n"

    def test_python_powershell_js_deps(self):
        self.write("src/app/main.py", "from app.core import engine\nfrom . import util\nimport app.io as io\n")
        self.write("src/app/__init__.py", "")
        self.write("src/app/core/__init__.py", "")
        self.write("src/app/core/engine.py", "X = 1\n")
        self.write("src/app/util.py", "")
        self.write("src/app/io.py", "")
        self.write("scripts/run.ps1", ". $PSScriptRoot\\lib\\common.ps1\nImport-Module ./lib/log.psm1\n")
        self.write("scripts/lib/common.ps1", "")
        self.write("scripts/lib/log.psm1", "")
        self.write("web/index.js", "const a = require('./lib/a');\nimport b from '../shared/b.js';\n")
        self.write("web/lib/a.ts", "")
        self.write("shared/b.js", "")
        st = State.load(self.hdir / "state.json")
        st["run_id"] = "20261003-0001"
        st["requested_files"] = ["src/app/main.py", "scripts/run.ps1", "web/index.js"]
        ctx = build_context(self.cfg, st, scan_repository(self.cfg))
        reasons = {s.path: s.reason for s in ctx.selected}
        for dep in ("src/app/core/engine.py", "src/app/util.py", "src/app/io.py",
                    "scripts/lib/common.ps1", "scripts/lib/log.psm1", "web/lib/a.ts", "shared/b.js"):
            self.assertTrue(reasons.get(dep, "").startswith("P5"), (dep, reasons))
        self.assertIn("```powershell\n", ctx.text)

    def test_traceback_paths(self):
        self.write("src/tool.py", "")
        log = 'Traceback (most recent call last):\n  File "C:\\x\\src\\tool.py", line 3, in main\nValueError: bad\n'
        self.assertEqual(files_from_log(log, scan_repository(self.cfg), self.cfg), ["src/tool.py"])


if __name__ == "__main__":
    unittest.main()
