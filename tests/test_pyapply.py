"""内蔵 patch エンジン (git apply 相当) のテスト。"""

import shutil
import subprocess
import unittest

from helpers import TempProject

from copilot_harness import pyapply
from copilot_harness.patch import parse_patch, render_patch, resolve_hunks, validate_patch

SRC = "".join(f"line{i}\n" for i in range(1, 11))


class PyApplyTest(TempProject):
    def run_patch(self, text: str) -> pyapply.ApplyResult:
        pp = parse_patch(text)
        self.assertFalse(pp.errors, pp.errors)
        resolve_hunks(pp, self.root)
        v = validate_patch(pp, self.cfg)
        self.assertTrue(v.ok, v.errors)
        res = pyapply.check(pp, self.root)
        if res.ok:
            pyapply.write(res, self.root)
        return res

    def read(self, rel: str) -> bytes:
        return (self.root / rel).read_bytes()

    def test_modify(self):
        self.write("src/a.c", SRC)
        res = self.run_patch("--- a/src/a.c\n+++ b/src/a.c\n@@ -4,3 +4,3 @@\n line4\n-line5\n+LINE5\n line6\n")
        self.assertTrue(res.ok, res.messages)
        self.assertEqual(self.read("src/a.c"), SRC.replace("line5\n", "LINE5\n").encode())

    def test_offset_like_git(self):
        self.write("src/a.c", "new0\nnew1\n" + SRC)  # 記載行より 2 行下にずれている
        res = self.run_patch("--- a/src/a.c\n+++ b/src/a.c\n@@ -4,3 +4,4 @@\n line4\n line5\n+added\n line6\n")
        self.assertTrue(res.ok, res.messages)
        self.assertIn(b"line5\nadded\nline6\n", self.read("src/a.c"))

    def test_multiple_hunks_and_pure_insertion(self):
        self.write("src/a.c", SRC)
        res = self.run_patch(
            "--- a/src/a.c\n+++ b/src/a.c\n@@ -0,0 +1 @@\n+// header\n"
            "@@ -9,2 +10,2 @@\n line9\n-line10\n+LINE10\n"
        )
        self.assertTrue(res.ok, res.messages)
        data = self.read("src/a.c").decode()
        self.assertTrue(data.startswith("// header\nline1\n"))
        self.assertTrue(data.endswith("line9\nLINE10\n"))

    def test_context_mismatch(self):
        self.write("src/a.c", SRC)
        res = self.run_patch("--- a/src/a.c\n+++ b/src/a.c\n@@ -4,2 +4,2 @@\n lineX\n-line5\n+L5\n")
        self.assertFalse(res.ok)
        self.assertEqual(self.read("src/a.c"), SRC.encode())

    def test_crlf_preserved(self):
        self.write("src/a.c", SRC, newline="\r\n")
        res = self.run_patch("--- a/src/a.c\n+++ b/src/a.c\n@@ -1,2 +1,3 @@\n line1\n+inserted\n line2\n")
        self.assertTrue(res.ok, res.messages)
        self.assertTrue(self.read("src/a.c").startswith(b"line1\r\ninserted\r\nline2\r\n"))
        self.assertNotIn(b"\n", self.read("src/a.c").replace(b"\r\n", b""))

    def test_cp932_and_bom_preserved(self):
        (self.root / "src").mkdir(exist_ok=True)
        (self.root / "src/sj.c").write_bytes("// 日本語コメント\nint a;\n".encode("cp932"))
        res = self.run_patch("--- a/src/sj.c\n+++ b/src/sj.c\n@@ -1,2 +1,3 @@\n // 日本語コメント\n+// 追加\n int a;\n")
        self.assertTrue(res.ok, res.messages)
        self.assertEqual(self.read("src/sj.c"), "// 日本語コメント\n// 追加\nint a;\n".encode("cp932"))

        (self.root / "src/bom.c").write_bytes(b"\xef\xbb\xbfint a;\n")
        res = self.run_patch("--- a/src/bom.c\n+++ b/src/bom.c\n@@ -1 +1,2 @@\n int a;\n+int b;\n")
        self.assertTrue(res.ok, res.messages)
        self.assertEqual(self.read("src/bom.c"), b"\xef\xbb\xbfint a;\nint b;\n")

    def test_no_newline_at_eof(self):
        self.write("src/a.c", "a\nb")
        res = self.run_patch("--- a/src/a.c\n+++ b/src/a.c\n@@ -1,2 +1,3 @@\n a\n b\n\\ No newline at end of file\n+c\n")
        self.assertTrue(res.ok, res.messages)
        self.assertEqual(self.read("src/a.c"), b"a\nb\nc\n")

        self.write("src/b.c", "x\ny\n")
        res = self.run_patch("--- a/src/b.c\n+++ b/src/b.c\n@@ -2 +2 @@\n-y\n+z\n\\ No newline at end of file\n")
        self.assertTrue(res.ok, res.messages)
        self.assertEqual(self.read("src/b.c"), b"x\nz")

    def test_new_and_delete_file(self):
        res = self.run_patch("--- /dev/null\n+++ b/src/new.c\n@@ -0,0 +1,2 @@\n+int a;\n+int b;\n")
        self.assertTrue(res.ok, res.messages)
        self.assertEqual(self.read("src/new.c"), b"int a;\nint b;\n")
        res = self.run_patch("--- a/src/new.c\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-int a;\n-int b;\n")
        self.assertTrue(res.ok, res.messages)
        self.assertFalse((self.root / "src/new.c").exists())

    def test_delete_requires_full_match(self):
        self.write("src/a.c", "int a;\nint b;\n")
        res = self.run_patch("--- a/src/a.c\n+++ /dev/null\n@@ -1 +0,0 @@\n-int a;\n")
        self.assertFalse(res.ok)
        self.assertTrue((self.root / "src/a.c").exists())

    def test_failure_is_atomic(self):
        self.write("src/a.c", SRC)
        self.write("src/b.c", SRC)
        res = self.run_patch(
            "--- a/src/a.c\n+++ b/src/a.c\n@@ -1 +1 @@\n-line1\n+L1\n"
            "--- a/src/b.c\n+++ b/src/b.c\n@@ -1 +1 @@\n-nomatch\n+L1\n"
        )
        self.assertFalse(res.ok)
        self.assertEqual(self.read("src/a.c"), SRC.encode())  # 1 つでも失敗すれば何も書かない

    @unittest.skipUnless(shutil.which("git"), "git が無い環境")
    def test_same_result_as_git_apply(self):
        patch = (
            "--- a/src/a.c\n+++ b/src/a.c\n@@ -2,3 +2,4 @@\n line2\n-line3\n+LINE3\n+extra\n line4\n"
            "@@ -8,3 +9,2 @@\n line8\n-line9\n line10\n"
        )
        for newline in ("\n", "\r\n"):
            with self.subTest(newline=newline):
                self.write("src/a.c", SRC, newline=newline)
                self.write("src/g.c", SRC, newline=newline)
                self.assertTrue(self.run_patch(patch).ok)
                pp = parse_patch(patch.replace("src/a.c", "src/g.c"))
                (self.root / "g.diff").write_bytes(render_patch(pp, self.root).encode())
                # 利用者の git 設定 (core.autocrlf) の影響を除いて比較する
                subprocess.run(["git", "-c", "core.autocrlf=false", "apply", "g.diff"], cwd=self.root, check=True)
                self.assertEqual(self.read("src/a.c"), self.read("src/g.c"))


if __name__ == "__main__":
    unittest.main()
