"""Repository 探索・git 操作 (読み取り系 + apply のみ。commit は行わない)。

git は任意。無い場合 (または project が git repository でない場合) は、
タスク開始時に取ったソースファイルのスナップショット (baseline.json) との比較で変更を検出する。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config
from .util import deny_match, first_match, now_iso, read_text, write_text

MAX_SCAN_FILES = 20000


def git(root: Path, *args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-c", "core.quotepath=off", *args],
        cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout,
    )


def git_available() -> bool:
    """git が使えるか。環境変数 HARNESS_NO_GIT=1 で git を使わないモードを強制できる。"""
    if os.environ.get("HARNESS_NO_GIT", "").strip() not in ("", "0"):
        return False
    return shutil.which("git") is not None


def is_git_repo(root: Path) -> bool:
    if not git_available():
        return False
    try:
        return git(root, "rev-parse", "--is-inside-work-tree").stdout.strip() == "true"
    except (OSError, subprocess.SubprocessError):
        return False


def _lines(cp: subprocess.CompletedProcess) -> list[str]:
    if cp.returncode != 0:
        return []
    return [ln.strip() for ln in cp.stdout.splitlines() if ln.strip()]


def detect_changes(cfg: Config, scan: "RepoScan | None" = None) -> tuple[list[str], str]:
    """Priority 1: 変更中ファイル。戻り値: (ファイル, 検出方法)。

    git repository なら git diff --name-only + 未追跡ファイル、
    そうでなければタスク開始時のスナップショットとの比較。
    """
    if is_git_repo(cfg.project_root):
        return _git_changed_files(cfg.project_root), "git diff"
    return snapshot_changes(cfg, scan), "タスク開始後に変更"


def changed_files(cfg: Config, scan: "RepoScan | None" = None) -> list[str]:
    return detect_changes(cfg, scan)[0]


def _git_changed_files(root: Path) -> list[str]:
    cp = git(root, "diff", "--name-only", "--relative", "HEAD")
    files = _lines(cp)
    if cp.returncode != 0:  # まだ commit が無いリポジトリ
        files = _lines(git(root, "diff", "--name-only", "--relative"))
        files += _lines(git(root, "diff", "--name-only", "--relative", "--cached"))
    files += _lines(git(root, "ls-files", "--others", "--exclude-standard"))
    out: list[str] = []
    for f in files:
        if f not in out:
            out.append(f)
    return out


def git_status_text(root: Path) -> str:
    if not is_git_repo(root):
        return "(git repository ではありません)"
    return git(root, "status", "--short", "--branch").stdout


def git_diff_text(root: Path) -> str:
    if not is_git_repo(root):
        return ""
    cp = git(root, "diff", "--relative", "HEAD")
    if cp.returncode != 0:
        cp = git(root, "diff", "--relative")
    return cp.stdout


def git_toplevel(root: Path) -> Path | None:
    if not is_git_repo(root):
        return None
    out = git(root, "rev-parse", "--show-toplevel").stdout.strip()
    return Path(out).resolve() if out else None


@dataclass
class RepoScan:
    root: Path
    files: list[str] = field(default_factory=list)
    source_files: list[str] = field(default_factory=list)
    excluded: list[str] = field(default_factory=list)

    def exists(self, rel: str) -> bool:
        return rel in self._set

    def __post_init__(self) -> None:
        self._set = set(self.files)

    def finalize(self) -> "RepoScan":
        self._set = set(self.files)
        return self


def scan_repository(cfg: Config) -> RepoScan:
    root = cfg.project_root
    exclude_dirs = {d.lower() for d in cfg.list("project.exclude_dirs")}
    exclusions = cfg.context_exclusions()
    exts = cfg.source_extensions()
    scan = RepoScan(root)
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames
            if d.lower() not in exclude_dirs and not os.path.islink(os.path.join(dirpath, d))
        )
        for name in sorted(filenames):
            full = Path(dirpath) / name
            rel = full.relative_to(root).as_posix()
            if first_match(rel, exclusions, deny_match):
                scan.excluded.append(rel)
                continue
            scan.files.append(rel)
            if full.suffix.lower() in exts:
                scan.source_files.append(rel)
            count += 1
            if count >= MAX_SCAN_FILES:
                return scan.finalize()
    return scan.finalize()


def build_repo_map(scan: RepoScan) -> str:
    """REPO_MAP.md 本文 (ディレクトリ毎にファイル名を列挙)。"""
    groups: dict[str, list[str]] = {}
    for rel in scan.files:
        d, _, name = rel.rpartition("/")
        groups.setdefault(d, []).append(name)
    lines: list[str] = []
    for d in sorted(groups, key=lambda x: (x != "", x.lower())):
        lines.append(f"{d}/" if d else "./")
        for name in groups[d]:
            lines.append(f"  {name}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_repo_map(cfg: Config, scan: RepoScan) -> str:
    text = "# REPO_MAP\n\n```text\n" + build_repo_map(scan) + "```\n"
    write_text(cfg.harness_dir / "REPO_MAP.md", text)
    return text


# ---------------------------------------------------------------- スナップショット (git 無し用)
def _baseline_path(cfg: Config) -> Path:
    return cfg.harness_dir / "baseline.json"


def _signature(path: Path) -> list:
    st = path.stat()
    return [st.st_size, st.st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest()]


def take_snapshot(cfg: Config, scan: RepoScan | None = None) -> int:
    """タスク開始時点のソースファイル (サイズ・更新時刻・SHA-256) を記録する。"""
    scan = scan or scan_repository(cfg)
    files = {}
    for rel in scan.source_files:
        try:
            files[rel] = _signature(cfg.project_root / rel)
        except OSError:
            continue
    write_text(_baseline_path(cfg), json.dumps({"created_at": now_iso(), "files": files}, indent=1) + "\n")
    return len(files)


def snapshot_changes(cfg: Config, scan: RepoScan | None = None) -> list[str]:
    """スナップショット以降に変更・追加されたソースファイル。スナップショットが無ければ作成する。"""
    path = _baseline_path(cfg)
    scan = scan or scan_repository(cfg)
    if not path.exists():
        take_snapshot(cfg, scan)
        return []
    try:
        base = json.loads(read_text(path)).get("files", {})
    except (ValueError, OSError):
        return []
    out: list[str] = []
    for rel in scan.source_files:
        sig = base.get(rel)
        if sig is None:
            out.append(rel)
            continue
        try:
            st = (cfg.project_root / rel).stat()
            if st.st_size == sig[0] and st.st_mtime_ns == sig[1]:
                continue  # 高速判定: サイズと更新時刻が同じなら未変更
            if _signature(cfg.project_root / rel)[2] != sig[2]:
                out.append(rel)
        except OSError:
            continue
    return out


def snapshot_report(cfg: Config) -> str:
    changed = snapshot_changes(cfg)
    lines = ["git が使えないため、タスク開始時のスナップショットとの比較を記録します。", ""]
    lines += [f"M/A {p}" for p in changed] or ["(変更なし)"]
    return "\n".join(lines) + "\n"
