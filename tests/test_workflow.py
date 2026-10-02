"""デモプロジェクトを使った end-to-end テスト (仕様 53 受入条件)。"""

import os
import subprocess
import unittest
from unittest import mock

from helpers import DEMO, TempProject

from copilot_harness import state as S
from copilot_harness.execute import do_run
from copilot_harness.util import HarnessError
from copilot_harness.workflow import Harness

NEED = (DEMO / "sample_responses" / "01_need_context.txt").read_text(encoding="utf-8")
FIX = (DEMO / "sample_responses" / "02_patch.txt").read_text(encoding="utf-8")
NOOP = """ACTION: PATCH
RUN_ID: {RUN_ID}
FILES:
- src/sensor.cpp
BEGIN_PATCH
--- a/src/sensor.cpp
+++ b/src/sensor.cpp
@@ -1,2 +1,3 @@
+// harness test
 #include <Arduino.h>
 #include "sensor.h"
END_PATCH
SUMMARY:
無関係な変更
"""


class WorkflowTest(TempProject):
    def setUp(self):
        super().setUp()
        self.copy_demo()
        self.git_init()

    def harness(self) -> Harness:
        return Harness(self.cfg)  # CLI と同様、毎回 state.json から読み直す

    def respond(self, template: str, run_id: str | None = None) -> None:
        rid = run_id or self.harness().state["run_id"]
        (self.hdir / "inbox" / "copilot_response.txt").write_text(template.replace("{RUN_ID}", rid), encoding="utf-8")

    def start(self) -> Harness:
        h = self.harness()
        h.start("CAN RX timeoutを修正する", ["CAN", "RX"], ["public API変更禁止"])
        return h

    def test_full_cycle(self):
        self.start()
        for name in ("STATE.md", "SOURCE_CONTEXT.md", "TEST_RESULT.md", "NEXT_PROMPT.txt"):
            self.assertTrue((self.hdir / "handoff" / name).exists(), name)

        self.assertFalse(do_run(self.harness()))  # baseline: CAN_RX FAIL
        st = self.harness().state
        self.assertEqual(st["tests"]["CAN_RX"]["status"], "FAIL")
        result_md = (self.hdir / "handoff" / "TEST_RESULT.md").read_text(encoding="utf-8")
        self.assertIn("CAN_RX: FAIL (RX_TIMEOUT)", result_md)

        self.respond(NEED)
        self.assertEqual(self.harness().apply(), "NEED_CONTEXT")
        st = self.harness().state
        self.assertIn("src/nvic.cpp", st["requested_files"])
        self.assertIn("src/interrupt.cpp", st["missing_files"])
        ctx = (self.hdir / "handoff" / "SOURCE_CONTEXT.md").read_text(encoding="utf-8")
        self.assertIn("# FILE: src/nvic.cpp", ctx)

        before = st["run_id"]
        self.respond(FIX)
        h = self.harness()
        self.assertEqual(h.apply(), "PATCH")
        self.assertNotEqual(h.state["run_id"], before)
        self.assertTrue(do_run(h))
        st = self.harness().state
        self.assertEqual(st["phase"], S.DONE)
        self.assertEqual(st["iteration"], 1)
        self.assertIn("can_hw_enable_rx_interrupt();", (self.root / "src" / "can.cpp").read_text(encoding="utf-8"))
        self.assertTrue((self.hdir / "backup" / st["run_id"] / "manifest.json").exists())
        # Harness は commit しない
        count = subprocess.run(["git", "rev-list", "--count", "HEAD"], cwd=self.root, capture_output=True, text=True)
        self.assertEqual(count.stdout.strip(), "1")

    def test_run_id_mismatch_and_reprocess(self):
        self.start()
        self.respond(FIX, run_id="19990101-0001")
        with self.assertRaises(HarnessError) as cm:
            self.harness().apply()
        self.assertIn("RUN_ID 不一致", str(cm.exception))
        self.respond(FIX)
        self.harness().apply()
        with self.assertRaises(HarnessError) as cm:
            self.harness().apply()
        self.assertIn("処理済み", str(cm.exception))

    def test_git_apply_check_failure_goes_back_to_copilot(self):
        self.start()
        self.respond(FIX.replace("can_hw_enable_tx();", "can_hw_enable_tx_typo();"))
        with self.assertRaises(HarnessError) as cm:
            self.harness().apply()
        self.assertIn("git apply --check", str(cm.exception))
        state_md = (self.hdir / "handoff" / "STATE.md").read_text(encoding="utf-8")
        self.assertIn("git apply --check で失敗", state_md)
        self.assertEqual(self.harness().state["iteration"], 0)

    def test_rejected_path_is_not_applied(self):
        self.start()
        self.respond(NOOP.replace("src/sensor.cpp", "tools/hil_test.py"))
        with self.assertRaises(HarnessError):
            self.harness().apply()
        self.assertNotIn("harness test", (self.root / "tools" / "hil_test.py").read_text(encoding="utf-8"))

    def test_rollback(self):
        self.start()
        original = (self.root / "src" / "can.cpp").read_bytes()
        self.respond(FIX)
        self.harness().apply()
        self.assertNotEqual((self.root / "src" / "can.cpp").read_bytes(), original)
        self.harness().rollback()
        self.assertEqual((self.root / "src" / "can.cpp").read_bytes(), original)

    def test_max_iterations_stops(self):
        text = (self.hdir / "config.yaml").read_text(encoding="utf-8").replace("max_iterations: 5", "max_iterations: 1")
        self.write_config(text)
        self.start()
        self.respond(NOOP)
        h = self.harness()
        h.apply()
        self.assertFalse(do_run(h))
        self.assertEqual(self.harness().state["phase"], S.HUMAN_REVIEW_REQUIRED)
        self.respond(FIX)
        with self.assertRaises(HarnessError) as cm:
            self.harness().apply()
        self.assertIn("HUMAN_REVIEW_REQUIRED", str(cm.exception))
        self.harness().note(resume=True)
        self.assertEqual(self.harness().apply(reprocess=True), "PATCH")


class NoDependencyWorkflowTest(TempProject):
    """PyYAML も git も無い環境で 1 タスクを完了できること。"""

    def setUp(self):
        super().setUp()
        self.copy_demo()  # git init はしない
        self._patches = [
            mock.patch.dict(os.environ, {"HARNESS_NO_GIT": "1"}),
            mock.patch("copilot_harness.config.yaml", None),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        super().tearDown()

    def respond(self, template: str) -> None:
        rid = Harness(self.cfg).state["run_id"]
        (self.hdir / "inbox" / "copilot_response.txt").write_text(template.replace("{RUN_ID}", rid), encoding="utf-8")

    def test_full_cycle_without_pyyaml_and_git(self):
        from copilot_harness.config import yaml_backend
        self.assertIn("built-in", yaml_backend())
        h = Harness(self.cfg)
        self.assertEqual(h.patch_engine(), "python")
        h.start("CAN RX timeoutを修正する", ["CAN", "RX"], [])
        self.assertTrue((self.hdir / "baseline.json").exists())
        self.assertFalse(do_run(Harness(self.cfg)))

        self.respond(NEED)
        self.assertEqual(Harness(self.cfg).apply(), "NEED_CONTEXT")
        self.respond(FIX)
        h = Harness(self.cfg)
        self.assertEqual(h.apply(), "PATCH")
        self.assertTrue(do_run(h))
        self.assertEqual(Harness(self.cfg).state["phase"], S.DONE)

        # P1: スナップショットとの比較で変更ファイルを検出
        ctx = (self.hdir / "handoff" / "SOURCE_CONTEXT.md").read_text(encoding="utf-8")
        self.assertIn("src/can.cpp — P1 変更中 (タスク開始後に変更)", ctx)
        state_md = (self.hdir / "handoff" / "STATE.md").read_text(encoding="utf-8")
        self.assertIn("# CHANGED FILES\n\n- src/can.cpp\n", state_md)
        # git diff の代わりの review 用 diff
        diff = Harness(self.cfg).diff_text()
        self.assertIn("--- a/src/can.cpp", diff)
        self.assertIn("+    can_hw_enable_rx_interrupt();", diff)
        self.assertIn("YAML: built-in", Harness(self.cfg).status_text())

    def test_manual_edit_detected_by_snapshot(self):
        Harness(self.cfg).start("CAN RX", ["CAN"], [])
        p = self.root / "src" / "sensor.cpp"
        p.write_text(p.read_text(encoding="utf-8") + "// edited\n", encoding="utf-8")
        Harness(self.cfg).prepare()
        ctx = (self.hdir / "handoff" / "SOURCE_CONTEXT.md").read_text(encoding="utf-8")
        self.assertIn("src/sensor.cpp — P1 変更中 (タスク開始後に変更)", ctx)


class SubdirectoryProjectTest(TempProject):
    """project root が git repository のサブディレクトリでも git apply が効くこと。"""

    def test_apply_in_subdirectory(self):
        self.copy_demo()
        top = self.root.parent
        subprocess.run(["git", "init", "-q"], cwd=top, check=True)
        subprocess.run(["git", "config", "core.autocrlf", "false"], cwd=top, check=True)
        h = Harness(self.cfg)
        h.start("CAN RX", ["CAN"], [])
        rid = Harness(self.cfg).state["run_id"]
        (self.hdir / "inbox" / "copilot_response.txt").write_text(FIX.replace("{RUN_ID}", rid), encoding="utf-8")
        self.assertEqual(Harness(self.cfg).apply(), "PATCH")
        self.assertIn("can_hw_enable_rx_interrupt();", (self.root / "src" / "can.cpp").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
