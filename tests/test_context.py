import unittest

from helpers import TempProject

from copilot_harness.context import build_context, files_from_log, render_file
from copilot_harness.repo import scan_repository
from copilot_harness.state import State


class ContextTest(TempProject):
    def setUp(self):
        super().setUp()
        self.write("main.ino", '#include "src/can.h"\nvoid setup() { can_init(); }\n')
        self.write("src/can.cpp", '#include "can.h"\n#include "util.h"\nvoid can_init() {}\n')
        self.write("src/can.h", "void can_init();\n")
        self.write("include/util.h", "int util();\n")
        self.write("src/util.c", "int util() { return 0; }\n")
        self.write("src/uart.cpp", "void uart_init() {}\n")
        self.write(".env", "TOKEN=abc\n")
        self.write("src/secret_keys.h", "#define KEY 1\n")
        self.write("certs/device.pem", "-----BEGIN-----\n")
        self.write("src/private_cfg.h", "#define X 1\n")

    def state(self, **kw) -> State:
        st = State.load(self.hdir / "state.json")
        st["run_id"] = "20261002-0001"
        st.data.update(kw)
        return st

    def test_secret_files_never_scanned(self):
        scan = scan_repository(self.cfg)
        for rel in (".env", "src/secret_keys.h", "certs/device.pem", "src/private_cfg.h"):
            self.assertNotIn(rel, scan.files)
            self.assertIn(rel, scan.excluded)

    def test_keyword_include_and_pair_selection(self):
        ctx = build_context(self.cfg, self.state(keywords=["CAN"]), scan_repository(self.cfg))
        reasons = {s.path: s.reason for s in ctx.selected}
        self.assertTrue(reasons["src/can.cpp"].startswith("P4"))
        self.assertTrue(reasons["main.ino"].startswith("P4"))
        self.assertTrue(reasons["include/util.h"].startswith("P5"))
        self.assertTrue(reasons["src/util.c"].startswith("P6") or reasons["src/util.c"].startswith("P5"), reasons)
        self.assertNotIn("src/uart.cpp", reasons)
        self.assertIn("# FILE: main.ino\n\n```cpp\n", ctx.text)
        self.assertNotIn("TOKEN=abc", ctx.text)

    def test_requested_files_first_and_missing_listed(self):
        st = self.state(requested_files=["src/uart.cpp"], missing_files=["src/nothing.c"])
        ctx = build_context(self.cfg, st, scan_repository(self.cfg))
        self.assertEqual(ctx.selected[0].path, "src/uart.cpp")
        self.assertIn("- src/nothing.c", ctx.text)

    def test_entry_point_fallback(self):
        ctx = build_context(self.cfg, self.state(), scan_repository(self.cfg))
        self.assertIn("main.ino", [s.path for s in ctx.selected])

    def test_budget(self):
        self.write_config("project:\n  root: ..\ncontext:\n  max_files: 1\n")
        ctx = build_context(self.cfg, self.state(keywords=["CAN"]), scan_repository(self.cfg))
        self.assertEqual(len(ctx.selected), 1)
        self.assertTrue(ctx.omitted)

    def test_fence_escalation(self):
        out = render_file("src/doc.c", "/* ```example``` */\n")
        self.assertIn("````c\n", out)

    def test_paths_from_compiler_logs(self):
        scan = scan_repository(self.cfg)
        log = (
            r"C:\Users\me\AppData\Local\Temp\arduino\sketches\AB12\sketch\main.ino.cpp:12:5: error: x" "\n"
            "src/can.cpp:3:1: error: expected ';'\n"
            r"D:\work\proj\include\util.h(4): error C2143" "\n"
        )
        self.assertEqual(files_from_log(log, scan, self.cfg), ["main.ino", "src/can.cpp", "include/util.h"])


if __name__ == "__main__":
    unittest.main()
