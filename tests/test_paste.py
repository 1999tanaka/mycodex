"""paste コマンドと回答言語指定のテスト。クリップボードはモックする。"""

import unittest
from unittest import mock

from helpers import DEMO, TempProject

from copilot_harness import state as S
from copilot_harness.cli import main
from copilot_harness.workflow import Harness

FIX = (DEMO / "sample_responses" / "02_patch.txt").read_text(encoding="utf-8")
NEED = (DEMO / "sample_responses" / "01_need_context.txt").read_text(encoding="utf-8")


class PasteTest(TempProject):
    def setUp(self):
        super().setUp()
        self.copy_demo()
        self.git_init()
        Harness(self.cfg).start("CAN RX timeoutを修正する", ["CAN", "RX"], [])
        self.run_id = Harness(self.cfg).state["run_id"]

    def paste(self, clip: str, *args: str) -> int:
        with mock.patch("copilot_harness.clipboard.read_clipboard", return_value=clip):
            return main(["--harness-dir", str(self.hdir), "paste", *args])

    def inbox(self) -> str:
        p = self.hdir / "inbox" / "copilot_response.txt"
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def test_paste_patch_runs_to_done(self):
        clip = FIX.replace("{RUN_ID}", self.run_id).replace("\n", "\r\n")  # Windows のクリップボードは CRLF
        self.assertEqual(self.paste(clip), 0)
        self.assertNotIn("\r", self.inbox())
        st = Harness(self.cfg).state
        self.assertEqual(st["phase"], S.DONE)
        self.assertIn("can_hw_enable_rx_interrupt();", (self.root / "src" / "can.cpp").read_text(encoding="utf-8"))

    def test_paste_need_context(self):
        self.assertEqual(self.paste(NEED.replace("{RUN_ID}", self.run_id)), 0)
        self.assertIn("src/nvic.cpp", Harness(self.cfg).state["requested_files"])

    def test_save_only_and_no_run(self):
        self.assertEqual(self.paste(FIX.replace("{RUN_ID}", self.run_id), "--save-only"), 0)
        self.assertIn("ACTION: PATCH", self.inbox())
        self.assertEqual(Harness(self.cfg).state["iteration"], 0)  # まだ apply していない
        self.assertEqual(self.paste(FIX.replace("{RUN_ID}", self.run_id), "--no-run"), 0)
        st = Harness(self.cfg).state
        self.assertEqual((st["phase"], st["build"]), (S.PATCH_APPLIED, "NOT_RUN"))

    def test_rejects_prompt_empty_and_non_response(self):
        prompt = (self.hdir / "handoff" / "NEXT_PROMPT.txt").read_text(encoding="utf-8")
        for clip in (prompt, "   ", "こんにちは。今日はいい天気ですね。"):
            with self.subTest(clip=clip[:20]):
                self.assertEqual(self.paste(clip), 1)
                self.assertEqual(self.inbox(), "")
        self.assertEqual(self.paste("メモ", "--force", "--save-only"), 0)
        self.assertEqual(self.inbox(), "メモ")

    def test_unprocessed_previous_response_is_kept(self):
        (self.hdir / "inbox" / "copilot_response.txt").write_text("ACTION: PATCH\n(古い回答)\n", encoding="utf-8")
        self.paste(NEED.replace("{RUN_ID}", self.run_id), "--save-only")
        prev = (self.hdir / "inbox" / "copilot_response.prev.txt").read_text(encoding="utf-8")
        self.assertIn("(古い回答)", prev)

    def test_wrong_run_id_reported(self):
        self.assertEqual(self.paste(FIX.replace("{RUN_ID}", "19990101-0001")), 1)
        self.assertEqual(Harness(self.cfg).state["iteration"], 0)


class ResponseLanguageTest(TempProject):
    def prompt(self) -> str:
        self.write("src/main.c", "int main(void) { return 0; }\n")
        Harness(self.cfg).start("task", ["main"], [])
        return (self.hdir / "handoff" / "NEXT_PROMPT.txt").read_text(encoding="utf-8")

    def test_japanese_by_default(self):
        text = self.prompt()
        self.assertIn("説明文 (REASON / SUMMARY / FINDING / SUSPECTS) は必ず日本語で書いてください。", text)
        self.assertIn("<変更内容の要約 (日本語)>", text)
        self.assertIn("NEXT_OBJECTIVE の文章は日本語で書く", text)

    def test_configurable_and_disable(self):
        self.write_config("project:\n  root: ..\n  languages: [c]\nhandoff:\n  response_language: English\n")
        self.assertIn("必ずEnglishで書いてください", self.prompt())
        self.write_config("project:\n  root: ..\n  languages: [c]\nhandoff:\n  response_language: \"\"\n")
        text = self.prompt()
        self.assertNotIn("で書いてください。", text)
        self.assertIn("<変更内容の要約>", text)


if __name__ == "__main__":
    unittest.main()
