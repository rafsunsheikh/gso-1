#!/usr/bin/env python3
"""Build the published site from the things that are already true.

The landing page used to be stamped with `git tag | head -1` in CI, on a
checkout that does not fetch tags, so it silently fell back to v0.1.0 and told
every visitor to download a version four releases old for weeks. Anything the
site claims about a release is derived here instead, from files in the repo:

    CHANGELOG.md          what changed, per version   -> docs/releases.html
    desktop/package.json  the version being shipped   -> {{VERSION}} everywhere

Run it with no arguments from anywhere in the checkout:

    python3 scripts/build_docs.py            # write the site
    python3 scripts/build_docs.py --check    # fail if the site is out of date

The --check mode is what keeps this honest: it is the difference between "we
remembered to regenerate" and "it cannot be forgotten".
"""
from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CHANGELOG = REPO / "CHANGELOG.md"
DOCS = REPO / "docs"
INDEX_SRC = DOCS / "index.html"
RELEASES_OUT = DOCS / "releases.html"

REPO_URL = "https://github.com/rafsunsheikh/gso-1"

# "## [0.1.4] — 2026-08-26" and the older "## [0.1.0], 2026-08-25"
_H2 = re.compile(r"^##\s+\[([^\]]+)\]\s*[—,-]?\s*(.*)$")
_H3 = re.compile(r"^###\s+(.+)$")
_LI = re.compile(r"^[-*]\s+(.*)$")
_LINKDEF = re.compile(r"^\[[^\]]+\]:\s")


# --------------------------------------------------------------- changelog

def parse_changelog(text: str) -> list[dict]:
    """CHANGELOG.md -> [{version, date, intro, sections:[{name, items}]}].

    Deliberately small: it understands exactly the shape this file already
    uses, and anything it does not understand it keeps as prose rather than
    dropping. Losing a line from a changelog on the way to the website is worse
    than rendering it plainly.
    """
    releases: list[dict] = []
    cur: dict | None = None
    section: dict | None = None
    buf: list[str] = []          # the current bullet, which may wrap lines

    def flush_item() -> None:
        nonlocal buf
        if buf and section is not None:
            section["items"].append(" ".join(s.strip() for s in buf).strip())
        buf = []

    for raw in text.splitlines():
        line = raw.rstrip()

        m = _H2.match(line)
        if m:
            flush_item()
            version, date = m.group(1).strip(), m.group(2).strip()
            cur = {"version": version, "date": date, "intro": [], "sections": []}
            releases.append(cur)
            section = None
            continue

        if cur is None or _LINKDEF.match(line):
            continue             # preamble, and the reference links at the end

        m = _H3.match(line)
        if m:
            flush_item()
            section = {"name": m.group(1).strip(), "items": []}
            cur["sections"].append(section)
            continue

        m = _LI.match(line)
        if m:
            flush_item()
            if section is None:  # bullets before any ### still belong somewhere
                section = {"name": "", "items": []}
                cur["sections"].append(section)
            buf = [m.group(1)]
            continue

        if not line.strip():
            flush_item()
            continue

        if buf:
            buf.append(line)     # continuation of the bullet above
        elif section is None:
            cur["intro"].append(line.strip())

    flush_item()
    return releases


# ------------------------------------------------------------------ render

def md_inline(s: str) -> str:
    """The narrow slice of Markdown the changelog actually uses."""
    out = html.escape(s, quote=False)
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r'<a href="\2">\1</a>', out)
    return out


# Section names carry a tone: what is new, what broke, what went away. Colouring
# them means the shape of a release is readable before a word of it is.
TONES = {
    "added": "ok", "security": "ok",
    "fixed": "warn",
    "changed": "accent", "documentation": "accent",
    "removed": "bad",
}


def tone_for(name: str) -> str:
    key = name.strip().lower()
    for prefix, tone in TONES.items():
        if key.startswith(prefix):
            return tone
    return "accent"


def render_release(rel: dict, version_now: str) -> str:
    ver = rel["version"]
    unreleased = ver.lower() == "unreleased"
    tag = f"v{ver}" if not unreleased else ""
    anchor = "unreleased" if unreleased else f"v{ver}"

    head = [f'<article class="rel" id="{anchor}">']
    head.append('<div class="rel-head">')
    if unreleased:
        head.append('<h2 class="rel-v">Unreleased</h2>')
        head.append('<span class="rel-tag rel-tag-soft">in progress</span>')
    else:
        head.append(f'<h2 class="rel-v">{html.escape(ver)}</h2>')
        if rel["date"]:
            head.append(f'<span class="rel-date">{html.escape(rel["date"])}</span>')
        if tag == version_now:
            head.append('<span class="rel-tag rel-tag-now">current</span>')
        head.append(
            f'<a class="rel-dl" href="{REPO_URL}/releases/tag/{tag}">Downloads &rarr;</a>')
    head.append("</div>")

    body: list[str] = []
    if rel["intro"]:
        body.append(f'<p class="rel-intro">{md_inline(" ".join(rel["intro"]))}</p>')

    for sec in rel["sections"]:
        if not sec["items"]:
            continue
        if sec["name"]:
            body.append(
                f'<h3 class="rel-s" data-tone="{tone_for(sec["name"])}">'
                f'{html.escape(sec["name"])}</h3>')
        body.append('<ul class="rel-list">')
        for item in sec["items"]:
            body.append(f"<li>{md_inline(item)}</li>")
        body.append("</ul>")

    if not body:
        body.append('<p class="rel-intro">Nothing yet.</p>')

    return "\n".join(head + body + ["</article>"])


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Release notes &middot; GSO-1</title>
<meta name="description" content="Every GSO-1 release: what was added, what was fixed, what changed, and what was removed.">
<meta property="og:title" content="GSO-1 release notes">
<meta property="og:description" content="Every GSO-1 release, what changed in it, and where to download it.">
<meta property="og:type" content="website">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>&#128193;</text></svg>">
<style>
:root{
  --bg:#07070c; --bg-2:#0b0b13; --panel:#11111c; --panel-2:#161624;
  --line:#23233a; --line-2:#2e2e4a;
  --ink:#eef0f6; --dim:#9ea6bd; --faint:#6b7390;
  --accent:#6f6af8; --accent-2:#9b97ff;
  --ok:#3ddc97; --warn:#f5b642; --bad:#ff6b6b;
  --mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace;
  --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Inter,Roboto,sans-serif;
  --maxw:820px;
}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--sans);
  font-size:16px;line-height:1.65;-webkit-font-smoothing:antialiased}
.wrap{max-width:var(--maxw);margin:0 auto;padding:0 24px}
a{color:var(--accent-2);text-decoration:none}
a:hover{text-decoration:underline}
code{font-family:var(--mono);font-size:.87em;background:var(--panel-2);
  border:1px solid var(--line);border-radius:5px;padding:1px 5px;color:#cfd3e6}

.topbar{position:fixed;inset:0 0 auto 0;z-index:50;height:52px;display:flex;
  align-items:center;justify-content:center;background:rgba(7,7,12,.72);
  backdrop-filter:blur(12px);border-bottom:1px solid var(--line)}
.topbar .wrap{display:flex;align-items:center;justify-content:space-between;width:100%}
.brand{display:flex;align-items:center;gap:9px;font-weight:700;letter-spacing:-.01em}
.brand .dot{width:9px;height:9px;border-radius:50%;background:var(--ok);
  box-shadow:0 0 0 4px rgba(61,220,151,.14)}
.topnav{display:flex;gap:22px;font-size:.85rem;color:var(--dim)}
.topnav a{color:var(--dim)}
.topnav a:hover{color:var(--ink);text-decoration:none}
.topnav a[aria-current]{color:var(--ink)}
@media(max-width:720px){.topnav{display:none}}

header.head{padding:118px 0 34px;border-bottom:1px solid var(--line)}
.eyebrow{font:600 11px var(--mono);letter-spacing:.16em;text-transform:uppercase;
  color:var(--faint);display:block;margin-bottom:12px}
h1{margin:0 0 10px;font-size:clamp(30px,5vw,42px);letter-spacing:-.02em}
.lede{margin:0;color:var(--dim);max-width:60ch}

main{padding:10px 0 90px}
.rel{padding:36px 0;border-bottom:1px solid var(--line)}
.rel:last-child{border-bottom:0}
.rel-head{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;margin-bottom:6px}
.rel-v{margin:0;font-size:1.5rem;letter-spacing:-.02em}
.rel-date{font:500 12px var(--mono);color:var(--faint)}
.rel-tag{font:600 10px var(--mono);letter-spacing:.1em;text-transform:uppercase;
  padding:3px 8px;border-radius:999px;border:1px solid var(--line-2)}
.rel-tag-now{color:var(--ok);border-color:rgba(61,220,151,.4);background:rgba(61,220,151,.1)}
.rel-tag-soft{color:var(--faint)}
.rel-dl{margin-left:auto;font-size:.85rem;white-space:nowrap}
.rel-intro{color:var(--dim);margin:10px 0 0;max-width:70ch}
.rel-s{font:600 11px var(--mono);letter-spacing:.14em;text-transform:uppercase;
  margin:24px 0 8px;color:var(--accent-2)}
.rel-s[data-tone="ok"]{color:var(--ok)}
.rel-s[data-tone="warn"]{color:var(--warn)}
.rel-s[data-tone="bad"]{color:var(--bad)}
.rel-list{margin:0;padding-left:20px}
.rel-list li{margin:0 0 9px;color:var(--dim);max-width:74ch}
.rel-list li strong{color:var(--ink);font-weight:600}

.foot{padding:30px 0 60px;color:var(--faint);font-size:.85rem;border-top:1px solid var(--line)}
.foot a{color:var(--dim)}
</style>
</head>
<body>

<div class="topbar">
  <div class="wrap">
    <span class="brand"><span class="dot"></span> GSO-1</span>
    <nav class="topnav">
      <a href="./index.html#idea">The idea</a>
      <a href="./index.html#does">What it does</a>
      <a href="./ops-room.html">Ops Room</a>
      <a href="./index.html#install">Install</a>
      <a href="./releases.html" aria-current="page">Releases</a>
      <a href="%%REPO%%">GitHub</a>
    </nav>
  </div>
</div>

<header class="head">
  <div class="wrap">
    <span class="eyebrow">Change of orders</span>
    <h1>Release notes</h1>
    <p class="lede">Every version of GSO-1, what it added, what it fixed, and what it took
      away. The newest release is <strong>%%VERSION%%</strong>,
      <a href="%%REPO%%/releases/latest">available for macOS, Windows and Linux</a>.</p>
  </div>
</header>

<main>
  <div class="wrap">
%%RELEASES%%
  </div>
</main>

<div class="foot">
  <div class="wrap">
    Generated from <a href="%%REPO%%/blob/main/CHANGELOG.md">CHANGELOG.md</a>.
    &middot; <a href="./index.html">Back to the front page</a>
  </div>
</div>

</body>
</html>
"""


def current_version() -> str:
    """The version being shipped, from the file the installer is built with."""
    data = json.loads((REPO / "desktop" / "package.json").read_text())
    return "v" + str(data["version"])


def build_releases_page(version: str) -> str:
    releases = parse_changelog(CHANGELOG.read_text())
    # An empty "Unreleased" is scaffolding, not news; do not publish it.
    shown = [r for r in releases
             if r["version"].lower() != "unreleased" or r["sections"]]
    body = "\n\n".join(render_release(r, version) for r in shown)
    return (PAGE.replace("%%RELEASES%%", body)
                .replace("%%VERSION%%", html.escape(version))
                .replace("%%REPO%%", REPO_URL))


def stamp(text: str, version: str) -> str:
    """Fill the version placeholders. {{VERSION}} is the tag, {{VER}} the number."""
    return (text.replace("{{VERSION}}", version)
                .replace("{{VER}}", version.lstrip("v"))
                .replace("{{DL}}", f"{REPO_URL}/releases/download/{version}"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="exit non-zero if the generated page is out of date")
    ap.add_argument("--out", default=str(DOCS),
                    help="directory to write the built site into")
    args = ap.parse_args()

    version = current_version()
    page = build_releases_page(version)

    if args.check:
        if not RELEASES_OUT.exists():
            print("docs/releases.html is missing; run scripts/build_docs.py", file=sys.stderr)
            return 1
        if RELEASES_OUT.read_text() != page:
            print("docs/releases.html is stale; run scripts/build_docs.py", file=sys.stderr)
            return 1
        print(f"docs are up to date ({version})")
        return 0

    out = Path(args.out)
    if out.resolve() != DOCS.resolve():
        # Copy the whole tree first, images and all, then overwrite the pages
        # that need stamping. Writing only the HTML would publish a site whose
        # assets/ is missing.
        if out.exists():
            shutil.rmtree(out)
        shutil.copytree(DOCS, out)
        # index.html keeps its placeholders in the repo and is stamped on the
        # way out, so a stale version can never be committed by hand.
        for name in ("index.html", "ops-room.html"):
            src = DOCS / name
            if src.exists():
                (out / name).write_text(stamp(src.read_text(), version))
    out.mkdir(parents=True, exist_ok=True)
    (out / "releases.html").write_text(page)

    n = len(parse_changelog(CHANGELOG.read_text()))
    print(f"built docs/releases.html: {n} entries, current {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
