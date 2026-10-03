"""build → flash → test → 結果判定 → handoff (仕様 36 自動実行シーケンス)。"""

from __future__ import annotations

import re
import time
from pathlib import Path

from . import state as S
from .builtin_cmds import is_builtin, run_builtin
from .context import files_from_log
from .repo import scan_repository
from .runner import (
    CommandResult, TestOutcome, capture_uart, excerpt, parse_exitcode_output, parse_test_output,
    run_command, write_log,
)
from .util import HarnessError, read_text, sha256_text, write_text
from .workflow import Harness, say

GENERIC_TOKENS = {"TEST", "TESTS", "FAIL", "PASS", "ERROR", "CHECK", "BASIC", "UNIT", "HW", "PY", "SELF", "COMMAND"}
PENDING = "PENDING"


def _cwd(h: Harness, stage: str) -> Path:
    return (h.root / str(h.cfg.get(f"{stage}.cwd", "."))).resolve()


def _env(h: Harness) -> dict[str, str]:
    return {"HARNESS_RUN_ID": h.state["run_id"], "HARNESS_PROJECT_ROOT": str(h.root)}


def _prepare_results(h: Harness) -> None:
    st = h.state
    st.ensure_run_id()
    if st["results_run_id"] != st["run_id"]:
        st.reset_results(st["run_id"])


def _log_path(h: Harness, name: str) -> Path:
    return h.cfg.logs_dir / h.state["run_id"] / name


def _stage(h: Harness, stage: str) -> CommandResult:
    cfg = h.cfg
    command = cfg.get(f"{stage}.command")
    say(f"[{stage}] {' '.join(str(command or '(未設定)').split())}")
    if is_builtin(command):
        res = run_builtin(command, h, stage)
    else:
        res = run_command(stage, command, _cwd(h, stage), float(cfg.get(f"{stage}.timeout", 120)), _env(h))
    if res.status != S.SKIPPED:
        write_log(_log_path(h, f"{stage}.log"), res)
    say(f"[{stage}] {res.status} {res.reason} ({res.duration:.1f}s)")
    return res


def build_ok(h: Harness) -> bool:
    st = h.state
    return st["build"] == S.PASS or (st["build"] == S.SKIPPED and not h.cfg.get("build.required", True))


def test_configured(cfg) -> bool:
    return bool(cfg.get("test.command")) or cfg.get("test.mode") in ("uart", "manual")


# ---------------------------------------------------------------- build
def do_build(h: Harness) -> bool:
    st = h.state
    _prepare_results(h)
    st.reset_results(st["run_id"])
    res = _stage(h, "build")
    if res.status == S.SKIPPED and h.cfg.get("build.required", True):
        res.status, res.reason = S.FAIL, "build.command が未設定です (config.yaml。不要なら build.required: false)"
        say(f"[build] {res.reason}")
    st["build"] = res.status
    if res.status == S.FAIL:
        st["build_excerpt"] = excerpt(res.output or res.reason, 60)
        st["build_error_files"] = files_from_log(res.output, scan_repository(h.cfg), h.cfg)
        st["failures"] = [f"BUILD: {res.reason}"]
        st.set_phase(S.BUILD_FAILED, res.reason)
    st.update_last_history(build=res.status)
    st.save()
    return build_ok(h)


# ---------------------------------------------------------------- flash
def do_flash(h: Harness, force: bool = False) -> bool:
    st = h.state
    _prepare_results(h)
    if not build_ok(h) and not force:
        raise HarnessError("flash には現在 RUN の BUILD = PASS が必要です (python harness.py build)。")
    st.data.update(flash=S.NOT_RUN, boot=S.NOT_RUN, tests={}, failures=[], flash_excerpt="", uart_excerpt="")
    if not h.cfg.get("flash.command"):
        st["flash"] = S.SKIPPED
        st.save()
        return True
    res = _stage(h, "flash")
    st["flash"] = res.status
    if res.status == S.FAIL:
        st["flash_excerpt"] = excerpt(res.output or res.reason, 30)
        st["failures"] = [f"FLASH: {res.reason}"]
        st.set_phase(S.FLASH_FAILED, res.reason)
    st.update_last_history(flash=res.status)
    st.save()
    return res.status in (S.PASS, S.SKIPPED)


# ---------------------------------------------------------------- test
def _failure_keywords(failures: list[str]) -> list[str]:
    out: list[str] = []
    for f in failures:
        name = f.split(": ", 1)[0]
        for tok in re.split(r"[_\-.\s/\\:]+", name.upper()):
            if len(tok) >= 2 and tok not in GENERIC_TOKENS and tok not in out:
                out.append(tok)
    return out[:8]


def record_outcome(h: Harness, oc: TestOutcome, output: str, extra_fail: str = "") -> bool:
    """テスト結果を State に記録する (自動テスト・手動入力 共通)。"""
    st, cfg = h.state, h.cfg
    if extra_fail and (oc.status == S.PASS or not oc.tests or "TIMEOUT" in extra_fail or "起動" in extra_fail):
        oc.failures.append(f"TEST_RUNNER: {extra_fail}")
        oc.status = S.FAIL
    st["boot"] = oc.boot
    st["tests"] = oc.tests
    st["failures"] = oc.failures
    st["uart_excerpt"] = excerpt(output or extra_fail, int(cfg.get("test.log_excerpt_lines", 40)))
    st["test_failure_files"] = files_from_log(output, scan_repository(cfg), cfg) if oc.failures else []
    st["test_failure_keywords"] = _failure_keywords(oc.failures)
    for w in oc.warnings:
        say(f"  warning: {w}")
    for name, t in oc.tests.items():
        say(f"  {name}: {t['status']}" + (f" ({t['detail']})" if t["detail"] else ""))
    passed = sum(1 for t in oc.tests.values() if t["status"] == "PASS")
    summary = "ALL PASS" if oc.status == S.PASS else f"{len(oc.failures)} FAIL / {passed} PASS"
    if oc.status == S.FAIL:
        st.set_phase(S.TEST_FAILED, "; ".join(oc.failures[:3]))
    st.update_last_history(tests=summary)
    st.save()
    say(f"[test] {oc.status} ({summary})")
    return oc.status == S.PASS


def do_test(h: Harness, force: bool = False):
    """戻り値: True=PASS / False=FAIL / PENDING=手動テスト待ち。"""
    st, cfg = h.state, h.cfg
    _prepare_results(h)
    if st["flash"] not in (S.PASS, S.SKIPPED) and not force:
        raise HarnessError("flash が PASS していないため test を開始しません。")
    mode = str(cfg.get("test.mode", "command"))
    required = [str(x) for x in cfg.list("test.required")]
    uart_cfg = cfg.get("test.uart", {}) or {}
    extra_fail = ""
    if mode == "manual":
        st["tests"], st["failures"] = {}, []
        st.set_phase(S.WAITING_TEST, "人によるテスト結果の入力待ち")
        st.save()
        say("[test] 手動テストモード: テストを実行し、結果を入力してください")
        say("  TEST 行をコピーして    python harness.py result --paste")
        say("  全部 OK なら           python harness.py result --pass")
        say('  NG なら                python harness.py result --fail "NG の内容"')
        return PENDING
    if mode == "uart":
        say(f"[test] UART {uart_cfg.get('port')} @ {uart_cfg.get('baudrate')}")
        output, err = capture_uart(uart_cfg)
        write_text(_log_path(h, "test.log"), output + (f"\n# harness: {err}\n" if err else ""))
        oc = parse_test_output(output, required, str(uart_cfg.get("boot_marker") or ""))
        return record_outcome(h, oc, output, err)
    res = _stage(h, "test")
    if res.status == S.SKIPPED:
        st["tests"], st["boot"] = {}, S.NOT_RUN
        st.update_last_history(tests="SKIPPED")
        st.save()
        say("[test] test.command 未設定のため SKIPPED")
        return True
    output = res.output
    if mode == "exitcode":
        if res.returncode is None:  # 起動失敗・タイムアウト
            extra_fail = res.reason
        oc = parse_exitcode_output(output, res.returncode, required)
    else:
        if res.status == S.FAIL:
            extra_fail = res.reason
        oc = parse_test_output(output, required, str(uart_cfg.get("boot_marker") or ""))
    return record_outcome(h, oc, output, extra_fail)


def do_result(h: Harness, text: str) -> bool:
    """手動テストの結果 (TEST 行) を取り込み、判定して handoff を作る。"""
    st = h.state
    if not st["task"]:
        raise HarnessError('タスクが未設定です: python harness.py start "<task>"')
    _prepare_results(h)
    write_text(_log_path(h, "test_manual.log"), text)
    oc = parse_test_output(text, [str(x) for x in h.cfg.list("test.required")])
    if not oc.tests:
        raise HarnessError("TEST:<名前>:<PASS|FAIL> 形式の行がありません。--pass / --fail も使えます。")
    oc.boot = S.NOT_RUN if h.cfg.profile.name != "mcu" else oc.boot
    record_outcome(h, oc, text)
    return finish(h)


# ---------------------------------------------------------------- 判定
def evaluate(h: Harness) -> bool:
    """成功判定 (仕様 44)。Copilot には判定させない。"""
    st, cfg = h.state, h.cfg
    lp = st["last_patch"]
    test_ok = (st["tests"] and not st["failures"]) or (not st["tests"] and not test_configured(cfg))
    ok = (
        build_ok(h)
        and st["flash"] in (S.PASS, S.SKIPPED)
        and bool(test_ok)
        and (lp is None or lp.get("validation") == "PASS")
    )
    if ok:
        st.set_phase(S.DONE, f"{completion_label(h)} PASS")
        st.update_last_history(note="SUCCESS")
        if not st["tests"]:
            say("  warning: テストが未設定のため、動作は未検証です (人が確認してください)。")
    else:
        max_iter = int(cfg.get("limits.max_iterations", 5))
        if st["iteration"] >= max_iter:
            st.set_phase(S.HUMAN_REVIEW_REQUIRED, f"{max_iter} 回の iteration で正常終了しませんでした")
    st.save()
    return ok


def completion_label(h: Harness) -> str:
    st = h.state
    parts = ["BUILD"] if st["build"] == S.PASS else []
    if st["flash"] == S.PASS:
        parts.append("FLASH")
    if st["tests"]:
        parts.append("REQUIRED_TESTS")
    return "/".join(parts) or "PATCH"


def finish(h: Harness) -> bool:
    success = evaluate(h)
    say("")
    h.generate_handoff()
    say("")
    if success:
        say("=" * 60)
        say(f" TASK COMPLETE: {completion_label(h).replace('/', ' / ')} = PASS")
        say(" python harness.py diff (または git diff) で変更をレビューし、")
        say(" 問題なければ人が commit してください。")
        say("=" * 60)
    else:
        h._print_next_steps()
    return success


def do_run(h: Harness) -> bool:
    st = h.state
    if not st["task"]:
        raise HarnessError('タスクが未設定です: python harness.py start "<task>"')
    # build 失敗 → flash しない / flash 失敗 → test しない
    if do_build(h) and do_flash(h):
        if do_test(h) == PENDING:
            return False
    return finish(h)


# ---------------------------------------------------------------- watch
def watch(h: Harness, interval: float = 1.0, auto_run: bool = True) -> None:
    """inbox/copilot_response.txt の更新を監視し apply (→ run) を自動実行する。"""
    path = h.cfg.response_path
    say(f"監視中: {path}  (Ctrl+C で終了)")
    last_seen = ""
    try:
        while True:
            time.sleep(interval)
            if not path.exists():
                continue
            try:
                m1 = path.stat().st_mtime
                time.sleep(0.5)  # 保存途中の読み込みを避ける
                if path.stat().st_mtime != m1:
                    continue
                text = read_text(path)
            except OSError:
                continue
            digest = sha256_text(text)
            if not text.strip() or digest == last_seen:
                continue
            # 別プロセス (paste / note など) の更新を反映するため毎回読み直す
            h.state = S.State.load(h.cfg.state_path)
            if digest in h.state["processed_responses"]:
                last_seen = digest
                continue
            last_seen = digest
            say(f"\n--- 新しい回答を検出 ({time.strftime('%H:%M:%S')}) ---")
            try:
                action = h.apply()
                if action == "PATCH" and auto_run:
                    do_run(h)
            except HarnessError as e:
                say(f"ERROR: {e}")
            if h.state["phase"] in (S.DONE, S.HUMAN_REVIEW_REQUIRED, S.WAITING_TEST):
                say(f"[{h.state['phase']}] 監視を終了します。")
                return
            say(f"\n監視中: {path}")
    except KeyboardInterrupt:
        say("\n監視を終了しました。")
