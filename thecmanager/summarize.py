"""Explain what a repo is, using the local model.

Half the library detects as `unknown`: no type, no language, no start command,
and for 78 of them no README either. The folder name is all you get. This
module reads the cheap, reliable things about a repo, hands them to the local
model, and asks only for the part a machine cannot work out, what the thing is
*for*.

Two rules keep the output trustworthy:

* **Facts come from code, judgement comes from the model.** Whether there are
  tests, whether CI is wired up, when the last commit landed, how many files
  there are, these are computed here and rendered directly. Asking a model to
  report them invites a confident wrong answer about something we already know.
* **The model fills a schema, it does not write a document.** llama.cpp
  constrains generation to the JSON schema below, so every summary has the same
  shape whatever the model felt like producing. Free prose drifts; a schema
  cannot.

Summaries are cached in `data/summaries.json`, keyed by commit, so the cost is
paid once per repo per change rather than once per time somebody opens a drawer.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from . import config, detector, git_ops, llm, scanner

# Bumping this regenerates every summary. Change it whenever the schema or the
# prompt changes in a way that makes old output the wrong shape or worse.
TEMPLATE_VERSION = 1

_FILE = config.DATA_DIR / "summaries.json"
_lock = threading.RLock()

# Directories that tell you nothing about a project and would swamp the tree.
_SKIP_DIRS = {
    ".git", "node_modules", ".venv", "venv", "env", "__pycache__", ".next",
    "dist", "build", "target", ".pytest_cache", ".mypy_cache", "vendor",
    ".idea", ".vscode", "coverage", ".gradle", "Pods", ".terraform",
}
_TREE_LIMIT = 120
_README_CHARS = 2000
_HEAD_LINES = 40


# --------------------------------------------------------------- the schema

# `layout` was in the first draft and cut: it restated the file tree the reader
# can already see. `caveats` stays because it is the only field that tells you
# something opening the folder would not.
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["headline", "purpose", "stack", "entry_points", "caveats", "confidence"],
    "properties": {
        "headline": {"type": "string", "maxLength": 100},
        "purpose": {"type": "string", "maxLength": 600},
        "stack": {
            "type": "array", "maxItems": 6,
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["name", "role"],
                "properties": {"name": {"type": "string", "maxLength": 40},
                               "role": {"type": "string", "maxLength": 60}},
            },
        },
        "entry_points": {
            "type": "array", "maxItems": 3,
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["path", "what"],
                "properties": {"path": {"type": "string", "maxLength": 120},
                               "what": {"type": "string", "maxLength": 120}},
            },
        },
        "caveats": {"type": "array", "maxItems": 4,
                    "items": {"type": "string", "maxLength": 160}},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
}

SYSTEM = (
    "You summarise a source repository for a developer who has 200 of them and "
    "has forgotten what this one is. Use only the evidence given. "
    "Never state facts about tests, CI, licences, commit dates or file counts: "
    "those are measured separately and shown next to your text. "
    "The headline names the thing and what it does, with no filler and no "
    "trailing period. Purpose is two or three plain sentences: what problem it "
    "solves and for whom. Caveats are things the reader would be annoyed to "
    "discover later, and it is correct to return none. "
    "If the evidence is thin, say so with confidence 'low' and keep the "
    "headline cautious rather than inventing a purpose."
)


# ------------------------------------------------------------ the evidence

def _walk(root: Path, limit: int = _TREE_LIMIT) -> tuple[list[str], int]:
    """A depth-first listing with vendor directories pruned, and a file count."""
    entries: list[str] = []
    total = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames
                             if d not in _SKIP_DIRS and not d.startswith("."))
        rel = Path(dirpath).relative_to(root)
        total += len(filenames)
        for f in sorted(filenames):
            if f.startswith("."):
                continue
            if len(entries) < limit:
                entries.append(str(rel / f) if str(rel) != "." else f)
        if total > 20000:      # a pathological tree must not stall the sweep
            break
    return entries, total


def _read(path: Path, chars: int) -> str:
    try:
        return path.read_text(errors="ignore")[:chars]
    except OSError:
        return ""


def _manifest(root: Path) -> dict:
    """The declared identity of a project, from whichever manifest it uses."""
    out: dict = {}
    pkg = root / "package.json"
    if pkg.exists():
        try:
            d = json.loads(pkg.read_text(errors="ignore"))
            out["package.json"] = {
                "name": d.get("name"), "description": d.get("description"),
                "scripts": list((d.get("scripts") or {}).items())[:10],
                "dependencies": list((d.get("dependencies") or {}))[:20],
            }
        except (ValueError, OSError):
            pass
    for name in ("pyproject.toml", "requirements.txt", "go.mod", "Cargo.toml",
                 "Gemfile", "pom.xml", "composer.json"):
        f = root / name
        if f.exists():
            out[name] = _read(f, 900)
    return out


_ENTRY_CANDIDATES = (
    "main.py", "app.py", "run.py", "manage.py", "server.py", "__main__.py",
    "index.js", "index.ts", "main.js", "main.ts", "server.js", "app.js",
    "main.go", "main.rs", "Program.cs", "index.php",
)


def _entry_heads(root: Path, tree: list[str]) -> dict:
    """The first lines of up to three plausible entry points."""
    picked: list[str] = []
    for rel in tree:
        if Path(rel).name in _ENTRY_CANDIDATES and rel.count("/") <= 2:
            picked.append(rel)
        if len(picked) >= 3:
            break
    out = {}
    for rel in picked:
        text = _read(root / rel, 4000)
        out[rel] = "\n".join(text.splitlines()[:_HEAD_LINES])
    return out


def _commits(root: Path, n: int = 10) -> list[str]:
    """Recent commit subjects: the cheapest strong signal for what a repo is for."""
    rc, out = git_ops._run(root, "log", f"-{n}", "--pretty=%s")
    return [l.strip() for l in out.splitlines() if l.strip()] if rc == 0 else []


def signals(name: str) -> dict:
    """The measured facts. Rendered as-is; never asked of the model."""
    root = scanner.app_path(name)
    tree, total = _walk(root, limit=4000)
    lower = [t.lower() for t in tree]

    def any_match(*frags: str) -> bool:
        return any(any(f in t for f in frags) for t in lower)

    licence = None
    for cand in ("LICENSE", "LICENSE.md", "LICENCE", "COPYING"):
        if (root / cand).exists():
            head = _read(root / cand, 400)
            for tag, label in (("Apache License", "Apache-2.0"), ("MIT License", "MIT"),
                               ("GNU GENERAL PUBLIC", "GPL"), ("BSD ", "BSD"),
                               ("Mozilla Public", "MPL")):
                if tag.lower() in head.lower():
                    licence = label
                    break
            licence = licence or "present"
            break

    rc, authors = git_ops._run(root, "shortlog", "-sn", "--all", "--no-merges")
    contributors = len([l for l in authors.splitlines() if l.strip()]) if rc == 0 else 0
    rc, when = git_ops._run(root, "log", "-1", "--pretty=%cr")

    return {
        "files": total,
        "tests": any_match("test/", "tests/", "_test.", ".test.", "spec/", "__tests__"),
        "ci": (root / ".github" / "workflows").is_dir() or any_match(
            ".gitlab-ci", "jenkinsfile", ".circleci"),
        # Anywhere in the tree, not just the root: a monorepo pins its
        # dependencies inside each package and the root has no lockfile at all.
        "lockfile": any_match(
            "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock",
            "uv.lock", "cargo.lock", "go.sum", "gemfile.lock"),
        "docker": (root / "Dockerfile").exists() or (root / "docker-compose.yml").exists(),
        "readme": any((root / f).exists() for f in (
            "README.md", "readme.md", "README.rst", "README.txt")),
        "license": licence,
        "contributors": contributors,
        "last_commit": when or None,
    }


def evidence(name: str) -> dict:
    """Everything the model is allowed to see about one repo."""
    root = scanner.app_path(name)
    tree, total = _walk(root)
    det = detector.detect(root)
    readme = ""
    for cand in ("README.md", "readme.md", "README.rst", "README.txt"):
        if (root / cand).exists():
            readme = _read(root / cand, _README_CHARS)
            break
    return {
        "name": name,
        "detected": {k: det.get(k) for k in ("type", "language", "start_command")},
        "readme": readme,
        "manifests": _manifest(root),
        "tree": tree,
        "file_count": total,
        "recent_commits": _commits(root),
        "entry_heads": _entry_heads(root, tree),
    }


def evidence_hash(ev: dict) -> str:
    blob = json.dumps(ev, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


def _prompt(ev: dict) -> str:
    parts = [f"Repository: {ev['name']}",
             f"Auto-detected: {json.dumps(ev['detected'])}",
             f"Files (excluding vendor directories): {ev['file_count']}"]
    if ev["readme"]:
        parts.append("README (truncated):\n" + ev["readme"])
    else:
        parts.append("README: none.")
    if ev["manifests"]:
        parts.append("Manifests:\n" + json.dumps(ev["manifests"], indent=1)[:2500])
    if ev["recent_commits"]:
        parts.append("Recent commit subjects:\n- " + "\n- ".join(ev["recent_commits"]))
    parts.append("File tree (partial):\n" + "\n".join(ev["tree"]))
    for rel, head in (ev["entry_heads"] or {}).items():
        parts.append(f"Head of {rel}:\n{head}")
    return "\n\n".join(parts)


# ------------------------------------------------------------- the model

_THINK = re.compile(r"<think>.*?</think>", re.S)


def _chat(prompt: str, timeout: int = 300) -> tuple[Optional[dict], str]:
    """Ask llama-server for one schema-shaped object.

    Talks to llama-server's OpenAI endpoint directly rather than through GSO-1's
    Anthropic shim: the shim exists so Claude Code can drive a local model, and
    routing through it here would add a translation layer for no benefit and
    lose the schema constraint on the way.
    """
    st = llm.status()
    if st.get("state") != "running":
        return None, "The local model is not running."

    body = {
        "model": st.get("model") or "local",
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": prompt}],
        "temperature": 0.3,
        # Generous, because the cap counts everything the model emits. See below.
        "max_tokens": 1500,
        # GLM-4.7 and friends think before they answer, and `max_tokens` counts
        # those tokens too. Left on, the model spent its entire budget
        # reasoning and returned `finish_reason: length` with an EMPTY message:
        # four of five test repos failed that way, 3946 reasoning tokens and
        # nothing else. There is nothing to think about here, the schema
        # dictates the shape, so turn it off. Doing so cut a summary from 900
        # tokens of scratchpad to 253 tokens of answer.
        "chat_template_kwargs": {"enable_thinking": False},
        # llama.cpp constrains sampling to the schema, so the shape is
        # guaranteed rather than hoped for.
        "response_format": {"type": "json_schema",
                            "json_schema": {"name": "repo_summary", "schema": SCHEMA}},
    }
    req = urllib.request.Request(
        f"{st['url']}/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return None, f"llama-server said {e.code}: {e.read()[:200].decode(errors='ignore')}"
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as e:
        return None, f"llama-server unreachable: {e}"

    try:
        choice = payload["choices"][0]
        text = choice["message"].get("content") or ""
    except (KeyError, IndexError):
        return None, "llama-server returned no message."

    # Name the real failure. "could not parse" sent me looking at the JSON when
    # the model had simply run out of budget before writing any.
    if not text.strip():
        if choice.get("finish_reason") == "length":
            used = (payload.get("usage") or {}).get("completion_tokens", "?")
            return None, (f"the model used all {used} tokens without answering; "
                          "raise max_tokens or keep thinking disabled")
        return None, "the model returned an empty message."

    # A thinking model may still emit its scratchpad ahead of the object when
    # reasoning_format leaves it inline.
    text = _THINK.sub("", text).strip()
    start = text.find("{")
    if start > 0:
        text = text[start:]
    try:
        return json.loads(text), ""
    except ValueError:
        return None, f"could not parse a summary from: {text[:200]}"


# -------------------------------------------------------------- the cache

def _load() -> dict:
    try:
        d = json.loads(_FILE.read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=1))
    tmp.replace(_FILE)


def get(name: str) -> Optional[dict]:
    """The cached summary for a repo, annotated with whether it still applies.

    A summary is not invalidated by uncommitted work: you would never hold a
    stable one while actually editing. It goes stale when the commit it was
    written from is no longer HEAD, and a stale summary is still shown, marked,
    rather than hidden.
    """
    with _lock:
        rec = _load().get(name)
    if not rec:
        return None
    st = git_ops.status(scanner.app_path(name))
    head = (st.get("last_commit") or "").split(" ")[0] if st.get("is_repo") else ""
    rec = dict(rec)
    rec["stale"] = bool(head and rec.get("sha") and head != rec["sha"])
    rec["outdated_template"] = rec.get("template") != TEMPLATE_VERSION
    return rec


def put(name: str, summary: dict, sha: str, ev_hash: str, seconds: float,
        model: str) -> dict:
    rec = {
        "sha": sha, "template": TEMPLATE_VERSION, "model": model,
        "generated_at": time.time(), "seconds": round(seconds, 1),
        "evidence_hash": ev_hash, "summary": summary,
    }
    with _lock:
        data = _load()
        data[name] = rec
        _save(data)
    return rec


def generate(name: str, force: bool = False) -> dict:
    """Summarise one repo, reusing the cache where it still applies."""
    root = scanner.app_path(name)
    if not root.is_dir():
        return {"ok": False, "message": f"No such repo: {name}"}

    st = git_ops.status(root)
    sha = (st.get("last_commit") or "").split(" ")[0] if st.get("is_repo") else ""

    existing = get(name)
    if existing and not force and not existing["stale"] and not existing["outdated_template"]:
        return {"ok": True, "cached": True, "record": existing}

    ev = evidence(name)
    ev_hash = evidence_hash(ev)

    # The commit moved but nothing we feed the model changed: a version bump, a
    # README typo, a file we never look at. Re-stamp instead of spending 30s.
    if existing and not force and existing.get("evidence_hash") == ev_hash \
            and not existing["outdated_template"]:
        rec = put(name, existing["summary"], sha, ev_hash,
                  existing.get("seconds", 0), existing.get("model", ""))
        return {"ok": True, "cached": True, "restamped": True, "record": rec}

    t0 = time.time()
    summary, err = _chat(_prompt(ev))
    if summary is None:
        return {"ok": False, "message": err}
    rec = put(name, summary, sha, ev_hash, time.time() - t0,
              llm.status().get("model") or "")
    return {"ok": True, "cached": False, "record": rec}
