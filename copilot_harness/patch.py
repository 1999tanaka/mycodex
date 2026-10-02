"""Patch Manager: unified diff の解析・安全検証。

適用前チェック (仕様 26):
  1. patch構文確認  2. path確認  3. allowed path確認
  4. changed file数確認  5. changed line数確認  6. git apply --check  7. patch適用
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config
from .util import allow_match, deny_match, first_match, read_text

HUNK_RE = re.compile(r"^@@\s*-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s*@@(.*)$")
WIN_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


@dataclass
class Hunk:
    old_start: int | None
    new_start: int | None
    section: str
    lines: list[str] = field(default_factory=list)  # 先頭文字 ' ', '+', '-', '\\'

    @property
    def old_lines(self) -> list[str]:
        return [ln[1:] for ln in self.lines if ln[:1] in (" ", "-")]

    @property
    def old_count(self) -> int:
        return sum(1 for ln in self.lines if ln[:1] in (" ", "-"))

    @property
    def new_count(self) -> int:
        return sum(1 for ln in self.lines if ln[:1] in (" ", "+"))


@dataclass
class FilePatch:
    old_path: str | None
    new_path: str | None
    hunks: list[Hunk] = field(default_factory=list)
    extended: list[str] = field(default_factory=list)  # diff --git 直後の拡張ヘッダ

    @property
    def path(self) -> str:
        return self.new_path or self.old_path or ""

    @property
    def is_new(self) -> bool:
        return self.old_path is None

    @property
    def is_delete(self) -> bool:
        return self.new_path is None

    @property
    def added(self) -> int:
        return sum(1 for h in self.hunks for ln in h.lines if ln.startswith("+"))

    @property
    def deleted(self) -> int:
        return sum(1 for h in self.hunks for ln in h.lines if ln.startswith("-"))


@dataclass
class ParsedPatch:
    files: list[FilePatch] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def added(self) -> int:
        return sum(f.added for f in self.files)

    @property
    def deleted(self) -> int:
        return sum(f.deleted for f in self.files)

    @property
    def paths(self) -> list[str]:
        return [f.path for f in self.files]


@dataclass
class Validation:
    status: str  # PASS / REJECTED / HUMAN_REVIEW_REQUIRED
    errors: list[str] = field(default_factory=list)
    limit_violations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "PASS"


# ---------------------------------------------------------------- 解析
def _header_path(raw: str) -> str | None:
    s = raw.split("\t", 1)[0].strip()
    if s.startswith('"') and s.endswith('"') and len(s) >= 2:  # git の C-quote (8進エスケープ)
        try:
            s = s[1:-1].encode("ascii").decode("unicode_escape").encode("latin-1").decode("utf-8")
        except (UnicodeError, ValueError):
            s = s[1:-1]
    if s == "/dev/null":
        return None
    if s.startswith(("a/", "b/")):
        s = s[2:]
    return s


def parse_patch(text: str) -> ParsedPatch:
    pp = ParsedPatch()
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    cur: FilePatch | None = None
    hunk: Hunk | None = None
    i = 0
    garbage = 0
    while i < len(lines):
        ln = lines[i]
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if ln.startswith("diff --git "):
            cur = FilePatch(old_path="", new_path="")
            pp.files.append(cur)
            hunk = None
            i += 1
            continue
        if ln.startswith("--- ") and nxt.startswith("+++ "):
            if cur is None or cur.hunks or (cur.old_path not in ("",) and cur.new_path not in ("",)):
                cur = FilePatch(old_path="", new_path="")
                pp.files.append(cur)
            cur.old_path = _header_path(ln[4:])
            cur.new_path = _header_path(nxt[4:])
            hunk = None
            i += 2
            continue
        if ln.startswith("@@"):
            if cur is None:
                pp.errors.append(f"ファイルヘッダ (---/+++) の無い hunk があります: {ln}")
                i += 1
                continue
            m = HUNK_RE.match(ln)
            if m:
                hunk = Hunk(int(m.group(1)), int(m.group(3)), m.group(5).strip())
            else:
                hunk = Hunk(None, None, "")
                pp.warnings.append(f"{cur.path}: hunk header に行番号がありません ({ln.strip()}) → 内容から位置を特定します。")
            cur.hunks.append(hunk)
            i += 1
            continue
        if hunk is not None:
            if ln == "":
                hunk.lines.append(" ")  # 空のコンテキスト行で先頭スペースが落ちたもの
            elif ln[0] in " +-\\":
                hunk.lines.append(ln)
            else:
                pp.errors.append(f"{cur.path}: 不正な hunk 行: {ln[:80]!r}")
            i += 1
            continue
        if cur is not None and not cur.hunks:
            cur.extended.append(ln)
        elif ln.strip():
            garbage += 1
        i += 1

    if garbage:
        pp.warnings.append(f"diff 以外の行を {garbage} 行無視しました。")
    if not pp.files:
        pp.errors.append("unified diff のファイルヘッダ (--- a/... / +++ b/...) が見つかりません。")
    for fp in pp.files:
        ext = "\n".join(fp.extended)
        if "GIT binary patch" in ext or "Binary files" in ext:
            pp.errors.append(f"{fp.path}: binary patch は許可されていません。")
        if re.search(r"^(rename|copy) (from|to) ", ext, re.MULTILINE):
            pp.errors.append(f"{fp.path}: rename/copy は許可されていません。")
        if re.search(r"^(old|new) mode |^(new|deleted) file mode (?!100644)", ext, re.MULTILINE):
            pp.errors.append(f"{fp.path}: file mode の変更は許可されていません。")
        if fp.old_path == "" or fp.new_path == "":
            pp.errors.append("--- / +++ ヘッダの無いファイルエントリがあります。")
        elif not fp.hunks:
            pp.errors.append(f"{fp.path}: hunk (@@) がありません。")
        for h in fp.hunks:
            if not any(x[:1] in "+-" for x in h.lines):
                pp.warnings.append(f"{fp.path}: 変更行の無い hunk があります。")
    return pp


# ---------------------------------------------------------------- 位置補正・描画
def _locate(file_lines: list[str], old: list[str]) -> tuple[int | None, bool]:
    """old の連続一致位置 (0-based) を返す。(位置, 末尾空白無視で一致したか)"""
    n = len(old)
    if n == 0:
        return None, False
    hits = [i for i in range(len(file_lines) - n + 1) if file_lines[i:i + n] == old]
    if len(hits) == 1:
        return hits[0], False
    if hits:
        return None, False
    rs_file = [x.rstrip() for x in file_lines]
    rs_old = [x.rstrip() for x in old]
    hits = [i for i in range(len(rs_file) - n + 1) if rs_file[i:i + n] == rs_old]
    return (hits[0], True) if len(hits) == 1 else (None, False)


def resolve_hunks(pp: ParsedPatch, root: Path) -> None:
    """行番号の無い hunk を対象ファイルの内容から位置決めする。"""
    for fp in pp.files:
        if not any(h.old_start is None for h in fp.hunks):
            continue
        if fp.is_new:
            for h in fp.hunks:
                h.old_start, h.new_start = 0, 1
            continue
        target = root / fp.old_path
        try:
            file_lines = read_text(target).replace("\r\n", "\n").split("\n")
        except OSError:
            pp.errors.append(f"{fp.path}: 対象ファイルを読めません。")
            continue
        for h in fp.hunks:
            if h.old_start is not None:
                continue
            pos, fuzzy = _locate(file_lines, h.old_lines)
            if pos is None:
                pp.errors.append(f"{fp.path}: hunk の位置を特定できません (コンテキスト不一致または複数箇所に一致)。")
                continue
            if fuzzy:  # 行末空白の差異 → 実ファイルの行で置き換える
                j = pos
                for k, ln in enumerate(h.lines):
                    if ln[:1] in (" ", "-"):
                        h.lines[k] = ln[0] + file_lines[j]
                        j += 1
            h.old_start = pos + 1
        offset = 0
        for h in sorted((x for x in fp.hunks if x.old_start is not None), key=lambda x: x.old_start):
            if h.new_start is None:
                h.new_start = h.old_start + offset
            offset += h.new_count - h.old_count


def _uses_crlf(path: Path) -> bool:
    try:
        return b"\r\n" in path.read_bytes()[:65536]
    except OSError:
        return False


def render_patch(pp: ParsedPatch, root: Path, match_line_endings: bool = True) -> str:
    out: list[str] = []
    for fp in pp.files:
        a = f"a/{fp.old_path}" if fp.old_path else "/dev/null"
        b = f"b/{fp.new_path}" if fp.new_path else "/dev/null"
        out.append(f"diff --git a/{fp.path} b/{fp.path}")
        if fp.is_new:
            out.append("new file mode 100644")
        elif fp.is_delete:
            out.append("deleted file mode 100644")
        out += [f"--- {a}", f"+++ {b}"]
        eol = "\r" if match_line_endings and not fp.is_new and _uses_crlf(root / fp.old_path) else ""
        for h in fp.hunks:
            os_, ns = h.old_start or 0, h.new_start or 0
            if h.old_count == 0 and os_ > 0 and fp.is_new:
                os_ = 0
            sec = f" {h.section}" if h.section else ""
            out.append(f"@@ -{os_},{h.old_count} +{ns},{h.new_count} @@{sec}")
            for ln in h.lines:
                out.append(ln if ln.startswith("\\") else ln + eol)
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- 検証
def check_path(rel: str, root: Path) -> str | None:
    """path 確認。問題があればエラーメッセージを返す。"""
    if not rel or rel.strip() != rel:
        return f"不正なパス: {rel!r}"
    if any(ord(c) < 32 for c in rel):
        return f"制御文字を含むパス: {rel!r}"
    if "\\" in rel:
        return f"バックスラッシュを含むパス: {rel}"
    if rel.startswith("/") or rel.startswith("~") or re.match(r"^[A-Za-z]:", rel) or ":" in rel:
        return f"絶対パス/ドライブ指定/ADS は禁止: {rel}"
    parts = rel.split("/")
    if any(p in ("", ".", "..") for p in parts):
        return f"'..' や空要素を含むパス: {rel}"
    for p in parts:
        if p.split(".")[0].lower() in WIN_RESERVED or p.endswith((".", " ")):
            return f"Windows で不正なファイル名: {rel}"
    root_r = root.resolve()
    target = root_r / rel
    try:
        target.resolve().relative_to(root_r)
    except ValueError:
        return f"project root の外を指すパス: {rel}"
    cur = root_r
    for p in parts:
        cur = cur / p
        if cur.is_symlink():
            return f"symlink を経由するパス: {rel}"
    return None


def validate_patch(pp: ParsedPatch, cfg: Config, declared_files: list[str] | None = None) -> Validation:
    v = Validation(status="PASS", errors=list(pp.errors), warnings=list(pp.warnings))
    root = cfg.project_root
    allowed = cfg.list("security.allowed_paths")
    denied = cfg.patch_denylist()
    seen: set[str] = set()
    for fp in pp.files:
        for rel in {p for p in (fp.old_path, fp.new_path) if p}:
            err = check_path(rel, root)
            if err:
                v.errors.append(err)
                continue
            pat = first_match(rel, denied, deny_match)
            if pat:
                v.errors.append(f"禁止対象への変更: {rel} (pattern: {pat})")
            elif not first_match(rel, allowed, allow_match):
                v.errors.append(f"許可パス外への変更: {rel} (allowed: {', '.join(allowed)})")
        if fp.path in seen:
            v.errors.append(f"同じファイルが複数回現れます: {fp.path}")
        seen.add(fp.path)
        if fp.old_path and fp.new_path and fp.old_path != fp.new_path:
            v.errors.append(f"rename は許可されていません: {fp.old_path} → {fp.new_path}")
        target = root / fp.path
        if fp.is_new and target.exists():
            v.errors.append(f"新規作成指定ですが既に存在します: {fp.path}")
        if not fp.is_new and not target.exists():
            v.errors.append(f"対象ファイルが存在しません: {fp.path}")

    lim = cfg.get("limits", {})
    if len(pp.files) > int(lim.get("max_changed_files", 5)):
        v.limit_violations.append(f"変更ファイル数 {len(pp.files)} > MAX_CHANGED_FILES {lim.get('max_changed_files')}")
    if pp.added > int(lim.get("max_added_lines", 500)):
        v.limit_violations.append(f"追加行数 {pp.added} > MAX_ADDED_LINES {lim.get('max_added_lines')}")
    if pp.deleted > int(lim.get("max_deleted_lines", 500)):
        v.limit_violations.append(f"削除行数 {pp.deleted} > MAX_DELETED_LINES {lim.get('max_deleted_lines')}")

    if declared_files is not None:
        declared = {d.replace("\\", "/").removeprefix("./") for d in declared_files}
        undeclared = [p for p in pp.paths if p not in declared]
        if undeclared:
            v.warnings.append(f"FILES に記載の無いファイルを変更しています: {', '.join(undeclared)}")

    if v.errors:
        v.status = "REJECTED"
    elif v.limit_violations:
        v.status = "HUMAN_REVIEW_REQUIRED"
    return v
