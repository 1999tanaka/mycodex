"""ブラウザ版 (GitHub Pages) のサイトを組み立てる。

    python web/build.py [出力先 (既定: _site)]

出力:
    index.html / style.css / core.js / ui.js   画面
    harness.zip                                harness.py + copilot_harness (ブラウザ内の Python で実行)
    demo-python.zip / demo-python-response.txt デモ用プロジェクトとサンプル回答
"""

from __future__ import annotations

import shutil
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
PAGES = ("index.html", "style.css", "core.js", "ui.js")
SKIP = {"__pycache__"}


def add_tree(zf: zipfile.ZipFile, src: Path, arc_root: str, exclude: set[str] = frozenset()) -> int:
    n = 0
    for p in sorted(src.rglob("*")):
        rel = p.relative_to(src)
        if p.is_dir() or any(part in SKIP for part in rel.parts) or p.suffix == ".pyc" or rel.as_posix() in exclude:
            continue
        zf.write(p, f"{arc_root}/{rel.as_posix()}" if arc_root else rel.as_posix())
        n += 1
    return n


def main() -> int:
    out = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT / "_site"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    build = time.strftime("%Y%m%d%H%M%S")

    for name in PAGES:
        text = (WEB / name).read_text(encoding="utf-8").replace("__BUILD__", build)
        (out / name).write_text(text, encoding="utf-8", newline="\n")
    (out / ".nojekyll").write_text("", encoding="utf-8")

    with zipfile.ZipFile(out / "harness.zip", "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(ROOT / "harness.py", "harness.py")
        n = add_tree(zf, ROOT / "copilot_harness", "copilot_harness")
    demo = ROOT / "examples" / "python_tool_demo"
    with zipfile.ZipFile(out / "demo-python.zip", "w", zipfile.ZIP_DEFLATED) as zf:
        add_tree(zf, demo, "", exclude={"sample_response.txt"})
    shutil.copy2(demo / "sample_response.txt", out / "demo-python-response.txt")

    print(f"built {out} (build {build}, harness files {n + 1})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
