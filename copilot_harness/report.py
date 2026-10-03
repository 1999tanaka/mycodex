"""handoff ファイル (STATE.md / TEST_RESULT.md / NEXT_PROMPT.txt) の生成。"""

from __future__ import annotations

from .config import Config
from .state import DONE, HUMAN_REVIEW_REQUIRED, State
from .util import one_line, read_text


def _bullets(items: list[str], empty: str = "- (なし)") -> list[str]:
    return [f"- {x}" for x in items] if items else [empty]


def _numbered(items: list[str], empty: str = "(なし)") -> list[str]:
    return [f"{i}. {x}" for i, x in enumerate(items, 1)] if items else [empty]


def tests_lines(state: State) -> list[str]:
    tests = state["tests"]
    if not tests:
        return ["(未実施)"]
    out = []
    for name, t in tests.items():
        detail = f" ({t['detail']})" if t.get("detail") and t["status"] != "PASS" else ""
        out.append(f"{name}: {t['status']}{detail}")
    return out


def render_state_md(cfg: Config, state: State, changed: list[str]) -> str:
    max_iter = cfg.get("limits.max_iterations", 5)
    res_note = ""
    if state["results_run_id"] and state["results_run_id"] != state["run_id"]:
        res_note = f" (RUN_ID {state['results_run_id']} の結果)"
    lp = state["last_patch"]
    lines = [
        "# RUN", "", f"RUN_ID: {state['run_id']}", "",
        "# TASK", "", state["task"] or "(未設定)", "",
        "# STATUS", "",
        f"PHASE: {state['phase']}",
        f"ITERATION: {state['iteration']} / {max_iter}",
    ]
    if state["phase_reason"]:
        lines.append(f"REASON: {state['phase_reason']}")
    lines += ["", "# BUILD", "", f"{state['build']}{res_note}", ""]
    if _show_flash(cfg, state):
        lines += ["# FLASH", "", state["flash"], ""]
    lines += [
        f"# {cfg.profile.test_label}", "", *tests_lines(state), "",
        "# VERIFIED", "", *_bullets(state["verified"]), "",
        "# CURRENT FINDING", "", state["finding"] or "(なし)", "",
        "# CHANGED FILES", "", *_bullets(changed), "",
        "# CURRENT SUSPECTS", "", *_numbered(state["suspects"]), "",
        "# CONSTRAINTS", "", *_bullets(state["constraints"]), "",
        "# NEXT OBJECTIVE", "", state["next_objective"] or _default_objective(state), "",
    ]
    if lp:
        lines += [
            "# LAST PATCH", "",
            f"RUN_ID: {lp.get('run_id')} / FILES: {', '.join(lp.get('files', []))} "
            f"(+{lp.get('added', 0)} -{lp.get('deleted', 0)}) / VALIDATION: {lp.get('validation')}",
            f"SUMMARY: {one_line(lp.get('summary', ''), 300) or '(なし)'}", "",
        ]
    if state["missing_files"] or state["last_response_note"]:
        lines += ["# CONTEXT NOTES", ""]
        if state["missing_files"]:
            lines.append("以下の要求ファイルは存在しないか提供できません。存在を前提にしないでください:")
            lines += _bullets(state["missing_files"])
        if state["last_response_note"]:
            lines.append(state["last_response_note"])
        lines.append("")
    hist = state["history"][-int(cfg.get("handoff.history_entries", 10)):]
    if hist:
        lines += ["# HISTORY (過去の試行。同じ修正を繰り返さないこと)", ""]
        for h in hist:
            lines.append("- " + _history_line(h))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _show_flash(cfg: Config, state: State) -> bool:
    return bool(cfg.get("flash.command")) or state["flash"] not in ("NOT_RUN", "SKIPPED")


def _default_objective(state: State) -> str:
    if state["phase"] == DONE:
        return "完了 (人によるレビュー待ち)"
    if state["build"] == "FAIL":
        return "build error を解消する。"
    if state["failures"]:
        return f"{state['failures'][0]} の原因を特定し修正する。"
    return state["task"] or "(未設定)"


def _history_line(h: dict) -> str:
    parts = [h.get("run_id", "?"), h.get("action", "")]
    if h.get("files"):
        parts.append(",".join(h["files"]))
    if h.get("summary"):
        parts.append(f"「{one_line(h['summary'], 100)}」")
    res = [f"{k.upper()}={h[k]}" for k in ("build", "flash", "tests") if h.get(k)]
    if res:
        parts.append("→ " + " ".join(res))
    if h.get("note"):
        parts.append(f"({one_line(h['note'], 100)})")
    return " ".join(p for p in parts if p)


def render_test_result_md(cfg: Config, state: State, build_log: str, flash_log: str, uart_log: str) -> str:
    lines = ["# RUN_ID", "", state["results_run_id"] or state["run_id"], "", "# BUILD", "", state["build"], ""]
    if _show_flash(cfg, state):
        lines += ["# FLASH", "", state["flash"], ""]
    if cfg.profile.name == "mcu" or state["boot"] not in ("NOT_RUN",):
        lines += ["# BOOT", "", state["boot"], ""]
    lines += [
        "# TESTS", "", *tests_lines(state), "",
        "# FAILURE", "", *(state["failures"] or ["(なし)"]), "",
    ]
    if state["build"] == "FAIL" and build_log:
        lines += ["# BUILD LOG (抜粋)", "", "```text", build_log, "```", ""]
    if state["flash"] == "FAIL" and flash_log:
        lines += ["# FLASH LOG (抜粋)", "", "```text", flash_log, "```", ""]
    if uart_log:
        lines += [f"# {cfg.profile.log_label} (抜粋)", "", "```text", uart_log, "```", ""]
    return "\n".join(lines).rstrip() + "\n"


RESPONSE_FORMAT = """\
回答は必ず次のどちらかの形式にしてください (見出し語は英字のまま)。

情報不足の場合:
ACTION: NEED_CONTEXT
RUN_ID: {run_id}
FILES:
- <必要なファイルのパス>
REASON:
<必要な理由{lang_note}>

修正可能な場合:
ACTION: PATCH
RUN_ID: {run_id}
FILES:
- <変更するファイルのパス>
BEGIN_PATCH
--- a/<path>
+++ b/<path>
@@ -<行>,<行数> +<行>,<行数> @@
 <unified diff>
END_PATCH
SUMMARY:
<変更内容の要約{lang_note}>
FINDING:
<現時点で判明した原因{lang_note} (任意)>
SUSPECTS:
- <残っている疑い{lang_note} (任意)>

ルール:
- diff のパスは SOURCE_CONTEXT.md の `# FILE:` に書かれたパスをそのまま使う (.ino も元のファイル名のまま)
- BEGIN_PATCH と END_PATCH の間には unified diff 以外を書かない
- コンテキスト行は SOURCE_CONTEXT.md の内容と一字一句一致させる
- 変更は最小限にする。shell コマンドや手順の実行指示は書かない (Harness は実行しない)
{lang_rule}"""


def render_next_prompt(cfg: Config, state: State) -> str:
    run_id = state["run_id"]
    lines = [
        "現在RUN_ID:", run_id, "",
        "添付された", "", "STATE.md", "SOURCE_CONTEXT.md", "TEST_RESULT.md", "",
        "を使用してください。", "",
        "STATE.mdを現在状態の唯一の正として扱ってください。", "",
        "既にVERIFIEDに記載されている事項を", "再確認するよう提案しないでください。", "",
        "HISTORY に記載された過去の修正と同じ修正を繰り返さないでください。", "",
        "情報不足の場合:", "ACTION: NEED_CONTEXT", "",
        "修正可能な場合:", "ACTION: PATCH", "",
        "として回答してください。", "",
    ]
    lang = str(cfg.get("handoff.response_language") or "").strip()
    if lang:
        lines += [f"説明文 (REASON / SUMMARY / FINDING / SUSPECTS) は必ず{lang}で書いてください。", ""]
    if state["phase"] == DONE:
        lines = [f"RUN_ID: {run_id}", "", "このタスクは Harness 判定で完了しています。Copilot への送信は不要です。", ""]
        return "\n".join(lines)
    if state["phase"] == HUMAN_REVIEW_REQUIRED:
        lines = [f"[HUMAN_REVIEW_REQUIRED] {state['phase_reason']}", "人のレビュー後に送信してください。", ""] + lines
    if cfg.get("handoff.use_notebook"):
        lines += ["プロジェクトの静的情報 (PROJECT / ARCHITECTURE / HARDWARE / CODING_RULES / AGENT_RULES) は Notebook を参照してください。", ""]
    else:
        rules = cfg.static_dir / "AGENT_RULES.md"
        if rules.exists():
            text = read_text(rules).replace("<!-- harness:template -->", "").strip()
            if text:
                lines += ["---", "[AGENT_RULES]", "", text, "---", ""]
    lines.append(RESPONSE_FORMAT.format(
        run_id=run_id,
        lang_note=f" ({lang})" if lang else "",
        lang_rule=(f"- REASON / SUMMARY / FINDING / SUSPECTS / NEXT_OBJECTIVE の文章は{lang}で書く"
                   " (見出し語・ファイルパス・コード・diff はそのまま)\n") if lang else "",
    ))
    return "\n".join(lines)
