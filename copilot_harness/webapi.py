"""ブラウザ版 UI (web/) から呼び出す API。Pyodide (ブラウザ内の Python) 上で動作する。

プロジェクトのファイルは JavaScript が /project に読み込み、処理後の変更をローカルのフォルダへ書き戻す。
"""

from __future__ import annotations

import contextlib
import io
import json
import traceback
from pathlib import Path

from . import cli, profiles, pyapply
from . import state as S
from .config import Config
from .util import HarnessError

PROJECT = Path("/project")
HDIR = PROJECT / ".copilot-harness"
HANDOFF_FILES = ("STATE.md", "SOURCE_CONTEXT.md", "TEST_RESULT.md", "NEXT_PROMPT.txt")
EDIT_SKIP_DIRS = {".git", "node_modules", "__pycache__", "history", "logs", "backup", "inbox", "handoff", "copilot_harness"}
MAX_EDIT_BYTES = 512 * 1024


def configure(project: str) -> None:
    """プロジェクトの場所を変更する (テスト用)。"""
    global PROJECT, HDIR
    PROJECT = Path(project)
    HDIR = PROJECT / ".copilot-harness"


def _capture(fn, *args, **kwargs) -> str:
    buf = io.StringIO()
    code, error = 0, ""
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        try:
            r = fn(*args, **kwargs)
            code = r if isinstance(r, int) and not isinstance(r, bool) else (0 if r is not False else 1)
        except HarnessError as e:
            code, error = 1, str(e)
        except SystemExit as e:
            code = int(e.code or 0) if isinstance(e.code, int) else 1
        except Exception:
            code, error = 1, traceback.format_exc()
    return json.dumps({"code": code, "output": buf.getvalue(), "error": error}, ensure_ascii=False)


def _harness():
    from .workflow import Harness
    return Harness(Config.load(HDIR))


# ---------------------------------------------------------------- コマンド
def run_cli(args_json: str) -> str:
    """CLI と同じコマンドを実行する。例: ["start", "タスク", "-k", "CAN"]"""
    args = json.loads(args_json)
    return _capture(cli.main, ["--harness-dir", str(HDIR), *args])


def init(profile: str = "auto") -> str:
    return _capture(cli.main, ["init", "--project", str(PROJECT), "--profile", profile])


def paste_text(text: str, run: bool = True) -> str:
    """Copilot の回答 (クリップボードの内容) を保存して apply → (PATCH なら) run。"""
    def go():
        from .execute import do_run
        h = _harness()
        h.save_response(text)
        print()
        action = h.apply()
        if action == "PATCH" and run:
            return 0 if do_run(h) else 1
        return 0
    return _capture(go)


def manual_result(passed: bool, stage: str = "test", detail: str = "", log: str = "") -> str:
    def go():
        from .execute import do_manual_result
        return do_manual_result(_harness(), bool(passed), stage, detail, log)
    return _capture(go)


def rollback_with_reason(reason: str, constraint: str = "") -> str:
    """修正を元に戻し、理由を STATE に書き、測り直して handoff を作る。"""
    def go():
        from .execute import do_run
        h = _harness()
        h.rollback()
        h.note(finding=reason or None, constraints=[constraint] if constraint else [])
        h = _harness()
        do_run(h)
        return 0
    return _capture(go)


def request_more(objective: str) -> str:
    """完了後に追加の修正を頼む (note --objective → prepare)。"""
    def go():
        h = _harness()
        h.note(objective=objective)
        _harness().prepare()
        return 0
    return _capture(go)


# ---------------------------------------------------------------- 状態
def ui_state() -> str:
    out: dict = {"initialized": (HDIR / "config.yaml").exists()}
    if not out["initialized"]:
        name, reason = profiles.detect(PROJECT)
        out.update(guess=name, guess_reason=reason,
                   profiles=[{"name": p.name, "title": p.title} for p in profiles.PROFILES.values()])
        return json.dumps(out, ensure_ascii=False)
    from .execute import local_commands
    try:
        h = _harness()
    except HarnessError as e:
        out.update(config_error=str(e))
        return json.dumps(out, ensure_ascii=False)
    st, cfg = h.state, h.cfg
    out.update(
        profile=cfg.profile.name, profile_title=cfg.profile.title,
        task=st["task"], run_id=st["run_id"], phase=st["phase"] if st["task"] else "NO_TASK",
        phase_reason=st["phase_reason"], next_action=st.next_action(),
        iteration=st["iteration"], max_iterations=cfg.get("limits.max_iterations", 5),
        build=st["build"], flash=st["flash"], has_flash=bool(cfg.get("flash.command")),
        tests=[{"name": n, **t} for n, t in st["tests"].items()], failures=st["failures"],
        test_mode=cfg.get("test.mode"), local_commands=local_commands(h),
        keywords=st["keywords"], constraints=st["constraints"], verified=st["verified"],
        finding=st["finding"], suspects=st["suspects"],
        has_backup=any((p / "manifest.json").exists() for p in cfg.backup_dir.glob("*")),
        history=st["history"][-10:],
        handoff={name: _read(cfg.handoff_dir / name) for name in HANDOFF_FILES},
    )
    return json.dumps(out, ensure_ascii=False)


def _read(path: Path) -> str:
    try:
        return pyapply.decode(path.read_bytes())[0]
    except OSError:
        return ""


def handoff_bundle() -> str:
    """添付を使わず 1 回の貼り付けで送るための、プロンプト + 3 ファイルの結合テキスト。"""
    cfg = Config.load(HDIR)
    parts = [_read(cfg.handoff_dir / "NEXT_PROMPT.txt").rstrip(), ""]
    for name in HANDOFF_FILES[:3]:
        parts += [f"===== {name} =====", _read(cfg.handoff_dir / name).rstrip(), ""]
    return "\n".join(parts).replace("添付された", "以下に貼り付けた")


def diff_text() -> str:
    try:
        return _harness().diff_text()
    except HarnessError as e:
        return str(e)


# ---------------------------------------------------------------- ファイル編集
def list_files() -> str:
    files: list[str] = []
    if (HDIR / "config.yaml").exists():
        files.append(".copilot-harness/config.yaml")
        files += sorted(f".copilot-harness/static/{p.name}" for p in (HDIR / "static").glob("*.md"))
    for p in sorted(PROJECT.rglob("*")):
        rel = p.relative_to(PROJECT)
        if not p.is_file() or any(part in EDIT_SKIP_DIRS for part in rel.parts) or rel.parts[0] == ".copilot-harness":
            continue
        if p.stat().st_size > MAX_EDIT_BYTES or b"\0" in p.read_bytes()[:2048]:
            continue
        files.append(rel.as_posix())
    return json.dumps(files, ensure_ascii=False)


def _safe(rel: str) -> Path:
    p = (PROJECT / rel).resolve()
    if PROJECT.resolve() not in (p, *p.parents) or ".git" in Path(rel).parts:
        raise HarnessError(f"編集できないパスです: {rel}")
    return p


def get_file(rel: str) -> str:
    data = _safe(rel).read_bytes()
    text, enc = pyapply.decode(data)
    eol = "\r\n" if b"\r\n" in data else "\n"
    return json.dumps({"text": text.replace("\r\n", "\n"), "encoding": enc, "eol": eol}, ensure_ascii=False)


def put_file(rel: str, text: str, encoding: str = "utf-8", eol: str = "\n") -> str:
    p = _safe(rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        p.write_bytes(text.replace("\r\n", "\n").replace("\n", eol).encode(encoding))
    except UnicodeEncodeError as e:
        return json.dumps({"ok": False, "error": f"{encoding} で保存できない文字があります: {e.object[e.start:e.end]!r}"},
                          ensure_ascii=False)
    return json.dumps({"ok": True}, ensure_ascii=False)


PHASE_LABELS = {
    "NO_TASK": "タスク未開始", S.INIT: "タスク未開始", S.WAITING_COPILOT: "Copilot に相談する段階",
    S.PATCH_APPLIED: "修正を適用済み", S.BUILD_FAILED: "ビルド失敗", S.FLASH_FAILED: "書込み失敗",
    S.TEST_FAILED: "テスト失敗", S.WAITING_TEST: "テスト結果の入力待ち", S.DONE: "完了",
    S.HUMAN_REVIEW_REQUIRED: "人の確認が必要",
}
