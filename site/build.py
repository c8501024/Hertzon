"""Build the Hertzon website: the landing page plus the documentation from docs/*.md.

    python site/build.py [output_dir]        (default: _site)

The Markdown files in docs/ stay the single source: GitHub shows them as they are, and this script turns
them into HTML pages with a navigation sidebar, an "On this page" list and a search box. The tutorial is
split into one page per chapter. Links between the Markdown files (``CIRCUIT.md#parts``) and anchors are
rewritten to the generated pages; links to anything else in the repository go to GitHub.

Needs the ``markdown`` package (pip install markdown).
"""
from __future__ import annotations

import html
import json
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
DOCS = ROOT / "docs"
REPO = "https://github.com/c8501024/Hertzon"
BASE_URL = "https://c8501024.github.io/Hertzon/"

# Navigation: (section title, [(source file relative to the repo root, page title)])
NAV = [
    ("Get started", [
        ("docs/TUTORIAL.md", "Tutorial"),
        ("docs/MCP.md", "AI co-design (MCP)"),
    ]),
    ("User guide", [
        ("docs/CIRCUIT.md", "Circuit designs"),
        ("docs/TUNING.md", "Derivative tuning"),
        ("docs/LUMPED_ELEMENTS.md", "Lumped elements"),
        ("docs/PCB_IMPORT.md", "PCB import"),
        ("docs/GERBER_EXPORT.md", "Gerber export"),
        ("docs/REMOTE.md", "Remote and cloud solving"),
        ("docs/RESULT_FILES.md", "Result files"),
        ("docs/MESHER.md", "Meshing"),
    ]),
    ("Reference", [
        ("docs/FEATURE_MATRIX.md", "Feature overview"),
        ("docs/BENCHMARKS.md", "Benchmarks"),
        ("docs/ROADMAP.md", "Roadmap"),
        ("CHANGELOG.md", "Changelog"),
    ]),
    ("Project", [
        ("docs/PRINCIPLES.md", "Design principles"),
        ("docs/EMERGE_INTEGRATION.md", "EMerge integration"),
        ("docs/EMERGE_CHANGES.md", "EMerge changes"),
        ("docs/THIRD_PARTY.md", "Third-party software"),
    ]),
]

SPLIT_FILES = {"docs/TUTORIAL.md"}   # one page per "## " chapter


# --------------------------------------------------------------------------------------------- Markdown

def slugify(text: str, sep: str = "-") -> str:
    """GitHub's heading anchors, so links written for GitHub keep working."""
    text = re.sub(r"<[^>]+>", "", text).strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def md_to_html(text: str) -> tuple[str, list]:
    md = markdown.Markdown(
        extensions=["tables", "fenced_code", "sane_lists", "toc", "attr_list"],
        extension_configs={"toc": {"slugify": slugify, "toc_depth": "2-3"}},
    )
    body = md.convert(text)
    return body, md.toc_tokens


def split_chapters(text: str) -> list[tuple[str, str]]:
    """Split Markdown at level-2 headings outside code blocks: [(heading, text)], the first one is ''."""
    parts, cur, head, fence = [], [], "", False
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith("```"):
            fence = not fence
        if not fence and line.startswith("## "):
            parts.append((head, "".join(cur)))
            cur, head = [], line[3:].strip()
        cur.append(line)
    parts.append((head, "".join(cur)))
    return parts


# --------------------------------------------------------------------------------------------- pages

@dataclass
class Page:
    src: str              # repo-relative Markdown file
    url: str              # site-relative output path, e.g. docs/circuit.html
    title: str            # title in the navigation
    md: str
    section: str
    nav_child: bool = False
    body: str = ""
    toc: list = field(default_factory=list)
    ids: set = field(default_factory=set)


def page_name(src: str) -> str:
    return Path(src).stem.lower().replace("_", "-")


def collect_pages() -> list[Page]:
    pages = []
    for section, items in NAV:
        for src, title in items:
            text = (ROOT / src).read_text(encoding="utf-8")
            name = page_name(src)
            if src in SPLIT_FILES:
                chapters = split_chapters(text)
                intro = chapters[0][1]
                rest = chapters[1:]
                # a trailing un-numbered chapter ("Where to go next") stays on the overview page
                tail = [c for c in rest if not re.match(r"\d", c[0])]
                rest = [c for c in rest if re.match(r"\d", c[0])]
                intro += "".join(t for _, t in tail)
                pages.append(Page(src, f"docs/{name}.html", title, intro, section))
                for head, body in rest:
                    num = head.split(".")[0]
                    label = re.sub(r"^\d+\.\s*", "", head)
                    pages.append(Page(src, f"docs/{name}-{num}.html", f"{num}. {label}", body, section,
                                      nav_child=True))
            else:
                pages.append(Page(src, f"docs/{name}.html", title, text, section))
    return pages


def rel(from_url: str, to_url: str) -> str:
    depth = from_url.count("/")
    return "../" * depth + to_url


def rewrite_links(page: Page, pages: list[Page], anchor_map: dict) -> None:
    src_dir = (ROOT / page.src).parent

    def resolve(target: str) -> str | None:
        if re.match(r"^[a-z]+:|^//", target):
            return None
        path, _, anchor = target.partition("#")
        if path:
            abs_path = (src_dir / path).resolve()
            try:
                repo_rel = abs_path.relative_to(ROOT).as_posix()
            except ValueError:
                return None
        else:
            repo_rel = page.src
        if repo_rel.endswith(".md"):
            if anchor and (repo_rel, anchor) in anchor_map:
                dest = anchor_map[(repo_rel, anchor)]
                return rel(page.url, dest) + ("" if dest == page.url and not anchor else f"#{anchor}")
            for p in pages:
                if p.src == repo_rel:
                    return rel(page.url, p.url) + (f"#{anchor}" if anchor else "")
            return f"{REPO}/blob/main/{repo_rel}" + (f"#{anchor}" if anchor else "")
        if repo_rel.startswith("docs/") and (ROOT / repo_rel).is_file() and \
                Path(repo_rel).suffix.lower() in {".png", ".jpg", ".gif", ".svg"}:
            return rel(page.url, repo_rel)  # images are copied to the same path
        return f"{REPO}/blob/main/{repo_rel}" + (f"#{anchor}" if anchor else "")

    def sub(m):
        attr, target = m.group(1), html.unescape(m.group(2))
        new = resolve(target)
        return m.group(0) if new is None else f'{attr}="{html.escape(new, quote=True)}"'

    page.body = re.sub(r'(href|src)="([^"]+)"', sub, page.body)


# --------------------------------------------------------------------------------------------- HTML

ICON = {
    "search": '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>',
    "github": '<svg viewBox="0 0 16 16" width="18" height="18" fill="currentColor"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0 0 16 8c0-4.42-3.58-8-8-8Z"/></svg>',
    "menu": '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18M3 12h18M3 18h18"/></svg>',
}


def header(prefix: str, active: str) -> str:
    def a(href, label, key):
        cls = ' class="active"' if key == active else ""
        return f'<a href="{prefix}{href}"{cls}>{label}</a>'
    return f"""<header class="top">
<div class="top-in">
<button class="menu" aria-label="Menu" onclick="document.body.classList.toggle('nav-open')">{ICON['menu']}</button>
<a class="brand" href="{prefix}index.html"><img src="{prefix}hertzon.png" alt="" width="28" height="28">Hertzon</a>
<nav class="main">
{a('docs/tutorial.html', 'Tutorial', 'tutorial')}
{a('docs/circuit.html', 'Docs', 'docs')}
{a('docs/feature-matrix.html', 'Features', 'features')}
<a href="{REPO}/releases/latest">Download</a>
</nav>
<button class="search-btn" onclick="hzSearch.open()" aria-label="Search">{ICON['search']}<span>Search docs</span><kbd>/</kbd></button>
<a class="gh" href="{REPO}" aria-label="GitHub">{ICON['github']}</a>
</div>
</header>"""


SEARCH_DIALOG = """<div id="search" class="search" hidden onclick="if(event.target===this)hzSearch.close()">
<div class="search-box">
<input id="search-q" type="search" placeholder="Search the documentation" autocomplete="off">
<ol id="search-res"></ol>
</div>
</div>"""


def footer(prefix: str) -> str:
    return f"""<footer class="foot">
<div class="foot-in">
<div><strong>Hertzon</strong> is an independent open-source project by Dominik Mair. Its FEM solver core is
<a href="https://github.com/FennisRobert/EMerge">EMerge</a> by Robert Fennis.
It is not affiliated with any vendor of commercial simulation software. Product names mentioned are trademarks of
their respective owners.</div>
<div class="foot-links"><a href="{REPO}">GitHub</a> · <a href="{REPO}/releases">Releases</a> ·
<a href="{REPO}/issues">Issues</a> · <a href="{prefix}docs/changelog.html">Changelog</a> ·
<a href="{REPO}/blob/main/LICENSE">GPL-2.0-or-later</a></div>
</div>
</footer>"""


def head(title: str, desc: str, prefix: str, canonical: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<meta name="description" content="{html.escape(desc, quote=True)}">
<link rel="canonical" href="{BASE_URL}{canonical}">
<link rel="icon" href="{prefix}hertzon.png">
<link rel="stylesheet" href="{prefix}style.css">
<script>window.HZ_PREFIX="{prefix}";</script>
<script defer src="{prefix}search.js"></script>
</head>"""


def first_paragraph(md_text: str) -> str:
    for block in re.split(r"\n\s*\n", md_text):
        b = block.strip()
        if b and not b.startswith(("#", "*", "|", "!", ">", "-", "`", "<")):
            t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", b)
            t = re.sub(r"[*_`]", "", t).replace("\n", " ")
            return t[:200]
    return ""


def render_toc(tokens: list) -> str:
    items, flat = [], []

    def walk(ts):
        for t in ts:
            if t["level"] in (2, 3):
                flat.append(t)
            walk(t.get("children", []))
    walk(tokens)
    for t in flat:
        items.append(f'<li class="l{t["level"]}"><a href="#{t["id"]}">{t["name"]}</a></li>')
    return "\n".join(items)


def render_sidebar(page: Page, pages: list[Page]) -> str:
    out = []
    for section, _ in NAV:
        out.append(f'<div class="nav-sec">{section}</div><ul>')
        for p in [p for p in pages if p.section == section]:
            cls = []
            if p is page:
                cls.append("cur")
            if p.nav_child:
                cls.append("child")
            c = f' class="{" ".join(cls)}"' if cls else ""
            out.append(f'<li{c}><a href="{rel(page.url, p.url)}">{html.escape(p.title)}</a></li>')
        out.append("</ul>")
    return "\n".join(out)


def render_page(page: Page, pages: list[Page]) -> str:
    prefix = "../"
    idx = pages.index(page)
    prev_p = pages[idx - 1] if idx > 0 else None
    next_p = pages[idx + 1] if idx + 1 < len(pages) else None
    pn = ['<nav class="pn">']
    pn.append(f'<a class="prev" href="{rel(page.url, prev_p.url)}"><small>Previous</small>{html.escape(prev_p.title)}</a>'
              if prev_p else "<span></span>")
    pn.append(f'<a class="next" href="{rel(page.url, next_p.url)}"><small>Next</small>{html.escape(next_p.title)}</a>'
              if next_p else "<span></span>")
    pn.append("</nav>")
    toc = render_toc(page.toc)
    edit = f"{REPO}/blob/main/{page.src}"
    active = "tutorial" if page.src == "docs/TUTORIAL.md" else (
        "features" if page.src == "docs/FEATURE_MATRIX.md" else "docs")
    h1 = re.search(r"<h1[^>]*>(.*?)</h1>", page.body)
    title_txt = re.sub(r"<[^>]+>", "", h1.group(1)) if h1 else page.title
    if page.nav_child:
        title_txt = f"Tutorial {page.title}"
    return f"""{head(f"{title_txt} – Hertzon documentation", first_paragraph(page.md) or title_txt, prefix, page.url)}
<body class="docs">
{header(prefix, active)}
<div class="layout">
<aside class="side"><nav>{render_sidebar(page, pages)}</nav></aside>
<main class="content">
<div class="crumbs">{page.section}{' · Tutorial' if page.nav_child else ''}</div>
<article class="md">
{page.body}
</article>
{''.join(pn)}
<p class="edit"><a href="{edit}">View this page on GitHub</a></p>
</main>
<aside class="toc">{'<div class="toc-h">On this page</div><ul>' + toc + '</ul>' if toc else ''}</aside>
</div>
{footer(prefix)}
{SEARCH_DIALOG}
</body>
</html>
"""


def search_entries(page: Page) -> list[dict]:
    """One entry per h1/h2/h3 section: title, url with anchor, plain text."""
    entries = []
    parts = re.split(r"(<h[1-3][^>]*>.*?</h[1-3]>)", page.body, flags=re.S)
    cur_title, cur_id, buf = page.title, "", []

    def flush():
        txt = re.sub(r"<[^>]+>", " ", "".join(buf))
        txt = re.sub(r"\s+", " ", html.unescape(txt)).strip()
        if txt or cur_id:
            entries.append({"t": cur_title, "p": page.title, "u": page.url + (f"#{cur_id}" if cur_id else ""),
                            "x": txt[:1500]})
    for part in parts:
        m = re.match(r'<h([1-3])(?: id="([^"]*)")?[^>]*>(.*?)</h\1>', part, flags=re.S)
        if m:
            flush()
            buf = []
            cur_id = m.group(2) or ""
            cur_title = html.unescape(re.sub(r"<[^>]+>", "", m.group(3)))
        else:
            buf.append(part)
    flush()
    return entries


# --------------------------------------------------------------------------------------------- build

def build(out: Path) -> None:
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    pages = collect_pages()
    for p in pages:
        p.body, p.toc = md_to_html(p.md)
        p.ids = set(re.findall(r'id="([^"]+)"', p.body))
    anchor_map = {}
    for p in pages:
        for i in p.ids:
            anchor_map.setdefault((p.src, i), p.url)
    for p in pages:
        rewrite_links(p, pages, anchor_map)
        # wide tables scroll on their own, not the page
        p.body = p.body.replace("<table>", '<div class="tw"><table>').replace("</table>", "</table></div>")

    (out / "docs").mkdir()
    for p in pages:
        (out / p.url).write_text(render_page(p, pages), encoding="utf-8")

    index = []
    for p in pages:
        index += search_entries(p)
    (out / "search-index.json").write_text(json.dumps(index, ensure_ascii=False, separators=(",", ":")),
                                           encoding="utf-8")

    # static files of the site, then the images the pages use (no copies in site/)
    for f in SITE.iterdir():
        if f.name in {"build.py", "__pycache__", "index.html"}:
            continue
        shutil.copy2(f, out / f.name) if f.is_file() else shutil.copytree(f, out / f.name)
    landing = (SITE / "index.html").read_text(encoding="utf-8")
    landing = landing.replace("<!--HEADER-->", header("", "")).replace("<!--FOOTER-->", footer("")) \
                     .replace("<!--SEARCH-->", SEARCH_DIALOG)
    (out / "index.html").write_text(landing, encoding="utf-8")
    for sub in ("screenshots", "tutorial", "benchmark"):
        if (DOCS / sub).is_dir():
            shutil.copytree(DOCS / sub, out / "docs" / sub)
    (out / "screenshots").mkdir(exist_ok=True)
    for f in (DOCS / "screenshots").glob("*.png"):
        shutil.copy2(f, out / "screenshots" / f.name)
    shutil.copy2(ROOT / "hertzon/gui/splash.jpg", out / "splash.jpg")
    for f in (ROOT / "hertzon/gui").glob("*.png"):
        shutil.copy2(f, out / f.name)

    urls = [""] + [p.url for p in pages]
    (out / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "".join(f"  <url><loc>{BASE_URL}{u}</loc></url>\n" for u in urls) + "</urlset>\n", encoding="utf-8")
    print(f"{len(pages)} pages, {len(index)} search entries -> {out}")


if __name__ == "__main__":
    build(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "_site")
