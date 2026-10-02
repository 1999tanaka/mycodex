import sys
import unittest
from pathlib import Path

from helpers import REPO

from copilot_harness.runner import excerpt, parse_test_output, run_command


class TestProtocolTest(unittest.TestCase):
    def test_spec_example(self):
        out = "TEST:ADC:PASS:value=2015\nTEST:FLASH:PASS\nTEST:CAN_TX:PASS\nTEST:CAN_RX:FAIL:RX_TIMEOUT\n"
        oc = parse_test_output(out, [])
        self.assertEqual(oc.status, "FAIL")
        self.assertEqual(oc.boot, "PASS")
        self.assertEqual(oc.tests["ADC"], {"status": "PASS", "detail": "value=2015"})
        self.assertEqual(oc.failures, ["CAN_RX: RX_TIMEOUT"])

    def test_timestamped_lines_and_required(self):
        out = "[10:31:02] BOOT\n[10:31:02] TEST:UART:PASS\n[10:31:03] TEST:EXTRA:FAIL:x\n"
        oc = parse_test_output(out, ["UART"])
        self.assertEqual(oc.status, "PASS")
        self.assertTrue(oc.warnings)  # required 外の FAIL は警告のみ

    def test_missing_required(self):
        oc = parse_test_output("TEST:UART:PASS\n", ["UART", "CAN_RX"])
        self.assertEqual(oc.status, "FAIL")
        self.assertIn("MISSING", oc.tests["CAN_RX"]["detail"])

    def test_no_output(self):
        oc = parse_test_output("", [])
        self.assertEqual((oc.status, oc.boot), ("FAIL", "FAIL"))

    def test_excerpt_keeps_errors_and_tail(self):
        log = "\n".join([f"line {i}" for i in range(200)] + ["src/a.c:1:1: error: boom"] + [f"t{i}" for i in range(50)])
        ex = excerpt(log, max_lines=30)
        self.assertIn("error: boom", ex)
        self.assertIn("t49", ex)
        self.assertLessEqual(len(ex.splitlines()), 40)


class RunCommandTest(unittest.TestCase):
    def test_pass_and_fail(self):
        ok = run_command("build", [sys.executable, "-c", "print('ok')"], REPO, 30)
        self.assertEqual((ok.status, ok.output.strip()), ("PASS", "ok"))
        ng = run_command("build", [sys.executable, "-c", "import sys; sys.exit(3)"], REPO, 30)
        self.assertEqual((ng.status, ng.returncode), ("FAIL", 3))

    def test_string_command_uses_same_python(self):
        res = run_command("test", "python -c \"print('hi')\"", REPO, 30)
        self.assertEqual(res.status, "PASS", res.output + res.reason)

    def test_timeout(self):
        res = run_command("test", [sys.executable, "-c", "import time; time.sleep(30)"], REPO, 1)
        self.assertEqual(res.status, "FAIL")
        self.assertIn("TIMEOUT", res.reason)

    def test_not_found_and_unset(self):
        res = run_command("flash", ["definitely-not-a-command-xyz"], Path.cwd(), 5)
        self.assertEqual(res.status, "FAIL")
        self.assertEqual(run_command("flash", "", REPO, 5).status, "SKIPPED")


if __name__ == "__main__":
    unittest.main()
