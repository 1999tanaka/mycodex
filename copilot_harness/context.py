"""Context Builder: Large Repository → Relevant Files → SOURCE_CONTEXT.md。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from .config import Config
from .repo import RepoScan, build_repo_map, detect_changes
from .state import State
from .util import deny_match, first_match, read_text

LANG_TAGS = {
    ".ino": "cpp", ".pde": "cpp", ".c": "c", ".h": "cpp", ".cpp": "cpp", ".cc": "cpp",
    ".cxx": "cpp", ".hpp": "cpp", ".hh": "cpp", ".hxx": "cpp", ".py": "python",
    ".s": "asm", ".asm": "asm", ".rs": "rust", ".ld": "text", ".ini": "ini",
    ".yaml": "yaml", ".yml": "yaml", ".json": "json", ".cmake": "cmake", ".md": "markdown",
}
HEADER_EXTS = {".h", ".hpp", ".hh", ".hxx"}
TEMPLATE_MARKER = "<!-- harness:template -->"

# path.ext:line / path.ext(line) 形式 (gcc, clang, armcc, MSVC, IAR)
LOG_PATH_RE = re.compile(
    r"(?P<path>(?:[A-Za-z]:)?[\w./\\\-+ ]*?[\w\-+]+\.(?:ino|pde|c|cc|cpp|cxx|h|hh|hpp|hxx|s|S|asm|py|rs))"
    r"(?=[:(]\s*\d+)"
)
INCLUDE_RE = re.compile(r'^\s*#\s*include\s*[<"]([^>"]+)[>"]', re.MULTILINE)


@dataclass
class Selected:
    path: str
    reason: str


@dataclass
class ContextResult:
    text: str
    selected: list[Selected] = field(default_factory=list)
    omitted: list[Selected] = field(default_factory=list)
    truncated: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- 解決系
def resolve_name(name: str, scan: RepoScan, cfg: Config) -> list[str]:
    """ログ・Copilot 要求・include 中のファイル名を repo 内の相対パスへ解決する。"""
    raw = name.strip().strip("`'\"").replace("\\", "/")
    if not raw:
        return []
    root = cfg.project_root
    p = Path(raw)
    if p.is_absolute() or re.match(r"^[A-Za-z]:/", raw):
        try:
            rel = Path(raw).resolve().relative_to(root).as_posix()
            if scan.exists(rel):
                return [rel]
        except (ValueError, OSError):
            pass
        raw = PurePosixPath(raw).name  # repo 外の絶対パス → basename で探す
    while raw.startswith("./"):
        raw = raw[2:]
    if raw.startswith(("a/", "b/")) and scan.exists(raw[2:]):
        return [raw[2:]]
    if scan.exists(raw):
        return [raw]
    base = PurePosixPath(raw).name
    if base.lower().endswith(".ino.cpp"):  # arduino-cli の一時変換ファイル
        base = base[:-4]
    hits = [f for f in scan.files if PurePosixPath(f).name.lower() == base.lower()]
    if len(hits) > 1 and "/" in raw:  # 末尾一致で絞り込む
        narrowed = [f for f in hits if f.lower().endswith(raw.lower())]
        hits = narrowed or hits
    return hits[:3]


def files_from_log(text: str, scan: RepoScan, cfg: Config, limit: int = 8) -> list[str]:
    out: list[str] = []
    for m in LOG_PATH_RE.finditer(text or ""):
        for rel in resolve_name(m.group("path").strip(), scan, cfg):
            if rel not in out:
                out.append(rel)
        if len(out) >= limit:
            break
    return out


def keyword_hits(keywords: list[str], scan: RepoScan, cfg: Config, cache: dict[str, str]) -> list[tuple[str, int]]:
    pats = [re.compile(r"(?<![A-Za-z0-9])" + re.escape(k), re.IGNORECASE) for k in keywords if k.strip()]
    if not pats:
        return []
    scored: list[tuple[str, int]] = []
    for rel in scan.source_files:
        name = PurePosixPath(rel).name
        content = _content(rel, cfg, cache)
        score = 0
        for pat in pats:
            if pat.search(name):
                score += 20
            score += min(len(pat.findall(content)), 30)
        if score:
            scored.append((rel, score))
    scored.sort(key=lambda x: (-x[1], x[0]))
    return scored


def include_deps(rel: str, scan: RepoScan, cfg: Config, cache: dict[str, str]) -> list[str]:
    out: list[str] = []
    here = PurePosixPath(rel).parent
    for inc in INCLUDE_RE.findall(_content(rel, cfg, cache)):
        inc = inc.strip()
        cands = [(here / inc).as_posix()] + [f"{d.strip('/')}/{inc}" for d in cfg.list("project.include_dirs")] + [inc]
        found = next((c for c in cands if scan.exists(_normpath(c))), None)
        if found:
            found = _normpath(found)
        else:
            hits = resolve_name(inc, scan, cfg)
            found = hits[0] if len(hits) == 1 else None
        if found and found not in out and found != rel:
            out.append(found)
    return out


def same_name_pairs(rel: str, scan: RepoScan) -> list[str]:
    p = PurePosixPath(rel)
    stem = p.name.split(".")[0].lower()
    return [
        f for f in scan.source_files
        if f != rel and PurePosixPath(f).name.split(".")[0].lower() == stem
    ]


def _normpath(p: str) -> str:
    parts: list[str] = []
    for part in p.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


def _content(rel: str, cfg: Config, cache: dict[str, str]) -> str:
    if rel not in cache:
        try:
            cache[rel] = read_text(cfg.project_root / rel)
        except OSError:
            cache[rel] = ""
    return cache[rel]


def _is_binary(path: Path) -> bool:
    try:
        return b"\0" in path.read_bytes()[:4096]
    except OSError:
        return True


# ---------------------------------------------------------------- 選択
def select_files(cfg: Config, state: State, scan: RepoScan, cache: dict[str, str]) -> list[Selected]:
    exclusions = cfg.context_exclusions()
    chosen: dict[str, str] = {}

    def add(rel: str, reason: str) -> None:
        if rel in chosen or not scan.exists(rel):
            return
        if first_match(rel, exclusions, deny_match):
            return
        if _is_binary(cfg.project_root / rel):
            return
        chosen[rel] = reason

    for rel in state["requested_files"]:
        add(rel, "P0 Copilot NEED_CONTEXT 要求")
    for name in cfg.list("context.pinned_files"):
        for rel in resolve_name(name, scan, cfg):
            add(rel, "P0 config pinned")
    changed, method = detect_changes(cfg, scan)
    for rel in changed:
        if rel in scan.source_files:
            add(rel, f"P1 変更中 ({method})")
    for rel in state["build_error_files"]:
        add(rel, "P2 build error")
    for rel in state["test_failure_files"]:
        add(rel, "P3 test failure (log)")
    for rel, score in keyword_hits(state["test_failure_keywords"], scan, cfg, cache)[:3]:
        add(rel, f"P3 test failure keyword ({', '.join(state['test_failure_keywords'])})")
    user_kw = list(dict.fromkeys(state["keywords"] + cfg.list("context.keywords")))
    for rel, score in keyword_hits(user_kw, scan, cfg, cache)[:6]:
        add(rel, f"P4 keyword ({', '.join(user_kw)}) score={score}")

    if not chosen:  # 手掛かりが無い場合はエントリポイントを入れる
        for rel in scan.source_files:
            name = PurePosixPath(rel).name.lower()
            if rel.lower().endswith(".ino") or name.split(".")[0] == "main":
                add(rel, "P7 entry point")

    base = list(chosen)
    for rel in base:
        for dep in include_deps(rel, scan, cfg, cache):
            add(dep, f"P5 include from {rel}")
    deps = [r for r in chosen if r not in base]
    for rel in base + deps:  # 直接選ばれたファイルのペアを優先
        for pair in same_name_pairs(rel, scan):
            add(pair, f"P6 same name as {rel}")
    return [Selected(p, r) for p, r in chosen.items()]


# ---------------------------------------------------------------- 描画
def _fence(content: str) -> str:
    longest = max((len(m) for m in re.findall(r"`+", content)), default=0)
    return "`" * max(3, longest + 1)


def render_file(rel: str, content: str) -> str:
    tag = LANG_TAGS.get(PurePosixPath(rel).suffix.lower(), "text")
    if PurePosixPath(rel).name in ("CMakeLists.txt", "Makefile"):
        tag = "cmake" if rel.endswith(".txt") else "makefile"
    body = content.replace("\r\n", "\n").replace("\r", "\n")
    if not body.endswith("\n"):
        body += "\n"
    fence = _fence(body)
    return f"# FILE: {rel}\n\n{fence}{tag}\n{body}{fence}\n"


def static_info_section(cfg: Config) -> str:
    """Notebook を使わない場合、static/ の記入済み情報を Context に含める。"""
    if cfg.get("handoff.use_notebook"):
        return ""
    parts: list[str] = []
    for name in ("PROJECT.md", "ARCHITECTURE.md", "HARDWARE.md", "CODING_RULES.md"):
        p = cfg.static_dir / name
        if not p.exists():
            continue
        text = read_text(p).strip()
        if not text or TEMPLATE_MARKER in text:
            continue
        parts.append(f"## {name}\n\n{text}\n")
    if not parts:
        return ""
    return "# PROJECT INFO (static/)\n\n" + "\n".join(parts) + "\n"


def build_context(cfg: Config, state: State, scan: RepoScan) -> ContextResult:
    cache: dict[str, str] = {}
    selected = select_files(cfg, state, scan, cache)
    max_files = int(cfg.get("context.max_files", 12))
    max_file = int(cfg.get("context.max_file_chars", 40000))
    budget = int(cfg.get("context.max_total_chars", 150000))

    result = ContextResult(text="")
    blocks: list[str] = []
    used = 0
    for sel in selected:
        if len(result.selected) >= max_files:
            result.omitted.append(sel)
            continue
        content = _content(sel.path, cfg, cache)
        if len(content) > max_file:
            cut = content[:max_file]
            cut = cut[: cut.rfind("\n") + 1] or cut
            total_lines = content.count("\n") + 1
            content = cut + f"\n/* ... harness: {cut.count(chr(10)) + 1} 行目以降を省略 (全 {total_lines} 行) ... */\n"
            result.truncated.append(sel.path)
        block = render_file(sel.path, content)
        if used + len(block) > budget and result.selected:
            result.omitted.append(sel)
            continue
        used += len(block)
        blocks.append(block)
        result.selected.append(sel)

    head = [
        "# SOURCE_CONTEXT",
        "",
        f"RUN_ID: {state['run_id']}",
        "",
        "`# FILE: <path>` の <path> がリポジトリ内の実ファイル名です。diff ではこの path をそのまま使ってください。",
        "",
        "# SELECTED FILES",
        "",
    ]
    for i, sel in enumerate(result.selected, 1):
        mark = " (truncated)" if sel.path in result.truncated else ""
        head.append(f"{i}. {sel.path}{mark} — {sel.reason}")
    if not result.selected:
        head.append("(該当ファイルなし: keywords を指定するか NEED_CONTEXT で要求してください)")
    if result.omitted:
        head += ["", "# OMITTED FILES (容量制限)", ""]
        head += [f"- {s.path} — {s.reason}" for s in result.omitted]
    if state["missing_files"]:
        head += ["", "# NOT FOUND (要求されたが存在しない/提供不可)", ""]
        head += [f"- {m}" for m in state["missing_files"]]
    head.append("")
    text = "\n".join(head) + "\n"

    if cfg.get("context.include_repo_map", True):
        lines = build_repo_map(scan).splitlines()
        limit = int(cfg.get("context.repo_map_max_lines", 120))
        if len(lines) > limit:
            lines = lines[:limit] + [f"... ({len(lines) - limit} 行省略)"]
        text += "# REPO_MAP (excerpt)\n\n```text\n" + "\n".join(lines) + "\n```\n\n"
    text += static_info_section(cfg)
    text += "\n".join(blocks)
    result.text = text
    return result
