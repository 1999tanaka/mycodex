import unittest

from helpers import TempProject

from copilot_harness.patch import parse_patch, render_patch, resolve_hunks, validate_patch

CAN = "void can_init(void)\n{\n    set_bitrate();\n    enable_tx();\n}\n"


def diff(path: str, body: str, old: str | None = None) -> str:
    a = f"a/{old or path}" if old != "/dev/null" else "/dev/null"
    return f"--- {a}\n+++ b/{path}\n{body}"


class PatchValidationTest(TempProject):
    def setUp(self):
        super().setUp()
        self.write("src/can.cpp", CAN)
        self.write("main.ino", "void setup() {}\n")
        self.write("tools/hil_test.py", "print(1)\n")
        self.write(".env", "TOKEN=x\n")

    def check(self, text: str):
        pp = parse_patch(text)
        if not pp.errors:
            resolve_hunks(pp, self.root)
        return pp, validate_patch(pp, self.cfg)

    def test_valid_patch(self):
        pp, v = self.check(diff("src/can.cpp", "@@ -3,2 +3,3 @@\n     set_bitrate();\n     enable_tx();\n+    enable_rx();\n"))
        self.assertTrue(v.ok, v.errors)
        self.assertEqual((pp.added, pp.deleted), (1, 0))

    def test_ino_allowed(self):
        _, v = self.check(diff("main.ino", "@@ -1 +1 @@\n-void setup() {}\n+void setup() { init(); }\n"))
        self.assertTrue(v.ok, v.errors)

    def test_rejects_dangerous_paths(self):
        for path in ("../outside.c", "/etc/passwd", "C:/Windows/system.ini", ".git/config", ".env",
                     "src/../../x.c", "src/secret_key.h", "src/server.pem", "tools/hil_test.py",
                     ".copilot-harness/config.yaml", "src/con.c"):
            with self.subTest(path=path):
                _, v = self.check(diff(path, "@@ -1 +1 @@\n-a\n+b\n"))
                self.assertEqual(v.status, "REJECTED", path)

    def test_rejects_new_file_outside_allowed(self):
        _, v = self.check(diff("Makefile", "@@ -0,0 +1 @@\n+all:\n", old="/dev/null"))
        self.assertEqual(v.status, "REJECTED")

    def test_new_file_in_src(self):
        pp, v = self.check(diff("src/new.c", "@@ -0,0 +1,2 @@\n+int a;\n+int b;\n", old="/dev/null"))
        self.assertTrue(v.ok, v.errors)
        self.assertTrue(pp.files[0].is_new)

    def test_rejects_rename_binary_mode(self):
        for text in (
            "diff --git a/src/can.cpp b/src/x.cpp\nrename from src/can.cpp\nrename to src/x.cpp\n",
            "diff --git a/src/a.bin b/src/a.bin\nGIT binary patch\nliteral 0\n",
            "diff --git a/src/can.cpp b/src/can.cpp\nold mode 100644\nnew mode 100755\n",
        ):
            with self.subTest(text=text):
                _, v = self.check(text)
                self.assertEqual(v.status, "REJECTED")

    def test_syntax_errors(self):
        for text in ("hello world\n", "@@ -1 +1 @@\n-a\n+b\n", diff("src/can.cpp", "")):
            with self.subTest(text=text):
                _, v = self.check(text)
                self.assertEqual(v.status, "REJECTED")

    def test_limits_require_human_review(self):
        self.write_config("project:\n  root: ..\nlimits:\n  max_added_lines: 2\n  max_changed_files: 1\n")
        body = "@@ -5 +5,4 @@\n }\n+a\n+b\n+c\n"
        _, v = self.check(diff("src/can.cpp", body))
        self.assertEqual(v.status, "HUMAN_REVIEW_REQUIRED")
        self.assertTrue(any("MAX_ADDED_LINES" in x for x in v.limit_violations))

    def test_hunk_without_line_numbers_is_located(self):
        pp, v = self.check(diff("src/can.cpp", "@@ ... @@\n     enable_tx();\n+    enable_rx();\n }\n"))
        self.assertTrue(v.ok, v.errors)
        h = pp.files[0].hunks[0]
        self.assertEqual((h.old_start, h.old_count, h.new_count), (4, 2, 3))
        self.assertIn("@@ -4,2 +4,3 @@", render_patch(pp, self.root))

    def test_hunk_with_wrong_context_fails(self):
        pp, v = self.check(diff("src/can.cpp", "@@ @@\n     does_not_exist();\n+    x();\n"))
        self.assertEqual(v.status, "REJECTED")

    def test_trailing_whitespace_tolerated(self):
        self.write("src/ws.c", "int a;   \nint b;\n")
        pp, v = self.check(diff("src/ws.c", "@@ @@\n int a;\n+int c;\n int b;\n"))
        self.assertTrue(v.ok, v.errors)
        self.assertEqual(pp.files[0].hunks[0].lines[0], " int a;   ")

    def test_crlf_target_gets_crlf_patch(self):
        self.write("src/crlf.c", "int a;\nint b;\n", newline="\r\n")
        pp, v = self.check(diff("src/crlf.c", "@@ -1,2 +1,3 @@\n int a;\n+int c;\n int b;\n"))
        self.assertTrue(v.ok, v.errors)
        self.assertIn("+int c;\r\n", render_patch(pp, self.root))

    def test_blank_context_line_without_space(self):
        self.write("src/blank.c", "int a;\n\nint b;\n")
        pp, v = self.check(diff("src/blank.c", "@@ -1,3 +1,4 @@\n int a;\n\n+int c;\n int b;\n"))
        self.assertTrue(v.ok, v.errors)
        self.assertEqual(pp.files[0].hunks[0].old_count, 3)


if __name__ == "__main__":
    unittest.main()
