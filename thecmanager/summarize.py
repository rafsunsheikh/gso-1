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
TEMPLATE_VERSION = 2

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
    "has forgotten what this one is. Use only the evidence given.\n"
    "headline: names the thing and what it does. No filler, no trailing period.\n"
    "purpose: two or three plain sentences, what problem it solves and for "
    "whom. Do not restate the headline.\n"
    "stack: the languages, frameworks and services it is built on. Return an "
    "empty list if it is not software, for example a folder of documents. "
    "Never put the repository's own name here.\n"
    "entry_points: where a newcomer starts. A file path, or the command the "
    "project documents for running it, whichever is truer for this repository.\n"
    "caveats: things the reader would be annoyed to discover later, such as a "
    "required setup step, a hard-coded assumption, or two half-finished "
    "versions of the same thing. Returning none is correct and common.\n"
    "confidence: 'high' when a README or manifest states what this is; "
    "'medium' when you inferred it confidently from code and commits; 'low' "
    "only when you are genuinely guessing. Judge the evidence, not your "
    "wording.\n"
    "Never state facts about tests, CI, licences, commit dates or file counts. "
    "Those are measured separately and shown beside your text."
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
    # Every ecosystem states its own identity somewhere. Somebody else's
    # library is Java, Rust, PHP or Elixir, not this one's Python and JS.
    for name in ("pyproject.toml", "setup.py", "requirements.txt", "Pipfile",
                 "go.mod", "Cargo.toml", "Gemfile", "pom.xml", "build.gradle",
                 "build.gradle.kts", "composer.json", "mix.exs", "Package.swift",
                 "pubspec.yaml", "CMakeLists.txt", "Makefile", "*.csproj",
                 "docker-compose.yml", "Dockerfile"):
        if "*" in name:
            match = next(iter(sorted(root.glob(name))), None)
            if match:
                out[match.name] = _read(match, 900)
            continue
        f = root / name
        if f.exists():
            out[name] = _read(f, 900)
    return out


_ENTRY_CANDIDATES = (
    "main.py", "app.py", "run.py", "manage.py", "server.py", "__main__.py",
    "cli.py", "wsgi.py", "asgi.py",
    "index.js", "index.ts", "main.js", "main.ts", "server.js", "app.js",
    "app.ts", "index.tsx", "App.tsx", "app.jsx",
    "main.go", "main.rs", "lib.rs", "Program.cs", "Main.java", "Application.java",
    "index.php", "artisan", "main.swift", "main.dart", "application.ex",
    "main.cpp", "main.c",
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
#
# This has to work on somebody else's machine, with a model we did not choose,
# served by a llama.cpp build we do not control. Three things vary and all
# three broke something during testing:
#
#   * Structured output. Recent llama.cpp constrains sampling to a JSON schema;
#     older builds only honour {"type": "json_object"}; some honour neither.
#   * Thinking. GLM and Qwen reason before answering and `max_tokens` counts
#     that scratchpad, so a model can spend its whole budget thinking and
#     return an empty message. `enable_thinking` switches it off, but it is a
#     Qwen/GLM chat-template argument: Llama, Mistral and Gemma templates do
#     not take it, and some builds reject an unknown argument outright.
#   * Speed. The same summary took 6s and 212s on one machine depending on
#     whether the model had been swapped out.
#
# So nothing is assumed. We probe the server once per model, remember what it
# accepted, and fall back a rung at a time when something is refused.

_THINK = re.compile(r"<think>.*?</think>", re.S)
_CAPS: dict[str, dict] = {}
_caps_lock = threading.Lock()

# Ladder, most constrained first. A model that cannot be constrained at all can
# still be asked nicely and have the object pulled out of its prose.
_MODES = ("schema", "json", "prompt")


def _post(url: str, body: dict, timeout: int) -> tuple[Optional[dict], str]:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode()), ""
    except urllib.error.HTTPError as e:
        detail = e.read()[:300].decode(errors="ignore")
        return None, f"HTTP {e.code}: {detail}"
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as e:
        return None, str(e)


def _body(prompt: str, mode: str, no_think: bool, max_tokens: int,
          model: str) -> dict:
    body: dict = {
        "model": model or "local",
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens": max_tokens,
    }
    if mode == "schema":
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "repo_summary", "schema": SCHEMA},
        }
    elif mode == "json":
        body["response_format"] = {"type": "json_object"}
    if no_think:
        body["chat_template_kwargs"] = {"enable_thinking": False}
    return body


def _extract(text: str) -> Optional[dict]:
    """Pull the object out, whatever the model wrapped it in."""
    text = _THINK.sub("", text or "").strip()
    if not text:
        return None
    # Fenced code, then the first balanced-looking object.
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    start = text.find("{")
    if start < 0:
        return None
    depth, end = 0, None
    for i, ch in enumerate(text[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    try:
        return json.loads(text[start:end] if end else text[start:])
    except ValueError:
        return None


def _valid(obj: object) -> bool:
    """Cheap shape check. The schema is the contract; a fallback rung may miss."""
    if not isinstance(obj, dict):
        return False
    return isinstance(obj.get("headline"), str) and isinstance(obj.get("purpose"), str)


def _coerce(obj: dict) -> dict:
    """Make an unconstrained answer fit the template rather than discarding it."""
    def pairs(v, a, b):
        out = []
        for item in v if isinstance(v, list) else []:
            if isinstance(item, dict) and item.get(a):
                out.append({a: str(item[a])[:120], b: str(item.get(b, ""))[:120]})
            elif isinstance(item, str):
                out.append({a: item[:120], b: ""})
        return out[:6]
    return {
        "headline": str(obj.get("headline", ""))[:100],
        "purpose": str(obj.get("purpose", ""))[:600],
        "stack": pairs(obj.get("stack"), "name", "role"),
        "entry_points": pairs(obj.get("entry_points"), "path", "what")[:3],
        # A smaller model answers "none" as a string rather than returning an
        # empty list, and rendering a caveat that reads "None" is worse than
        # rendering no caveats at all.
        "caveats": [str(c)[:160] for c in (obj.get("caveats") or [])
                    if isinstance(c, (str, int, float))
                    and str(c).strip().lower().rstrip(".")
                    not in ("", "none", "n/a", "na", "null", "no caveats",
                            "nothing", "not applicable")][:4],
        "confidence": obj.get("confidence") if obj.get("confidence") in
        ("high", "medium", "low") else "medium",
    }


def probe(force: bool = False) -> dict:
    """Work out what this server and model actually support. Once per model.

    Costs one tiny request. Worth it: the alternative is discovering on repo 40
    of 262 that this model ignores `response_format`, and having 39 useless
    summaries.
    """
    st = llm.status()
    if st.get("state") != "running":
        return {"ok": False, "reason": "the local model is not running"}
    key = f"{st.get('url')}|{st.get('model')}"
    with _caps_lock:
        if not force and key in _CAPS:
            return _CAPS[key]

    url = f"{st['url']}/v1/chat/completions"
    model = st.get("model") or "local"
    caps = {"ok": False, "mode": "prompt", "no_think": False, "model": model,
            "reason": ""}

    probe_prompt = ('Reply with this exact object and nothing else: '
                    '{"headline":"ok","purpose":"ok","stack":[],'
                    '"entry_points":[],"caveats":[],"confidence":"high"}')

    for no_think in (True, False):
        for mode in _MODES:
            body = _body(probe_prompt, mode, no_think, 300, model)
            payload, err = _post(url, body, timeout=120)
            if payload is None:
                # A rejected argument is information: stop offering it.
                if no_think and "chat_template" in err.lower():
                    break
                continue
            try:
                choice = payload["choices"][0]
                text = choice["message"].get("content") or ""
            except (KeyError, IndexError):
                continue
            if _valid(_extract(text)):
                caps.update(ok=True, mode=mode, no_think=no_think)
                # Whether the model reasons at all, so callers can budget for it.
                caps["thinks"] = bool(choice["message"].get("reasoning_content")
                                      or "<think>" in text)
                with _caps_lock:
                    _CAPS[key] = caps
                return caps
    caps["reason"] = "the model would not return a usable object"
    with _caps_lock:
        _CAPS[key] = caps
    return caps


def _complete(prompt: str, timeout: int) -> tuple[Optional[dict], str]:
    """One summary, degrading a rung at a time rather than failing outright."""
    st = llm.status()
    if st.get("state") != "running":
        return None, "The local model is not running."
    caps = probe()
    if not caps.get("ok"):
        return None, caps.get("reason") or "the model could not be used"

    url = f"{st['url']}/v1/chat/completions"
    model = caps["model"]
    attempts = [(caps["mode"], caps["no_think"], 1500)]
    # A thinking model that will not be silenced needs room for the scratchpad
    # *and* the answer, so the retry raises the ceiling rather than repeating.
    if caps.get("thinks") or not caps["no_think"]:
        attempts.append((caps["mode"], caps["no_think"], 4000))
    for mode in _MODES[_MODES.index(caps["mode"]) + 1:]:
        attempts.append((mode, caps["no_think"], 4000))

    last = ""
    for mode, no_think, budget in attempts:
        payload, err = _post(url, _body(prompt, mode, no_think, budget, model), timeout)
        if payload is None:
            last = err
            continue
        try:
            choice = payload["choices"][0]
            text = choice["message"].get("content") or ""
        except (KeyError, IndexError):
            last = "llama-server returned no message"
            continue
        obj = _extract(text)
        if _valid(obj):
            return _coerce(obj), ""
        if choice.get("finish_reason") == "length":
            used = (payload.get("usage") or {}).get("completion_tokens", "?")
            last = (f"the model used all {used} tokens without answering "
                    "(it is reasoning at length; a larger budget was tried)")
        else:
            last = f"could not read a summary from: {text[:160]}"
    return None, last


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


def _revision(name: str) -> str:
    """What this summary was written from: the commit, or the evidence itself."""
    root = scanner.app_path(name)
    if git_ops.is_repo(root):
        rc, sha = git_ops._run(root, "rev-parse", "--short", "HEAD")
        if rc == 0 and sha:
            return sha.strip()
    return ""


def timeout_for(name: str) -> int:
    """How long to wait, scaled to how slow this machine has proven to be.

    A summary took 6 seconds on a warm Apple Silicon machine and 212 on the
    same machine while the model was swapping. A fixed timeout is either too
    short for somebody on CPU or so long that a hung server looks like a slow
    one, so it is derived from what we have actually measured here.
    """
    seen = [r.get("seconds", 0) for r in _load().values() if r.get("seconds")]
    if not seen:
        return 900                      # generous until we know anything
    seen.sort()
    typical = seen[len(seen) // 2]
    return int(max(180, min(1800, typical * 8)))


def count_cached() -> int:
    """How many repos already have a summary for the current template."""
    return sum(1 for r in _load().values()
               if r.get("template") == TEMPLATE_VERSION)


def throughput() -> dict:
    """Measured seconds per summary on THIS machine, for honest estimates."""
    seen = sorted(r.get("seconds", 0) for r in _load().values() if r.get("seconds"))
    if not seen:
        return {"samples": 0, "median_seconds": None}
    return {"samples": len(seen), "median_seconds": round(seen[len(seen) // 2], 1)}


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
    rec = dict(rec)
    head = _revision(name)
    # Plenty of project folders are not repositories. Those fall back to a hash
    # of the evidence, so they still cache and still go stale, just on content
    # rather than on commits.
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

    sha = _revision(name)
    existing = get(name)
    if existing and not force and not existing["stale"] and not existing["outdated_template"]:
        return {"ok": True, "cached": True, "record": existing}

    ev = evidence(name)
    ev_hash = evidence_hash(ev)
    # Not a repository: the evidence itself is the revision, so a folder that
    # has not changed is not summarised twice.
    sha = sha or ev_hash

    # The commit moved but nothing we feed the model changed: a version bump, a
    # README typo, a file we never look at. Re-stamp instead of spending 30s.
    if existing and not force and existing.get("evidence_hash") == ev_hash \
            and not existing["outdated_template"]:
        rec = put(name, existing["summary"], sha, ev_hash,
                  existing.get("seconds", 0), existing.get("model", ""))
        return {"ok": True, "cached": True, "restamped": True, "record": rec}

    t0 = time.time()
    summary, err = _complete(_prompt(ev), timeout_for(name))
    if summary is None:
        return {"ok": False, "message": err}
    rec = put(name, summary, sha, ev_hash, time.time() - t0,
              llm.status().get("model") or "")
    return {"ok": True, "cached": False, "record": rec}
