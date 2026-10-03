"""CLI (仕様 37)。"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from . import __version__
from .config import Config
from . import profiles
from .templates import HARNESS_GITIGNORE, config_yaml, static_files
from .util import HarnessError, write_text

CODE_DIR = Path(__file__).resolve().parent          # copilot_harness/
SCRIPT_DIR = CODE_DIR.parent                         # harness.py のあるディレクトリ
SUBDIRS = ("static", "handoff", "inbox", "logs", "history", "backup")


def _split(values: list[str] | None) -> list[str]:
    out: list[str] = []
    for v in values or []:
        out += [x.strip() for x in v.replace("、", ",").split(",") if x.strip()]
    return out


def find_harness_dir(arg: str | None) -> Path:
    if arg:
        return Path(arg).resolve()
    if (SCRIPT_DIR / "config.yaml").exists():
        return SCRIPT_DIR
    cur = Path.cwd().resolve()
    for d in (cur, *cur.parents):
        if d.name == ".copilot-harness" and (d / "config.yaml").exists():
            return d
        if (d / ".copilot-harness" / "config.yaml").exists():
            return d / ".copilot-harness"
    raise HarnessError(
        ".copilot-harness/config.yaml が見つかりません。\n"
        "  python harness.py init --project <project_dir> で初期化してください。"
    )


# ---------------------------------------------------------------- init
def cmd_init(args) -> int:
    if args.project:
        target = Path(args.project).resolve() / ".copilot-harness"
    elif SCRIPT_DIR.name == ".copilot-harness":
        target = SCRIPT_DIR
    else:
        target = Path.cwd().resolve() / ".copilot-harness"
    target.mkdir(parents=True, exist_ok=True)

    if target.resolve() != SCRIPT_DIR.resolve():
        shutil.copy2(SCRIPT_DIR / "harness.py", target / "harness.py")
        dst = target / "copilot_harness"
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(CODE_DIR, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        print(f"Harness 本体をコピーしました: {target / 'harness.py'}")

    for sub in SUBDIRS:
        (target / sub).mkdir(exist_ok=True)
    project_root = target.parent
    cfg_path = target / "config.yaml"
    if cfg_path.exists() and not args.force:
        try:
            profile = Config.load(target).get("project.profile", "mcu")
        except HarnessError:
            profile = "mcu"
        print(f"既存の config.yaml を使います (profile: {profile})。作り直す場合は --force")
    elif args.profile == "auto":
        profile, reason = profiles.detect(project_root)
        print(f"プロファイルを自動判定しました: {profile} ({profiles.get(profile).title})")
        print(f"  理由: {reason}")
        print("  違う場合は --profile mcu / python / vba / tool を付けて --force で作り直してください")
    else:
        profile = args.profile
        print(f"プロファイル: {profile} ({profiles.get(profile).title})")

    created = []
    files = {"config.yaml": config_yaml(profile, profiles.find_workbook(project_root)), ".gitignore": HARNESS_GITIGNORE}
    files.update({f"static/{k}": v for k, v in static_files(profile).items()})
    for rel, text in files.items():
        p = target / rel
        if not p.exists() or args.force:
            write_text(p, text)
            created.append(rel)
    resp = target / "inbox" / "copilot_response.txt"
    if not resp.exists():
        write_text(resp, "")
    for rel in created:
        print(f"  created: {rel}")
    print(f"\n初期化しました: {target}")
    print("次の手順:")
    print(f"  1. {cfg_path} を環境に合わせて編集 (build / test コマンドなど)")
    if profile == "vba":
        print("     Excel VBA: 対象ブック (vba.workbook) を確認し、python harness.py vba-export でモジュールを書き出す")
        print(f"     テスト用モジュールのひな形: {target / 'copilot_harness' / 'assets' / 'HarnessTests.bas'}")
        print("     (src/vba にコピーすると build でブックへ取り込まれます)")
    print(f"  2. {target / 'static'} の PROJECT.md 等を記入 (任意)")
    print(f"  3. cd {target}")
    print('     python harness.py start "タスク内容" -k キーワード')
    return 0


# ---------------------------------------------------------------- 各コマンド
def _harness(args):
    from .workflow import Harness
    return Harness(Config.load(find_harness_dir(args.harness_dir)))


def cmd_start(args) -> int:
    h = _harness(args)
    h.start(args.task, _split(args.keywords), args.constraint or [])
    return 0


def cmd_prepare(args) -> int:
    h = _harness(args)
    if args.task:
        h.start(args.task, _split(args.keywords), args.constraint or [])
        return 0
    if args.keywords:
        h.state["keywords"] = list(dict.fromkeys(h.state["keywords"] + _split(args.keywords)))
    h.prepare()
    return 0


def cmd_apply(args) -> int:
    from .execute import do_run
    h = _harness(args)
    action = h.apply(allow_large=args.allow_large, ignore_run_id=args.ignore_run_id, reprocess=args.reprocess)
    if action == "PATCH":
        if args.run:
            return 0 if do_run(h) else 1
        print("\n次: python harness.py run  (build → flash → test)")
    return 0


def cmd_paste(args) -> int:
    from .clipboard import ClipboardError, read_clipboard
    from .execute import do_run
    h = _harness(args)
    try:
        text = read_clipboard()
    except ClipboardError as e:
        raise HarnessError(str(e)) from e
    h.save_response(text, force=args.force)
    if args.save_only:
        print("\n次: python harness.py apply --run  (watch 実行中なら自動で処理されます)")
        return 0
    print()
    action = h.apply(allow_large=args.allow_large, ignore_run_id=args.ignore_run_id)
    if action == "PATCH":
        if args.no_run:
            print("\n次: python harness.py run  (build → flash → test)")
            return 0
        return 0 if do_run(h) else 1
    return 0


def cmd_build(args) -> int:
    from .execute import do_build
    h = _harness(args)
    ok = do_build(h)
    h.generate_handoff()
    return 0 if ok else 1


def cmd_flash(args) -> int:
    from .execute import do_flash
    h = _harness(args)
    ok = do_flash(h, force=args.force)
    h.generate_handoff()
    return 0 if ok else 1


def cmd_test(args) -> int:
    from .execute import PENDING, do_test, finish
    h = _harness(args)
    if do_test(h, force=args.force) == PENDING:
        return 0
    return 0 if finish(h) else 1


def cmd_result(args) -> int:
    """手動テストの結果を入力する (test.mode: manual)。"""
    from .execute import do_manual_result, do_result
    h = _harness(args)
    log = Path(args.log_file).read_text(encoding="utf-8-sig", errors="replace") if args.log_file else ""
    if args.passed is not None:
        return 0 if do_manual_result(h, True, "test", args.passed or "", log) else 1
    if args.failed:
        do_manual_result(h, False, args.stage, args.failed, log)
        return 1
    if args.file:
        text = Path(args.file).read_text(encoding="utf-8-sig")
    else:
        from .clipboard import ClipboardError, read_clipboard
        try:
            text = read_clipboard()
        except ClipboardError as e:
            raise HarnessError(str(e)) from e
    return 0 if do_result(h, text) else 1


def cmd_vba_export(args) -> int:
    """Excel ブックの VBA モジュールを src/vba へ書き出す。"""
    from .builtin_cmds import vba
    h = _harness(args)
    res = vba(h, "export", "vba-export")
    print(res.output.strip() or res.reason)
    if res.status != "PASS":
        raise HarnessError(f"VBA の書き出しに失敗しました: {res.reason}")
    return 0


def cmd_run(args) -> int:
    from .execute import do_run
    return 0 if do_run(_harness(args)) else 1


def cmd_next(args) -> int:
    _harness(args).next()
    return 0


def cmd_status(args) -> int:
    print(_harness(args).status_text())
    return 0


def cmd_diff(args) -> int:
    text = _harness(args).diff_text()
    print(text if text else "(このタスクで Harness が適用した変更はありません)")
    return 0


def cmd_ports(args) -> int:
    from .serialport import backend_name, list_ports
    ports = list_ports()
    if not ports:
        print("シリアルポートが見つかりません (マイコンの接続・USB ドライバを確認してください)")
    for name, desc in ports:
        print(f"{name:<10} {desc}")
    print(f"\nserial backend: {backend_name()}")
    return 0


def cmd_monitor(args) -> int:
    from datetime import datetime

    from .config import DEFAULT_CONFIG
    from .runner import uart_session
    try:
        cfg = Config.load(find_harness_dir(args.harness_dir))
        uart = dict(cfg.get("test.uart", {}) or {})
        log_dir = cfg.logs_dir / "monitor"
    except HarnessError:
        cfg, uart, log_dir = None, dict(DEFAULT_CONFIG["test"]["uart"]), Path.cwd()
    send = args.send.replace("\\r", "\r").replace("\\n", "\n") if args.send is not None else None
    for key, val in (("port", args.port), ("baudrate", args.baud), ("reset", args.reset), ("send", send)):
        if val is not None:
            uart[key] = val
    until = args.until or ""
    limit = "無制限" if not args.seconds else f"{args.seconds:g}s"
    print(f"UART monitor: {uart.get('port')} @ {uart.get('baudrate')}  (時間: {limit}"
          + (f", '{until}' で終了" if until else "") + ", Ctrl+C で終了)", flush=True)
    try:
        log, err = uart_session(uart, duration=args.seconds, until=until, echo=lambda s: print(s, flush=True))
    except KeyboardInterrupt:
        log, err = "", "中断されました"
    if log:
        path = log_dir / f"uart_{datetime.now():%Y%m%d-%H%M%S}.log"
        write_text(path, log + "\n")
        print(f"\nログを保存しました: {path}")
    if err:
        print(f"ERROR: {err}", file=sys.stderr)
        return 1
    return 0


def cmd_watch(args) -> int:
    from .execute import watch
    watch(_harness(args), interval=args.interval, auto_run=not args.no_run)
    return 0


def cmd_rollback(args) -> int:
    h = _harness(args)
    h.rollback(args.run_id)
    h.generate_handoff()
    return 0


def cmd_note(args) -> int:
    h = _harness(args)
    h.note(
        verified=args.verified or [], finding=args.finding, suspects=args.suspect or [],
        clear_suspects=args.clear_suspects, constraints=args.constraint or [], objective=args.objective,
        keywords=_split(args.keywords), resume=args.resume, reset_iterations=args.reset_iterations,
        clear_requested=args.clear_requested, task=args.task,
    )
    return 0


# ---------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="harness.py", description="Microsoft Copilot 向け Local Coding Harness v0.2")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--harness-dir", help=".copilot-harness ディレクトリ (省略時は自動検出)")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("init", help=".copilot-harness/ を初期化")
    s.add_argument("--project", help="対象プロジェクトのルート (省略時はカレント)")
    s.add_argument("--profile", choices=["auto", *profiles.PROFILES], default="auto",
                   help="用途: auto (ファイルから自動判定) / mcu / python / vba / tool")
    s.add_argument("--force", action="store_true", help="config.yaml / static を上書き")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("start", help="タスクを開始して handoff を生成")
    s.add_argument("task")
    s.add_argument("--keywords", "-k", action="append", help="関連キーワード (カンマ区切り可)")
    s.add_argument("--constraint", "-c", action="append", help="制約 (複数指定可)")
    s.set_defaults(func=cmd_start)

    s = sub.add_parser("prepare", help="repository scan → SOURCE_CONTEXT/STATE/NEXT_PROMPT 生成 (新 RUN_ID)")
    s.add_argument("--task")
    s.add_argument("--keywords", "-k", action="append")
    s.add_argument("--constraint", "-c", action="append")
    s.set_defaults(func=cmd_prepare)

    s = sub.add_parser("apply", help="inbox/copilot_response.txt を解析し PATCH 適用 / NEED_CONTEXT 反映")
    s.add_argument("--run", action="store_true", help="PATCH 適用後に run を続けて実行")
    s.add_argument("--allow-large", action="store_true", help="人の判断で変更量制限超過を許可")
    s.add_argument("--ignore-run-id", action="store_true", help="RUN_ID 不一致を無視")
    s.add_argument("--reprocess", action="store_true", help="処理済みの回答を再処理")
    s.set_defaults(func=cmd_apply)

    s = sub.add_parser("paste", help="クリップボードの Copilot の回答を inbox に保存し apply → run (PATCH 時) まで実行")
    s.add_argument("--save-only", action="store_true", help="inbox に保存するだけ (watch と併用する場合)")
    s.add_argument("--no-run", action="store_true", help="PATCH 適用後に run しない")
    s.add_argument("--force", action="store_true", help="回答の形式チェックをせずに保存")
    s.add_argument("--allow-large", action="store_true", help="人の判断で変更量制限超過を許可")
    s.add_argument("--ignore-run-id", action="store_true", help="RUN_ID 不一致を無視")
    s.set_defaults(func=cmd_paste)

    s = sub.add_parser("build", help="build を実行")
    s.set_defaults(func=cmd_build)
    s = sub.add_parser("flash", help="マイコンへ書込み (flash.command 未設定なら省略)")
    s.add_argument("--force", action="store_true", help="BUILD PASS 前でも実行")
    s.set_defaults(func=cmd_flash)
    s = sub.add_parser("test", help="テストを実行 (test.mode に従う)")
    s.add_argument("--force", action="store_true", help="FLASH PASS 前でも実行")
    s.set_defaults(func=cmd_test)
    s = sub.add_parser("result", help="手動テストの結果を入力 (test.mode: manual)。既定はクリップボードの TEST 行")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--paste", action="store_true", help="クリップボードの TEST:<名前>:<PASS|FAIL> 行を取り込む (既定)")
    g.add_argument("--file", help="TEST 行を書いたファイルを取り込む")
    g.add_argument("--pass", dest="passed", nargs="?", const="", help="全テスト OK (任意で詳細)")
    g.add_argument("--fail", dest="failed", help="NG の内容")
    s.add_argument("--stage", choices=["build", "flash", "test"], default="test", help="--fail のとき、どの段階で失敗したか")
    s.add_argument("--log-file", help="エラーメッセージやログを書いたファイル (Copilot に渡す)")
    s.set_defaults(func=cmd_result)
    s = sub.add_parser("vba-export", help="Excel ブックの VBA モジュールを src/vba へ書き出す (Excel 必須)")
    s.set_defaults(func=cmd_vba_export)

    s = sub.add_parser("run", help="build → flash → test → 判定 → handoff 生成")
    s.set_defaults(func=cmd_run)
    s = sub.add_parser("next", help="現在状態から handoff/ を再生成")
    s.set_defaults(func=cmd_next)
    s = sub.add_parser("status", help="現在状態を表示")
    s.set_defaults(func=cmd_status)
    s = sub.add_parser("diff", help="このタスクで Harness が適用した変更を表示 (git 不要)")
    s.set_defaults(func=cmd_diff)

    s = sub.add_parser("ports", help="シリアルポート一覧 (ポートは開かない)")
    s.set_defaults(func=cmd_ports)
    s = sub.add_parser("monitor", help="UART ログを表示・保存 (pyserial 不要)")
    s.add_argument("--port", "-p", help="例: COM5 / /dev/ttyACM0 (省略時は config の test.uart.port)")
    s.add_argument("--baud", "-b", type=int)
    s.add_argument("--seconds", "-s", type=float, default=0, help="記録時間 (0 = Ctrl+C まで)")
    s.add_argument("--until", "-u", help="この文字列を含む行で終了 (例: TEST:END)")
    s.add_argument("--reset", choices=["none", "dtr", "rts"], help="開いた直後に MCU をリセット")
    s.add_argument("--send", help="開いた後に送る文字列")
    s.set_defaults(func=cmd_monitor)

    s = sub.add_parser("watch", help="inbox を監視して apply → run を自動実行")
    s.add_argument("--interval", type=float, default=1.0)
    s.add_argument("--no-run", action="store_true", help="PATCH 適用後に run しない")
    s.set_defaults(func=cmd_watch)

    s = sub.add_parser("rollback", help="Harness が適用した PATCH を backup から戻す")
    s.add_argument("run_id", nargs="?")
    s.set_defaults(func=cmd_rollback)

    s = sub.add_parser("note", help="STATE の VERIFIED / FINDING / SUSPECTS 等を人が更新")
    s.add_argument("--verified", "-v", action="append", help="確認済み事項 (複数可)")
    s.add_argument("--finding")
    s.add_argument("--suspect", action="append")
    s.add_argument("--clear-suspects", action="store_true")
    s.add_argument("--constraint", "-c", action="append")
    s.add_argument("--objective")
    s.add_argument("--keywords", "-k", action="append")
    s.add_argument("--resume", action="store_true", help="HUMAN_REVIEW_REQUIRED から再開")
    s.add_argument("--reset-iterations", action="store_true")
    s.add_argument("--clear-requested", action="store_true", help="NEED_CONTEXT で追加したファイルをクリア")
    s.add_argument("--task", help="依頼内容 (やりたいこと) を書き換える (履歴・修正回数は保持)")
    s.set_defaults(func=cmd_note)
    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except HarnessError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
