"""Harness の各操作 (prepare / apply / next / status / rollback / note)。"""

from __future__ import annotations

import difflib
import json
import shutil
import subprocess
from pathlib import Path

from . import pyapply
from . import state as S
from .config import Config, yaml_backend
from .context import ContextResult, build_context, resolve_name
from .patch import check_path, parse_patch, render_patch, resolve_hunks, validate_patch
from .repo import (
    changed_files, git_available, git_diff_text, git_status_text, git_toplevel, is_git_repo,
    scan_repository, snapshot_report, take_snapshot, write_repo_map,
)
from .report import render_next_prompt, render_state_md, render_test_result_md
from .response import NEED_CONTEXT, PATCH, parse_response
from .util import HarnessError, deny_match, first_match, read_text, sha256_text, write_text


PROMPT_MARKER = "回答は必ず次のどちらかの形式にしてください"  # NEXT_PROMPT.txt 固有の文


def say(msg: str = "") -> None:
    print(msg, flush=True)


class Harness:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.state = S.State.load(cfg.state_path)

    @property
    def root(self) -> Path:
        return self.cfg.project_root

    def run_dir(self, base: Path, run_id: str | None = None) -> Path:
        return base / (run_id or self.state["run_id"])

    # ------------------------------------------------------------ handoff
    def generate_handoff(self) -> ContextResult:
        st = self.state
        st.ensure_run_id()
        scan = scan_repository(self.cfg)
        write_repo_map(self.cfg, scan)
        ctx = build_context(self.cfg, st, scan)
        hidden = self.cfg.context_exclusions() + [f"{d}/**" for d in self.cfg.list("project.exclude_dirs")]
        changed = [
            f for f in dict.fromkeys(changed_files(self.cfg, scan) + st["harness_changed_files"])
            if not first_match(f, hidden, deny_match)
        ]
        files = {
            "STATE.md": render_state_md(self.cfg, st, changed),
            "SOURCE_CONTEXT.md": ctx.text,
            "TEST_RESULT.md": render_test_result_md(self.cfg, st, st["build_excerpt"], st["flash_excerpt"], st["uart_excerpt"]),
            "NEXT_PROMPT.txt": render_next_prompt(self.cfg, st),
        }
        snap = self.run_dir(self.cfg.history_dir)
        for name, text in files.items():
            write_text(self.cfg.handoff_dir / name, text)
            write_text(snap / name, text)
        st.save()
        say(f"handoff/ を生成しました (RUN_ID {st['run_id']})")
        for sel in ctx.selected:
            say(f"  context: {sel.path:<40} {sel.reason}")
        for sel in ctx.omitted:
            say(f"  omitted: {sel.path} ({sel.reason})")
        say(f"  SOURCE_CONTEXT.md: {len(ctx.text):,} 文字")
        return ctx

    # ------------------------------------------------------------ start / prepare / next
    def start(self, task: str, keywords: list[str], constraints: list[str]) -> None:
        self.state.start_task(task, keywords, constraints)
        say(f"タスクを開始しました: {task}")
        if not is_git_repo(self.root):
            n = take_snapshot(self.cfg)
            say(f"  git 無し: ソースファイル {n} 件のスナップショットを記録しました (変更検出用)")
        self.prepare()

    def prepare(self) -> None:
        st = self.state
        if not st["task"]:
            raise HarnessError('タスクが未設定です: python harness.py start "<task>" --keywords A,B')
        run_id = st.new_run_id()
        logs = self.run_dir(self.cfg.logs_dir)
        if is_git_repo(self.root):
            write_text(logs / "git_status.txt", git_status_text(self.root))
            write_text(logs / "git_diff.patch", git_diff_text(self.root))
        else:
            write_text(logs / "changes.txt", snapshot_report(self.cfg))
        if st["phase"] != S.HUMAN_REVIEW_REQUIRED:
            st.set_phase(S.WAITING_COPILOT)
        st.add_history({"run_id": run_id, "action": "PREPARE"})
        self.generate_handoff()
        self._print_next_steps()

    def next(self) -> None:
        self.generate_handoff()
        self._print_next_steps()

    def _print_next_steps(self) -> None:
        if self.state["phase"] in (S.DONE, S.HUMAN_REVIEW_REQUIRED):
            say(f"\n[{self.state['phase']}] {self.state.next_action()}")
            return
        say("\n次の操作:")
        say(f"  1. Copilot に {self.cfg.handoff_dir} の STATE.md / SOURCE_CONTEXT.md / TEST_RESULT.md を添付し、")
        say("     NEXT_PROMPT.txt の内容を貼り付けて送信")
        say("  2. Copilot の回答のコピーボタンを押して  python harness.py paste")
        say("     (回答をファイルに保存した場合は  python harness.py apply --run)")

    # ------------------------------------------------------------ paste
    def save_response(self, text: str, *, force: bool = False) -> None:
        """Copilot の回答を inbox/copilot_response.txt に保存する (paste コマンド用)。"""
        text = text.replace("\r\n", "\n")
        if not text.strip():
            raise HarnessError("クリップボードが空です。Copilot の回答のコピーボタンを押してから実行してください。")
        prompt_path = self.cfg.handoff_dir / "NEXT_PROMPT.txt"
        prompt = read_text(prompt_path).replace("\r\n", "\n").strip() if prompt_path.exists() else ""
        if not force and ((prompt and text.strip() == prompt) or PROMPT_MARKER in text):
            raise HarnessError("クリップボードの内容は NEXT_PROMPT (Copilot への質問) のままです。\n"
                               "  Copilot の回答の下にあるコピーボタンを押してから、もう一度実行してください。")
        res = parse_response(text)
        if res.action is None and not force:
            raise HarnessError("クリップボードの内容は Copilot の回答ではないようです (ACTION 行がありません)。\n"
                               "  このまま保存する場合は --force を付けてください。")
        path = self.cfg.response_path
        if path.exists():
            old = read_text(path)
            if old.strip() and sha256_text(old) not in self.state["processed_responses"] and old != text:
                write_text(path.with_name("copilot_response.prev.txt"), old)
                say("  未処理だった前回の回答を copilot_response.prev.txt に退避しました")
        write_text(path, text)
        say(f"Copilot の回答を保存しました: {path}")
        say(f"  ACTION: {res.action or '?'} / RUN_ID: {res.run_id or '(なし)'} / "
            f"FILES: {', '.join(res.files) or '-'} / {len(text):,} 文字")

    # ------------------------------------------------------------ apply
    def apply(self, *, allow_large: bool = False, ignore_run_id: bool = False, reprocess: bool = False) -> str | None:
        """inbox の応答を処理する。戻り値: 処理した ACTION (失敗時は例外)。"""
        st, cfg = self.state, self.cfg
        path = cfg.response_path
        if not path.exists() or not read_text(path).strip():
            raise HarnessError(f"Copilot の回答がありません: {path}")
        raw = read_text(path)
        digest = sha256_text(raw)
        if digest in st["processed_responses"] and not reprocess:
            raise HarnessError("この回答は処理済みです (再処理する場合は --reprocess)。")
        if st["phase"] == S.DONE:
            raise HarnessError("タスクは完了済みです。新しいタスクは start で開始してください。")

        res = parse_response(raw)
        st["processed_responses"] = (st["processed_responses"] + [digest])[-100:]
        write_text(self.run_dir(cfg.history_dir) / "copilot_response.txt", raw)
        for w in res.warnings:
            say(f"  warning: {w}")
        if not res.ok:
            return self._reject("応答形式エラー", res.errors)

        if res.run_id is None:
            say(f"  warning: 応答に RUN_ID がありません (現在 {st['run_id']})")
        elif res.run_id != st["run_id"] and not ignore_run_id:
            st.save()
            raise HarnessError(
                f"RUN_ID 不一致: 応答={res.run_id} / 現在={st['run_id']}。古い回答の可能性があります。\n"
                "  最新の handoff を Copilot へ渡し直すか、意図的なら --ignore-run-id を指定してください。"
            )
        self._absorb_notes(res)
        if res.action == NEED_CONTEXT:
            self._need_context(res)
            return NEED_CONTEXT
        self._apply_patch(res, allow_large)
        return PATCH

    def _absorb_notes(self, res) -> None:
        st = self.state
        if res.finding:
            st["finding"] = res.finding
        if res.suspects:
            st["suspects"] = res.suspects
        if res.next_objective:
            st["next_objective"] = res.next_objective

    def _reject(self, title: str, errors: list[str], note: str = "") -> None:
        st = self.state
        st.add_history({"run_id": st["run_id"], "action": "REJECTED", "note": f"{title}: {'; '.join(errors)}"})
        st["last_response_note"] = note or f"前回の回答は Harness により却下されました ({title}): " + " / ".join(errors)
        self.generate_handoff()
        raise HarnessError(f"{title}:\n  - " + "\n  - ".join(errors))

    def _need_context(self, res) -> None:
        st, cfg = self.state, self.cfg
        scan = scan_repository(cfg)
        excl = cfg.context_exclusions()
        added, missing = [], []
        for name in res.files:
            err = None if "/" not in name and "\\" not in name else check_path(name.replace("\\", "/"), self.root)
            hits = [] if err else resolve_name(name, scan, cfg)
            hits = [h for h in hits if not first_match(h, excl, deny_match)]
            if hits:
                added += [h for h in hits if h not in added]
            else:
                missing.append(name)
        st["requested_files"] = list(dict.fromkeys(st["requested_files"] + added))
        st["missing_files"] = list(dict.fromkeys(st["missing_files"] + missing))
        if res.keywords:
            st["keywords"] = list(dict.fromkeys(st["keywords"] + res.keywords))
        st["last_response_note"] = f"前回 Copilot は追加情報を要求しました: {res.reason}" if res.reason else ""
        st.add_history({"run_id": st["run_id"], "action": "NEED_CONTEXT", "files": added, "note": res.reason})
        if st["phase"] not in (S.HUMAN_REVIEW_REQUIRED,):
            st.set_phase(S.WAITING_COPILOT)
        say(f"NEED_CONTEXT: 追加 {len(added)} 件 {added}" + (f" / 見つからない {missing}" if missing else ""))
        self.generate_handoff()
        self._print_next_steps()

    # ------------------------------------------------------------ patch
    def _apply_patch(self, res, allow_large: bool) -> None:
        st, cfg = self.state, self.cfg
        max_iter = int(cfg.get("limits.max_iterations", 5))
        if st["phase"] == S.HUMAN_REVIEW_REQUIRED and not allow_large:
            st.save()
            raise HarnessError(f"HUMAN_REVIEW_REQUIRED のため停止中: {st['phase_reason']}\n  レビュー後 `note --resume` で再開してください。")
        if st["iteration"] >= max_iter:
            st.set_phase(S.HUMAN_REVIEW_REQUIRED, f"MAX_ITERATIONS ({max_iter}) に到達")
            st.save()
            raise HarnessError(f"HUMAN_REVIEW_REQUIRED: MAX_ITERATIONS ({max_iter}) に到達しました。")
        self.patch_engine()  # 設定エラーを先に検出

        pp = parse_patch(res.patch)
        if not pp.errors:
            resolve_hunks(pp, self.root)
        v = validate_patch(pp, cfg, res.files or None)
        engine = self.patch_engine(pp) if v.status != "REJECTED" else "python"
        for w in v.warnings:
            say(f"  warning: {w}")
        if v.status == "REJECTED":
            return self._reject("patch validation 失敗", v.errors)
        if v.status == "HUMAN_REVIEW_REQUIRED" and not allow_large:
            st.set_phase(S.HUMAN_REVIEW_REQUIRED, "; ".join(v.limit_violations))
            st.add_history({"run_id": st["run_id"], "action": "PATCH_HELD", "files": pp.paths, "note": "; ".join(v.limit_violations)})
            st.save()
            raise HarnessError("HUMAN_REVIEW_REQUIRED (変更量制限超過):\n  - " + "\n  - ".join(v.limit_violations)
                               + "\n  内容を確認し、適用する場合は apply --allow-large --reprocess")

        patch_text = render_patch(pp, self.root, bool(cfg.get("patch.match_line_endings", True)))
        src_run = st["run_id"]
        patch_file = self.run_dir(cfg.history_dir, src_run) / "patch.diff"
        patch_file.parent.mkdir(parents=True, exist_ok=True)
        patch_file.write_bytes(patch_text.encode("utf-8"))  # CRLF をそのまま保持

        label = "git apply --check" if engine == "git" else "patch --check (内蔵エンジン)"
        errors, pres = self._check_patch(engine, pp, patch_file, src_run)
        if errors:
            return self._reject(f"{label} 失敗", errors,
                                note=f"前回の PATCH は {label} で失敗しました。SOURCE_CONTEXT.md の現行コードに"
                                     "一致するコンテキスト行で diff を作り直してください: " + " / ".join(errors))

        new_run = st.new_run_id()
        self._backup(pp, new_run, patch_text, src_run)
        err = self._write_patch(engine, patch_file, pres, new_run)
        if err:
            self.rollback(new_run, quiet=True)
            st.set_phase(S.HUMAN_REVIEW_REQUIRED, "patch の書き込みに失敗しました (check は成功)")
            st.save()
            raise HarnessError(f"patch 適用失敗: {err}")

        st["iteration"] += 1
        st["last_patch"] = {
            "run_id": new_run, "source_run_id": src_run, "files": pp.paths, "added": pp.added,
            "deleted": pp.deleted, "summary": res.summary, "validation": "PASS",
        }
        st["harness_changed_files"] = list(dict.fromkeys(st["harness_changed_files"] + pp.paths))
        st["last_response_note"] = ""
        st["missing_files"] = []
        st.reset_results(new_run)
        st.set_phase(S.PATCH_APPLIED)
        st.add_history({"run_id": new_run, "action": "PATCH", "files": pp.paths, "summary": res.summary})
        st.save()
        say(f"PATCH を適用しました: {', '.join(pp.paths)} (+{pp.added} -{pp.deleted})  RUN_ID {src_run} → {new_run}")
        say(f"  iteration {st['iteration']} / {cfg.get('limits.max_iterations')}  (engine: {engine})")

    def patch_engine(self, pp=None) -> str:
        """patch.engine: auto (git リポジトリなら git、それ以外は内蔵) / git / python。

        auto では、UTF-8 以外 (Shift-JIS など) のファイルを変更する patch や、
        新規ファイルを UTF-8/LF 以外で作る設定 (VBA) の場合は内蔵エンジンを使う。
        git apply はバイト列で照合するため、Shift-JIS の日本語を含む行と一致できないため。
        """
        eng = str(self.cfg.get("patch.engine", "auto") or "auto").lower()
        if eng not in ("auto", "git", "python"):
            raise HarnessError(f"patch.engine の値が不正です: {eng} (auto / git / python)")
        if eng == "auto":
            # git repository でないフォルダでは git apply が利用者の git 設定 (core.autocrlf 等) の影響を
            # 受けて CRLF のファイルと一致しないことがあるため、内蔵エンジンを使う
            if not is_git_repo(self.root) or (pp is not None and self._needs_builtin_engine(pp)):
                return "python"
            return "git"
        if eng == "git" and not git_available():
            raise HarnessError("patch.engine: git ですが git が見つかりません。auto または python を指定してください。")
        return eng

    def _needs_builtin_engine(self, pp) -> bool:
        enc = str(self.cfg.get("patch.new_file_encoding") or "utf-8").lower().replace("_", "-")
        eol = str(self.cfg.get("patch.new_file_eol") or "lf").lower()
        for fp in pp.files:
            if fp.is_new:
                if enc not in ("utf-8", "utf8") or eol == "crlf":
                    return True
                continue
            try:
                (self.root / fp.path).read_bytes().decode("utf-8")
            except UnicodeDecodeError:
                return True
            except OSError:
                continue
        return False

    def _check_patch(self, engine: str, pp, patch_file: Path, run_id: str):
        """git apply --check 相当。戻り値: (エラー行, 内蔵エンジンの結果)。"""
        log = self.run_dir(self.cfg.logs_dir, run_id) / "patch_check.log"
        if engine == "git":
            chk = self._git_apply(patch_file, check=True)
            write_text(log, "engine: git\n" + chk.stdout + chk.stderr)
            if chk.returncode == 0:
                return [], None
            return (chk.stderr or chk.stdout).strip().splitlines()[-8:] or ["(詳細不明)"], None
        pres = pyapply.check(pp, self.root, new_encoding=str(self.cfg.get("patch.new_file_encoding") or "utf-8"),
                             new_eol=str(self.cfg.get("patch.new_file_eol") or "lf"))
        write_text(log, "engine: python\n" + "\n".join(pres.messages) + "\n")
        return ([] if pres.ok else pres.messages), pres

    def _write_patch(self, engine: str, patch_file: Path, pres, run_id: str) -> str:
        log = self.run_dir(self.cfg.logs_dir, run_id) / "patch_apply.log"
        if engine == "git":
            app = self._git_apply(patch_file, check=False)
            write_text(log, "engine: git\n" + app.stdout + app.stderr)
            return "" if app.returncode == 0 else (app.stderr.strip() or "git apply error")
        try:
            pyapply.write(pres, self.root)
        except OSError as e:
            return str(e)
        write_text(log, "engine: python\n" + "\n".join(pres.messages) + "\n")
        return ""

    def _git_apply(self, patch_file: Path, check: bool) -> subprocess.CompletedProcess:
        root = self.root.resolve()
        cwd, extra = root, []
        top = git_toplevel(root)
        if top and top != root:
            cwd, extra = top, [f"--directory={root.relative_to(top).as_posix()}"]
        args = [str(a) for a in self.cfg.list("patch.git_apply_args")]
        cmd = ["git", "apply", *extra, *args, "--whitespace=nowarn", "-v"]
        if check:
            cmd.append("--check")
        cmd.append(str(patch_file.resolve()))
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")

    def _backup(self, pp, run_id: str, patch_text: str, src_run: str) -> None:
        bdir = self.run_dir(self.cfg.backup_dir, run_id)
        manifest = {"run_id": run_id, "source_run_id": src_run, "files": []}
        for fp in pp.files:
            src = self.root / fp.path
            existed = src.exists()
            if existed:
                dst = bdir / "files" / fp.path
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
            manifest["files"].append({"path": fp.path, "existed": existed})
        (bdir / "applied.patch").write_bytes(patch_text.encode("utf-8"))
        write_text(bdir / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))

    # ------------------------------------------------------------ rollback
    def rollback(self, run_id: str | None = None, quiet: bool = False) -> None:
        bdir_root = self.cfg.backup_dir
        if run_id is None:
            cands = sorted(p.name for p in bdir_root.glob("*") if (p / "manifest.json").exists())
            if not cands:
                raise HarnessError("backup がありません。")
            run_id = cands[-1]
        bdir = bdir_root / run_id
        mf = bdir / "manifest.json"
        if not mf.exists():
            raise HarnessError(f"backup が見つかりません: {bdir}")
        manifest = json.loads(read_text(mf))
        for ent in manifest["files"]:
            rel = ent["path"]
            if check_path(rel, self.root):
                continue
            target = self.root / rel
            if ent["existed"]:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(bdir / "files" / rel, target)
            elif target.exists():
                target.unlink()
            if not quiet:
                say(f"  restored: {rel}")
        if not quiet:
            st = self.state
            st.add_history({"run_id": st["run_id"], "action": "ROLLBACK", "note": f"backup {run_id} を復元"})
            st["last_response_note"] = f"RUN_ID {run_id} の PATCH は人の判断でロールバックされました。"
            st.set_phase(S.WAITING_COPILOT)
            st.save()
            say(f"RUN_ID {run_id} の変更を元に戻しました。")

    # ------------------------------------------------------------ note / status
    def note(self, *, verified=(), finding=None, suspects=(), clear_suspects=False, constraints=(),
             objective=None, keywords=(), resume=False, reset_iterations=False, clear_requested=False,
             task=None) -> None:
        st = self.state
        if task is not None and task.strip():
            st["task"] = task.strip()  # 依頼内容の変更 (履歴や修正回数は保持)
        st["verified"] = list(dict.fromkeys(st["verified"] + list(verified)))
        if finding is not None:
            st["finding"] = finding
        if clear_suspects:
            st["suspects"] = []
        st["suspects"] = list(dict.fromkeys(st["suspects"] + list(suspects)))
        st["constraints"] = list(dict.fromkeys(st["constraints"] + list(constraints)))
        st["keywords"] = list(dict.fromkeys(st["keywords"] + list(keywords)))
        if objective is not None:
            st["next_objective"] = objective
        if reset_iterations:
            st["iteration"] = 0
        if clear_requested:
            st["requested_files"], st["missing_files"] = [], []
        if resume and st["phase"] == S.HUMAN_REVIEW_REQUIRED:
            st.set_phase(S.WAITING_COPILOT, "")
            if st["iteration"] >= int(self.cfg.get("limits.max_iterations", 5)):
                st["iteration"] = 0
        st.save()
        say("STATE を更新しました。handoff を再生成するには: python harness.py next")

    def status_text(self) -> str:
        st = self.state
        fails = sum(1 for t in st["tests"].values() if t["status"] in ("FAIL", "ERROR"))
        hw = "未実施" if not st["tests"] else (f"{fails} FAIL" if fails else "ALL PASS")
        lines = [
            f"RUN_ID: {st['run_id'] or '(未発行)'}", "",
            "Task:", st["task"] or "(未設定)", "",
            "Phase:", st["phase"] + (f" ({st['phase_reason']})" if st["phase_reason"] else ""), "",
            "Build:", st["build"], "",
            *(["Flash:", st["flash"], ""] if self.cfg.get("flash.command") or st["flash"] not in ("NOT_RUN", "SKIPPED") else []),
            ("Hardware:" if self.cfg.profile.name == "mcu" else "Test:"), hw, "",
            "Iterations:", f"{st['iteration']} / {self.cfg.get('limits.max_iterations')}", "",
            "Next Action:", st.next_action(),
        ]
        if st["failures"]:
            lines += ["", "Failures:"] + [f"  - {f}" for f in st["failures"]]
        git = "git repository" if is_git_repo(self.root) else ("git あり (repository ではない)" if git_available() else "git なし")
        try:
            engine = self.patch_engine()
        except HarnessError as e:
            engine = str(e)
        lines += ["", "Environment:", f"  profile: {self.cfg.profile.name} ({self.cfg.profile.title})",
                  f"  YAML: {yaml_backend()} / {git} / patch engine: {engine}"]
        return "\n".join(lines)

    # ------------------------------------------------------------ diff (git 不要のレビュー用)
    def diff_text(self) -> str:
        """このタスクで Harness が適用した変更を、適用前 (backup) と現在の比較で unified diff にする。"""
        originals: dict[str, bytes | None] = {}
        for h in self.state["history"]:
            if h.get("action") != "PATCH":
                continue
            bdir = self.cfg.backup_dir / h["run_id"]
            mf = bdir / "manifest.json"
            if not mf.exists():
                continue
            for ent in json.loads(read_text(mf))["files"]:
                rel = ent["path"]
                if rel not in originals:
                    originals[rel] = (bdir / "files" / rel).read_bytes() if ent["existed"] else None
        out: list[str] = []
        for rel, before in originals.items():
            cur_path = self.root / rel
            after = cur_path.read_bytes() if cur_path.exists() else None
            if before == after:
                continue
            a = pyapply.decode(before)[0].replace("\r\n", "\n") if before is not None else ""
            b = pyapply.decode(after)[0].replace("\r\n", "\n") if after is not None else ""
            out += difflib.unified_diff(
                pyapply.split_lines(a), pyapply.split_lines(b),
                f"a/{rel}" if before is not None else "/dev/null",
                f"b/{rel}" if after is not None else "/dev/null",
            )
            if out and not out[-1].endswith("\n"):
                out[-1] += "\n\\ No newline at end of file\n"
        return "".join(out)
