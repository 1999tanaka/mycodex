import unittest

from helpers import REPO  # noqa: F401  (sys.path 設定)

from copilot_harness.response import NEED_CONTEXT, PATCH, parse_response

PATCH_RESPONSE = """\
原因は RX 割り込みが未許可であることです。

**ACTION:** PATCH
**RUN_ID:** 20261002-0017

FILES:
- `src/can.cpp`

BEGIN_PATCH
```diff
--- a/src/can.cpp
+++ b/src/can.cpp
@@ -1,2 +1,3 @@
 void can_init(void) {
+    enable_rx();
 }
```
END_PATCH

SUMMARY:
CAN RX interrupt enableを修正。

SUSPECTS:
1. NVIC configuration
2. callback registration
"""


class ResponseTest(unittest.TestCase):
    def test_patch_with_markdown_decorations(self):
        r = parse_response(PATCH_RESPONSE)
        self.assertTrue(r.ok, r.errors)
        self.assertEqual(r.action, PATCH)
        self.assertEqual(r.run_id, "20261002-0017")
        self.assertEqual(r.files, ["src/can.cpp"])
        self.assertTrue(r.patch.startswith("--- a/src/can.cpp\n"))
        self.assertNotIn("```", r.patch)
        self.assertIn("+    enable_rx();\n", r.patch)
        self.assertEqual(r.summary, "CAN RX interrupt enableを修正。")
        self.assertEqual(r.suspects, ["NVIC configuration", "callback registration"])

    def test_crlf_and_bom(self):
        r = parse_response("﻿" + PATCH_RESPONSE.replace("\n", "\r\n"))
        self.assertTrue(r.ok, r.errors)
        self.assertNotIn("\r", r.patch)

    def test_need_context(self):
        r = parse_response(
            "ACTION: NEED_CONTEXT\nRUN_ID: 20261002-0001\n\nFILES:\n- src/interrupt.cpp (登録処理)\n"
            "- include/interrupt.h\n\nREASON:\nCAN RX interrupt登録処理確認が必要。\n"
        )
        self.assertTrue(r.ok, r.errors)
        self.assertEqual(r.action, NEED_CONTEXT)
        self.assertEqual(r.files, ["src/interrupt.cpp", "include/interrupt.h"])
        self.assertEqual(r.reason, "CAN RX interrupt登録処理確認が必要。")

    def test_need_context_ignores_patch(self):
        r = parse_response("ACTION: NEED_CONTEXT\nFILES:\n- a.c\nBEGIN_PATCH\n--- a/a.c\n+++ b/a.c\nEND_PATCH\n")
        self.assertTrue(r.ok)
        self.assertEqual(r.patch, "")
        self.assertTrue(r.warnings)

    def test_truncated_patch(self):
        r = parse_response("ACTION: PATCH\nBEGIN_PATCH\n--- a/x.c\n+++ b/x.c\n@@ -1 +1 @@\n-a\n")
        self.assertFalse(r.ok)
        self.assertTrue(any("END_PATCH" in e for e in r.errors))

    def test_patch_outside_markers_is_ignored(self):
        r = parse_response("ACTION: PATCH\n```diff\n--- a/x.c\n+++ b/x.c\n```\n")
        self.assertFalse(r.ok)

    def test_conflicting_actions(self):
        r = parse_response("ACTION: PATCH\nACTION: NEED_CONTEXT\nFILES:\n- a.c\n")
        self.assertFalse(r.ok)

    def test_missing_action(self):
        self.assertFalse(parse_response("こんにちは").ok)
        self.assertFalse(parse_response("   ").ok)

    def test_multiple_patch_blocks_concatenated(self):
        r = parse_response(
            "ACTION: PATCH\nBEGIN_PATCH\n--- a/a.c\n+++ b/a.c\n@@ -1 +1 @@\n-a\n+b\nEND_PATCH\n"
            "BEGIN_PATCH\n--- a/b.c\n+++ b/b.c\n@@ -1 +1 @@\n-a\n+b\nEND_PATCH\n"
        )
        self.assertTrue(r.ok)
        self.assertEqual(r.patch_blocks, 2)
        self.assertIn("--- a/b.c", r.patch)


if __name__ == "__main__":
    unittest.main()
