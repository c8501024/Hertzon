"""Branch overview: what is on dev but not yet released on main, and which pull requests are open.

Reads the GitHub API and writes one HTML page with three lanes of blocks:

    open pull requests  ->  dev, not yet released  ->  main (last release)

Python standard library only. A GitHub token is optional for the public repository (without one the API
allows 60 requests per hour). It is taken from GITHUB_TOKEN / GH_TOKEN, or from `gh auth token`.

    python tools/branch_overview.py            # writes the page and opens it in the browser
    python tools/branch_overview.py --serve    # local server, every reload of the page fetches fresh data
    python tools/branch_overview.py --out overview.html --no-open
"""
from __future__ import annotations

import argparse
import base64
import html
import http.server
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import webbrowser
from datetime import datetime, timezone

API = "https://api.github.com"
DEFAULT_REPO = "c8501024/hertzon"


# --- GitHub API -----------------------------------------------------------------------------------

def find_token() -> str | None:
    for name in ("GITHUB_TOKEN", "GH_TOKEN"):
        if os.environ.get(name):
            return os.environ[name]
    try:
        out = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    token = out.stdout.strip()
    return token if out.returncode == 0 and token else None


class GitHub:
    def __init__(self, repo: str, token: str | None):
        self.repo = repo
        self.token = token

    def get(self, path: str, **params):
        query = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{API}/repos/{self.repo}{path}" + (f"?{query}" if query else "")
        req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                                   "User-Agent": "hertzon-branch-overview"})
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as err:
            if err.code == 404:
                return None
            if err.code == 403 and err.headers.get("X-RateLimit-Remaining") == "0":
                raise SystemExit("GitHub API rate limit reached. Set GITHUB_TOKEN or log in with `gh auth login`.")
            raise


def file_version(gh: GitHub, ref: str) -> str | None:
    """The version in pyproject.toml on a branch."""
    data = gh.get("/contents/pyproject.toml", ref=ref)
    if not data or "content" not in data:
        return None
    text = base64.b64decode(data["content"]).decode("utf-8", "replace")
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
    return m.group(1) if m else None


def check_summary(gh: GitHub, sha: str) -> str:
    """'success', 'failure', 'pending' or 'none' from the check runs of a commit."""
    data = gh.get(f"/commits/{sha}/check-runs", per_page=100) or {}
    runs = data.get("check_runs", [])
    if not runs:
        return "none"
    if any(r["status"] != "completed" for r in runs):
        return "pending"
    if any(r["conclusion"] in ("failure", "timed_out", "cancelled", "action_required") for r in runs):
        return "failure"
    return "success"


def fetch(gh: GitHub, base: str, head: str) -> dict:
    """Everything the page shows, as plain data."""
    compare = gh.get(f"/compare/{base}...{head}", per_page=250) or {}
    # Commits on main that dev lacks. The merge commit of each release is always there and is harmless.
    reverse = gh.get(f"/compare/{head}...{base}", per_page=250) or {}
    merged_into_head = [p for p in gh.get("/pulls", state="closed", base=head, sort="updated",
                                          direction="desc", per_page=100) or [] if p.get("merged_at")]
    open_prs = gh.get("/pulls", state="open", sort="created", direction="desc", per_page=100) or []
    details = []
    for pr in open_prs:
        full = gh.get(f"/pulls/{pr['number']}") or pr
        full["checks"] = check_summary(gh, pr["head"]["sha"])
        details.append(full)
    releases = gh.get("/releases", per_page=1) or []
    return {
        "repo": gh.repo, "base": base, "head": head,
        "compare": compare, "only_on_base": reverse.get("commits", []), "merged": merged_into_head, "open": details,
        "release": releases[0] if releases else None,
        "version_base": file_version(gh, base), "version_head": file_version(gh, head),
        "fetched": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


# --- Model ----------------------------------------------------------------------------------------

def build_model(data: dict, released_count: int = 8) -> dict:
    """Sort the merged pull requests into 'only on dev' and 'released', and keep the commits on dev
    that no pull request brought in."""
    compare = data["compare"]
    ahead = compare.get("commits", [])
    ahead_shas = {c["sha"] for c in ahead}
    pending, released = [], []
    for pr in sorted(data["merged"], key=lambda p: p["merged_at"], reverse=True):
        (pending if pr.get("merge_commit_sha") in ahead_shas else released).append(pr)

    # Commits on dev that came in through none of the pending pull requests (pushed directly). Merge
    # commits are left out: they are either a pull request's merge or a merge of main into dev.
    covered = set()
    for pr in pending:
        covered.add(pr.get("merge_commit_sha"))
        covered.update(pr.get("_commit_shas", ()))
    direct = [c for c in ahead if c["sha"] not in covered and len(c.get("parents", [])) < 2]

    return {
        "repo": data["repo"], "base": data["base"], "head": data["head"],
        "ahead_by": compare.get("ahead_by", 0),
        "only_on_base": [c for c in data.get("only_on_base", []) if len(c.get("parents", [])) < 2],
        "pending": pending, "released": released[:released_count], "direct": direct,
        "open": data["open"], "release": data["release"],
        "version_base": data["version_base"], "version_head": data["version_head"],
        "fetched": data["fetched"],
    }


def attach_pr_commits(gh: GitHub, data: dict) -> None:
    """Add the commit list of each pending pull request, so its commits are not shown as direct ones."""
    ahead_shas = {c["sha"] for c in data["compare"].get("commits", [])}
    for pr in data["merged"]:
        if pr.get("merge_commit_sha") in ahead_shas:
            commits = gh.get(f"/pulls/{pr['number']}/commits", per_page=250) or []
            pr["_commit_shas"] = {c["sha"] for c in commits}
            pr["_commit_count"] = len(commits)


# --- HTML -----------------------------------------------------------------------------------------

def _date(iso: str | None) -> str:
    if not iso:
        return ""
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().strftime("%d.%m.%Y %H:%M")


def _e(text) -> str:
    return html.escape(str(text if text is not None else ""))


def _labels(pr: dict) -> str:
    return "".join(f'<span class="label" style="--c:#{_e(l["color"])}">{_e(l["name"])}</span>'
                   for l in pr.get("labels", []))


def _pr_block(pr: dict, kind: str, base: str, head: str) -> str:
    meta = [f'#{pr["number"]}', _e(pr["user"]["login"])]
    extra = ""
    if kind == "open":
        meta.append(f'{_e(pr["head"]["ref"])} → <b>{_e(pr["base"]["ref"])}</b>')
        meta.append(f'geöffnet {_date(pr["created_at"])}')
        badges = []
        if pr.get("draft"):
            badges.append('<span class="badge draft">Entwurf</span>')
        checks = {"success": ("ok", "Checks grün"), "failure": ("bad", "Checks rot"),
                  "pending": ("wait", "Checks laufen"), "none": ("none", "keine Checks")}[pr.get("checks", "none")]
        badges.append(f'<span class="badge {checks[0]}">{checks[1]}</span>')
        state = pr.get("mergeable_state")
        if state == "dirty":
            badges.append('<span class="badge bad">Merge-Konflikt</span>')
        elif state == "behind":
            badges.append(f'<span class="badge wait">hinter {_e(pr["base"]["ref"])}</span>')
        if pr["base"]["ref"] not in (head,):
            badges.append(f'<span class="badge warn">Ziel ist {_e(pr["base"]["ref"])}, nicht {_e(head)}</span>')
        if "additions" in pr:
            badges.append(f'<span class="diff"><span class="add">+{pr["additions"]}</span> '
                          f'<span class="del">−{pr["deletions"]}</span> · {pr["changed_files"]} Dateien</span>')
        extra = f'<div class="badges">{"".join(badges)}</div>'
    else:
        meta.append(f'gemergt {_date(pr["merged_at"])}')
        if pr.get("_commit_count"):
            meta.append(f'{pr["_commit_count"]} Commits')
    return (f'<a class="block {kind}" href="{_e(pr["html_url"])}" target="_blank" rel="noopener">'
            f'<div class="title">{_e(pr["title"])}</div>{_labels(pr)}'
            f'<div class="meta">{" · ".join(meta)}</div>{extra}</a>')


def _commit_block(c: dict) -> str:
    first = c["commit"]["message"].splitlines()[0]
    who = (c.get("author") or {}).get("login") or c["commit"]["author"]["name"]
    return (f'<a class="block direct" href="{_e(c["html_url"])}" target="_blank" rel="noopener">'
            f'<div class="title">{_e(first)}</div>'
            f'<div class="meta">{_e(c["sha"][:7])} · {_e(who)} · {_date(c["commit"]["author"]["date"])}'
            f' · direkt auf dev, ohne PR</div></a>')


def render(m: dict) -> str:
    base, head, repo = m["base"], m["head"], m["repo"]
    gh_url = f"https://github.com/{repo}"
    open_to_head = [p for p in m["open"] if p["base"]["ref"] == head]
    open_other = [p for p in m["open"] if p["base"]["ref"] != head]

    open_html = "".join(_pr_block(p, "open", base, head) for p in open_to_head + open_other) \
        or '<div class="empty">Keine offenen Pull Requests.</div>'
    pending_html = "".join(_pr_block(p, "pending", base, head) for p in m["pending"])
    pending_html += "".join(_commit_block(c) for c in m["direct"])
    if not pending_html:
        pending_html = f'<div class="empty">{_e(head)} und {_e(base)} sind gleich. Nichts wartet auf ein Release.</div>'
    released_html = "".join(_pr_block(p, "released", base, head) for p in m["released"]) \
        or '<div class="empty">Noch keine gemergten Pull Requests.</div>'

    rel = m["release"]
    rel_line = (f'Letztes Release <a href="{_e(rel["html_url"])}" target="_blank">{_e(rel["tag_name"])}</a> '
                f'vom {_date(rel["published_at"])}') if rel else "Noch kein Release"
    vb, vh = m["version_base"], m["version_head"]
    version_note = ""
    if m["ahead_by"] and vb and vh and vb == vh:
        version_note = (f'<div class="note">Version auf {_e(head)} ist noch {_e(vh)}. Für das nächste Release '
                        f'die Versionsnummer erhöhen, dann {_e(head)} → {_e(base)} mergen.</div>')
    behind_note = ""
    if m["only_on_base"]:
        behind_note = (f'<div class="note warn">{_e(base)} hat {len(m["only_on_base"])} Commit(s), die nicht auf '
                       f'{_e(head)} sind (z. B. ein Hotfix direkt auf {_e(base)}). '
                       f'<a href="{gh_url}/compare/{_e(head)}...{_e(base)}" target="_blank">ansehen</a></div>')

    n_pending = len(m["pending"]) + len(m["direct"])
    return f"""<!doctype html>
<html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Branch-Überblick {_e(repo)}</title>
<style>
:root {{ --bg:#f6f7f9; --panel:#fff; --text:#1d2330; --muted:#667085; --line:#e3e6eb;
  --open:#3b6fd8; --pending:#d98a1c; --released:#2f9e6a; --direct:#8a5cd6; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#14171c; --panel:#1d2128; --text:#e6e9ee; --muted:#98a2b3;
  --line:#2c323c; --open:#6f9bff; --pending:#f0a840; --released:#4cc38a; --direct:#b18cff; }} }}
* {{ box-sizing:border-box }}
body {{ margin:0; font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif; background:var(--bg); color:var(--text) }}
header {{ padding:20px 24px 8px }}
h1 {{ font-size:20px; margin:0 0 4px }}
header .sub {{ color:var(--muted) }}
header a, .note a {{ color:inherit }}
.flow {{ display:grid; grid-template-columns:1fr auto 1fr auto 1fr; gap:10px; padding:12px 24px 24px; align-items:start }}
.arrow {{ align-self:center; font-size:28px; color:var(--muted); padding-top:30px }}
.lane {{ background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:14px; min-width:0 }}
.lane h2 {{ font-size:15px; margin:0 0 2px; display:flex; gap:8px; align-items:center }}
.lane h2 .count {{ background:var(--c); color:#fff; border-radius:10px; padding:0 8px; font-size:12px }}
.lane .desc {{ color:var(--muted); font-size:12px; margin-bottom:12px }}
.lane.open {{ --c:var(--open) }} .lane.pending {{ --c:var(--pending) }} .lane.released {{ --c:var(--released) }}
.lane {{ border-top:4px solid var(--c) }}
.block {{ display:block; text-decoration:none; color:inherit; border:1px solid var(--line); border-left:6px solid var(--c);
  border-radius:8px; padding:9px 11px; margin-bottom:9px; background:var(--panel) }}
.block:hover {{ box-shadow:0 2px 8px rgba(0,0,0,.12) }}
.block.open {{ --c:var(--open) }} .block.pending {{ --c:var(--pending) }} .block.released {{ --c:var(--released) }}
.block.direct {{ --c:var(--direct) }}
.title {{ font-weight:600 }}
.meta {{ color:var(--muted); font-size:12px; margin-top:3px }}
.badges {{ margin-top:6px; display:flex; flex-wrap:wrap; gap:5px; align-items:center }}
.badge {{ font-size:11px; border-radius:6px; padding:1px 7px; border:1px solid var(--line) }}
.badge.ok {{ background:#2f9e6a22; color:var(--released) }} .badge.bad {{ background:#d92d2022; color:#d92d20 }}
.badge.wait {{ background:#d98a1c22; color:var(--pending) }} .badge.warn {{ background:#d92d2015; color:#d92d20 }}
.badge.draft, .badge.none {{ color:var(--muted) }}
.diff {{ font-size:11px; color:var(--muted) }} .add {{ color:var(--released) }} .del {{ color:#d92d20 }}
.label {{ display:inline-block; font-size:11px; border-radius:10px; padding:0 7px; margin:4px 4px 0 0;
  border:1px solid var(--c); color:var(--text) }}
.empty {{ color:var(--muted); font-style:italic; padding:8px 0 }}
.note {{ margin:0 0 10px; padding:8px 10px; border-radius:8px; background:#d98a1c1a; font-size:12px }}
.note.warn {{ background:#d92d2015 }}
.legend {{ display:flex; gap:14px; flex-wrap:wrap; color:var(--muted); font-size:12px; margin-top:8px }}
.legend span::before {{ content:""; display:inline-block; width:10px; height:10px; border-radius:2px; background:var(--c); margin-right:5px }}
@media (max-width:900px) {{ .flow {{ grid-template-columns:1fr }} .arrow {{ transform:rotate(90deg); padding:0; justify-self:center }} }}
</style></head><body>
<header>
  <h1>Branch-Überblick <a href="{gh_url}" target="_blank">{_e(repo)}</a></h1>
  <div class="sub">{rel_line} · Version {_e(base)}: {_e(vb or "?")} · Version {_e(head)}: {_e(vh or "?")}
    · {_e(head)} ist {m["ahead_by"]} Commit(s) vor {_e(base)}
    · <a href="{gh_url}/compare/{_e(base)}...{_e(head)}" target="_blank">Vergleich auf GitHub</a>
    · Stand {_date(m["fetched"])}</div>
  <div class="legend">
    <span style="--c:var(--open)">offener PR</span><span style="--c:var(--pending)">auf {_e(head)}, nicht released</span>
    <span style="--c:var(--direct)">Commit direkt auf {_e(head)}</span><span style="--c:var(--released)">released auf {_e(base)}</span>
  </div>
</header>
<div class="flow">
  <section class="lane open"><h2>Offene Pull Requests <span class="count">{len(m["open"])}</span></h2>
    <div class="desc">Warten auf Review oder Merge nach {_e(head)}.</div>{open_html}</section>
  <div class="arrow">→</div>
  <section class="lane pending"><h2>Auf {_e(head)}, noch nicht in {_e(base)} <span class="count">{n_pending}</span></h2>
    <div class="desc">Kommt mit dem nächsten Release ({_e(head)} → {_e(base)}).</div>{behind_note}{version_note}{pending_html}</section>
  <div class="arrow">→</div>
  <section class="lane released"><h2>Released auf {_e(base)} <span class="count">{len(m["released"])}</span></h2>
    <div class="desc">Die letzten gemergten PRs, die schon in {_e(base)} sind.</div>{released_html}</section>
</div>
</body></html>"""


# --- CLI ------------------------------------------------------------------------------------------

def generate(repo: str, base: str, head: str, token: str | None) -> str:
    gh = GitHub(repo, token)
    data = fetch(gh, base, head)
    attach_pr_commits(gh, data)
    return render(build_model(data))


def serve(args, token: str | None) -> None:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path not in ("/", "/index.html"):
                self.send_error(404)
                return
            try:
                body = generate(args.repo, args.base, args.head, token).encode("utf-8")
                code = 200
            except Exception as exc:  # shown in the browser, the server keeps running
                body = f"<pre>{_e(exc)}</pre>".encode("utf-8")
                code = 500
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"Branch overview at {url} (reload the page for fresh data, Ctrl+C to stop)")
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description="Blocks of open pull requests, unreleased changes on dev and "
                                            "the last release on main.")
    p.add_argument("--repo", default=DEFAULT_REPO, help="owner/name (default %(default)s)")
    p.add_argument("--base", default="main", help="release branch (default %(default)s)")
    p.add_argument("--head", default="dev", help="development branch (default %(default)s)")
    p.add_argument("--out", help="HTML file to write (default: a file in the temp folder)")
    p.add_argument("--no-open", action="store_true", help="do not open the browser")
    p.add_argument("--serve", action="store_true", help="local server, each reload fetches fresh data")
    p.add_argument("--port", type=int, default=8765, help="port for --serve (default %(default)s)")
    args = p.parse_args(argv)
    token = find_token()
    if not token:
        print("No GitHub token found, using the API without login (60 requests per hour).", file=sys.stderr)
    if args.serve:
        serve(args, token)
        return
    page = generate(args.repo, args.base, args.head, token)
    out = args.out or os.path.join(tempfile.gettempdir(), "hertzon-branch-overview.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(page)
    print(out)
    if not args.no_open:
        webbrowser.open("file://" + os.path.abspath(out))


if __name__ == "__main__":
    main()
