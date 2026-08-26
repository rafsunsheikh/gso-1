"""First-run help for the local model: install llama.cpp, fetch a GGUF.

GSO-1 can drive a local model but could not, until now, get you to the point of
having one. Somebody arriving without llama.cpp and without a `.gguf` file was
shown a red line naming an environment variable for a binary they did not have,
and an empty list. This module closes that gap:

* `brew_plan()` / `install_llama()` — find the package manager and run the one
  command that installs `llama-server`, streaming its output so a failure is
  readable rather than a spinner that stops.
* `catalog()` — a short list of models that are actually worth running as a
  Claude Code backend, each resolved against the Hugging Face API at call time
  so the sizes are real and an entry that no longer exists disappears instead
  of 404ing halfway through a 17 GB download.
* `download()` — fetch one, resumably, into the folder GSO-1 already scans.

Nothing here runs on import, and nothing runs without an explicit request: this
installs software and writes gigabytes to disk, which is the user's call every
time, not a side effect of opening a tab.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterator, Optional

from . import config, llm

_HF_API = "https://huggingface.co/api/models"
_HF_FILE = "https://huggingface.co/{repo}/resolve/main/{file}"
_UA = {"User-Agent": "GSO-1"}

# The seed list. Kept as data, and every entry is checked against Hugging Face
# before it is shown, because a curated list that rots into dead links is worse
# than no list: it fails at the slowest possible moment. `quant` is matched as a
# substring against the repo's real filenames, so a repo that renames its files
# still resolves as long as the quant label survives.
_CATALOG = [
    {
        "id": "qwen3-4b",
        "repo": "unsloth/Qwen3-4B-Instruct-2507-GGUF",
        "quant": "Q4_K_M",
        "label": "Qwen3 4B Instruct",
        "note": "Smallest one worth using for tools. Fits comfortably on 16 GB.",
    },
    {
        "id": "qwen3-30b",
        "repo": "unsloth/Qwen3-30B-A3B-Instruct-2507-GGUF",
        "quant": "UD-Q4_K_XL",
        "label": "Qwen3 30B A3B",
        "note": "Mixture-of-experts: 30B of weights, a fraction active per token.",
    },
    {
        "id": "glm-4.7-flash",
        "repo": "unsloth/GLM-4.7-Flash-GGUF",
        "quant": "UD-Q4_K_XL",
        "label": "GLM-4.7 Flash",
        "note": "Strong tool-calling for its size.",
    },
    {
        "id": "qwen3.6-35b",
        "repo": "unsloth/Qwen3.6-35B-A3B-GGUF",
        "quant": "UD-Q4_K_XL",
        "label": "Qwen3.6 35B A3B",
        "note": "The most capable of these; wants 32 GB or more.",
    },
]

_CATALOG_TTL = 900  # seconds; the HF API is not free and the list barely moves
_cache: dict = {"at": 0.0, "items": []}
_cache_lock = threading.Lock()


# ------------------------------------------------------------------ llama.cpp

def _brew() -> Optional[str]:
    return shutil.which("brew")


def install_plan() -> dict:
    """How this machine would install llama.cpp, if it can.

    Reports rather than acts. The UI needs to know whether to offer a button or
    a command to copy, and saying "run brew" on a machine without brew is the
    same unhelpful dead end this module exists to remove.
    """
    if llm.server_bin():
        return {"needed": False}
    brew = _brew()
    if brew:
        return {
            "needed": True,
            "can_run": True,
            "manager": "Homebrew",
            "command": "brew install llama.cpp",
            "note": "Installs llama-server into your Homebrew prefix.",
        }
    if os.name == "nt":
        return {
            "needed": True, "can_run": False, "manager": None,
            "command": "winget install llama.cpp",
            "note": "Or download a release build and point GSO-1 at llama-server.exe below.",
        }
    return {
        "needed": True, "can_run": False, "manager": None,
        "command": "See github.com/ggml-org/llama.cpp for a build for your platform",
        "note": "Then put the path to llama-server in the box below.",
    }


_install_lock = threading.Lock()


def install_llama() -> Iterator[str]:
    """Run the install command, yielding output lines as they arrive.

    Streamed rather than run-and-report because `brew install` on a cold cache
    is minutes long, and a button that goes quiet for four minutes is
    indistinguishable from one that is broken.
    """
    plan = install_plan()
    if not plan.get("needed"):
        yield "llama-server is already installed."
        return
    if not plan.get("can_run"):
        yield f"No package manager found. Run this yourself:\n  {plan['command']}"
        return
    if not _install_lock.acquire(blocking=False):
        yield "An install is already running."
        return
    try:
        proc = subprocess.Popen(
            [_brew(), "install", "llama.cpp"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, env={**os.environ, "HOMEBREW_NO_AUTO_UPDATE": "1"},
        )
        for line in proc.stdout:  # type: ignore[union-attr]
            yield line.rstrip()
        proc.wait()
        # server_bin() reads the filesystem per call, so the new binary is
        # visible immediately; there is nothing to restart.
        found = llm.server_bin() or shutil.which("llama-server")
        if proc.returncode == 0 and found:
            yield f"\nInstalled. llama-server is at {found}"
        elif proc.returncode == 0:
            yield "\nInstall finished but llama-server is still not on any known path."
        else:
            yield f"\nInstall failed (exit {proc.returncode})."
    finally:
        _install_lock.release()


# -------------------------------------------------------------------- catalog

def _hf_get(url: str, timeout: int = 12) -> Optional[dict]:
    try:
        req = urllib.request.Request(url, headers=_UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None


def _resolve(entry: dict) -> Optional[dict]:
    """Turn one catalog entry into a real file with a real size, or drop it."""
    meta = _hf_get(f"{_HF_API}/{entry['repo']}")
    if not meta:
        return None
    names = [
        s.get("rfilename", "") for s in (meta.get("siblings") or [])
        if str(s.get("rfilename", "")).endswith(".gguf")
    ]
    # A multi-part GGUF needs every shard; that is a different download flow, so
    # prefer a single file and skip the entry rather than fetch one piece of it.
    single = [n for n in names if "-of-" not in n]
    match = [n for n in single if entry["quant"].lower() in n.lower()]
    if not match:
        return None
    name = min(match, key=len)
    size = _file_size(entry["repo"], name)
    if not size:
        return None
    return {
        **{k: entry[k] for k in ("id", "repo", "label", "note", "quant")},
        "file": name,
        "size_bytes": size,
        "size_gb": round(size / 1e9, 1),
    }


def _file_size(repo: str, name: str) -> int:
    """Size from a HEAD, following the CDN redirect Hugging Face issues."""
    url = _HF_FILE.format(repo=repo, file=name)
    try:
        req = urllib.request.Request(url, headers=_UA, method="HEAD")
        with urllib.request.urlopen(req, timeout=12) as r:
            return int(r.headers.get("Content-Length") or 0)
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return 0


def catalog(total_ram_bytes: int = 0) -> dict:
    """Resolvable models, annotated with whether this machine can hold them.

    The RAM check is the point: a list that offers a 22 GB model to a 16 GB
    laptop is a list that wastes an hour of somebody's bandwidth before telling
    them. Weights plus KV cache plus everything else already running is the
    real budget, so the bar is deliberately above the file size alone.
    """
    with _cache_lock:
        fresh = _cache["items"] and (time.time() - _cache["at"]) < _CATALOG_TTL
        items = list(_cache["items"]) if fresh else []
    if not items:
        items = [r for r in (_resolve(e) for e in _CATALOG) if r]
        with _cache_lock:
            _cache["at"], _cache["items"] = time.time(), list(items)

    have = {Path(m["path"]).name for m in llm.list_models()}
    out = []
    for it in items:
        need = it["size_bytes"] * 1.25          # weights + a working KV cache
        out.append({
            **it,
            "on_disk": it["file"] in have,
            "fits": (not total_ram_bytes) or need <= total_ram_bytes,
        })
    return {"models": out, "dest": str(download_dir()), "reachable": bool(items)}


def download_dir() -> Path:
    """Where a downloaded model goes: the first scanned folder that exists.

    Falls back to creating the first one in the list, so "nothing exists yet" is
    a solvable state rather than an error, and whatever we create is somewhere
    the scanner already looks.
    """
    dirs = [Path(d).expanduser() for d in llm.model_dirs()]
    for d in dirs:
        if d.is_dir():
            return d
    return dirs[0] if dirs else (Path.home() / "models")


# ------------------------------------------------------------------- download

_dl_lock = threading.Lock()
_dl: dict = {"state": "idle"}


def status() -> dict:
    with _dl_lock:
        return dict(_dl)


def cancel() -> bool:
    with _dl_lock:
        if _dl.get("state") != "running":
            return False
        _dl["cancel"] = True
        return True


def start(model_id: str, total_ram_bytes: int = 0) -> dict:
    """Begin a download in the background. One at a time, on purpose."""
    with _dl_lock:
        if _dl.get("state") == "running":
            return {"ok": False, "message": "A download is already running."}
    entry = next((m for m in catalog(total_ram_bytes)["models"]
                  if m["id"] == model_id), None)
    if not entry:
        return {"ok": False, "message": "That model is no longer available."}

    dest_dir = download_dir()
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return {"ok": False, "message": f"Cannot write to {dest_dir}: {e}"}

    # Seed `done` from the partial file so a resumed download does not flash
    # 0% for the second or two it takes to open the connection.
    part = dest_dir / (entry["file"] + ".part")
    already = part.stat().st_size if part.exists() else 0
    with _dl_lock:
        _dl.clear()
        _dl.update({
            "state": "running", "id": entry["id"], "label": entry["label"],
            "file": entry["file"], "dest": str(dest_dir / entry["file"]),
            "total": entry["size_bytes"], "done": already, "cancel": False,
            "message": "", "started": time.time(),
        })
    threading.Thread(target=_run, args=(entry, dest_dir), daemon=True).start()
    return {"ok": True, "message": f"Downloading {entry['label']}…"}


def _run(entry: dict, dest_dir: Path) -> None:
    final = dest_dir / entry["file"]
    part = final.with_suffix(final.suffix + ".part")
    url = _HF_FILE.format(repo=entry["repo"], file=entry["file"])
    # Resume from whatever a previous attempt managed. These files are tens of
    # gigabytes; starting over because a laptop slept is not acceptable.
    have = part.stat().st_size if part.exists() else 0
    headers = dict(_UA)
    if have:
        headers["Range"] = f"bytes={have}-"
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=60) as r:
            if have and r.status != 206:
                have = 0        # server ignored the range; start clean
            mode = "ab" if have else "wb"
            with _dl_lock:
                _dl["done"] = have
            with open(part, mode) as fh:
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    fh.write(chunk)
                    with _dl_lock:
                        _dl["done"] += len(chunk)
                        if _dl.get("cancel"):
                            _dl.update({"state": "cancelled",
                                        "message": "Download cancelled; partial file kept."})
                            return
        part.replace(final)
        with _dl_lock:
            _dl.update({"state": "done", "message": f"{entry['label']} is ready to load."})
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        with _dl_lock:
            _dl.update({"state": "error",
                        "message": f"Download failed: {e}. Press it again to resume."})
