"""Auto-detect how to start a project and what it is.

Detection is heuristic: it inspects the files in a project directory and
guesses the language/framework, a sensible start command, an optional setup
command (install deps), and a likely default port. Anything it guesses can be
overridden per-app in the registry.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional


def _venv_python(path: Path) -> str:
    """Return the python interpreter to use for this project."""
    for candidate in (".venv/bin/python", "venv/bin/python", "env/bin/python"):
        p = path / candidate
        if p.exists():
            return str(p)
    return "python3"


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _find_streamlit_entry(path: Path) -> Optional[str]:
    """Look for a top-level .py file that imports streamlit."""
    candidates = ["streamlit_app.py", "app.py", "main.py", "Home.py"]
    for name in candidates:
        f = path / name
        if f.exists():
            try:
                head = f.read_text(encoding="utf-8", errors="ignore")[:4000]
                if "import streamlit" in head or "streamlit" in head.split("\n")[0]:
                    return name
            except Exception:
                pass
    # Fallback: scan a few python files at the top level.
    for f in sorted(path.glob("*.py"))[:10]:
        try:
            if "import streamlit" in f.read_text(encoding="utf-8", errors="ignore")[:2000]:
                return f.name
        except Exception:
            continue
    return None


def _node_script(scripts: dict) -> Optional[str]:
    for key in ("dev", "start", "serve", "develop"):
        if key in scripts:
            return key
    return None


def _script_port_hint(path: Path, script: str) -> Optional[int]:
    """Guess a port by reading an explicit run script."""
    f = path / script
    try:
        text = f.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return None
    # Explicit --port / --server.port / PORT=
    m = re.search(r"(?:--port|--server\.port|\bPORT)[ =]+(\d{2,5})", text)
    if m:
        return int(m.group(1))
    if "streamlit" in text:
        return 8501
    if "runserver" in text or "manage.py" in text:
        return 8000
    if "uvicorn" in text or "fastapi" in text:
        return 8000
    if "flask" in text:
        return 5000
    return None


def detect(path: Path) -> dict:
    """Return a detection dict for the project at `path`.

    Keys: type, language, start_command, setup_command, default_port, notes
    """
    path = Path(path)
    py = _venv_python(path)

    result = {
        "type": "unknown",
        "language": "unknown",
        "start_command": None,
        "setup_command": None,
        "default_port": None,
        "notes": "",
    }

    # ---- Explicit run script (highest priority) -------------------------
    # An executable run.sh / start.sh is an unambiguous "this is how to start
    # me" signal and should win over framework auto-detection.
    for script in ("run.sh", "start.sh", "serve.sh"):
        if (path / script).exists():
            setup = None
            for s in ("install.sh", "setup.sh", "bootstrap.sh"):
                if (path / s).exists():
                    setup = f"bash {s}"
                    break
            result.update(
                type="script",
                language="shell script",
                start_command=f"bash {script}",
                setup_command=setup,
                default_port=_script_port_hint(path, script),
                notes=f"Uses explicit {script}"
                + (f" (setup: {setup})" if setup else ""),
            )
            return result

    # ---- Makefile run target --------------------------------------------
    makefile = path / "Makefile"
    if makefile.exists():
        try:
            mk = makefile.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            mk = ""
        for target in ("run", "serve", "dev", "start"):
            if re.search(rf"^{target}:", mk, re.MULTILINE):
                result.update(
                    type="make",
                    language="makefile",
                    start_command=f"make {target}",
                    notes=f"Makefile target '{target}'",
                )
                return result

    # ---- Node / JS / TS -------------------------------------------------
    pkg_file = path / "package.json"
    if pkg_file.exists():
        pkg = _read_json(pkg_file)
        scripts = pkg.get("scripts", {}) or {}
        deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
        script = _node_script(scripts)
        port = None
        framework = "node"
        if "next" in deps:
            framework, port = "next.js", 3000
        elif "vite" in deps:
            framework, port = "vite", 5173
        elif "react-scripts" in deps:
            framework, port = "create-react-app", 3000
        elif "express" in deps:
            framework, port = "express", 3000
        result.update(
            type="node",
            language=f"javascript ({framework})",
            start_command=f"npm run {script}" if script else "npm start",
            setup_command=None if (path / "node_modules").exists() else "npm install",
            default_port=port,
            notes=f"scripts: {', '.join(scripts.keys()) or 'none'}",
        )
        return result

    # ---- Django ---------------------------------------------------------
    if (path / "manage.py").exists():
        setup = (
            f"{py} -m pip install -r requirements.txt"
            if (path / "requirements.txt").exists()
            else None
        )
        result.update(
            type="django",
            language="python (django)",
            start_command=f"{py} manage.py runserver",
            setup_command=setup,
            default_port=8000,
        )
        return result

    # ---- Streamlit ------------------------------------------------------
    entry = _find_streamlit_entry(path)
    if entry:
        setup = (
            f"{py} -m pip install -r requirements.txt"
            if (path / "requirements.txt").exists()
            else None
        )
        result.update(
            type="streamlit",
            language="python (streamlit)",
            start_command=f"{py} -m streamlit run {entry}",
            setup_command=setup,
            default_port=8501,
        )
        return result

    # ---- FastAPI / generic python ---------------------------------------
    has_reqs = (path / "requirements.txt").exists()
    has_pyproject = (path / "pyproject.toml").exists()
    if has_reqs or has_pyproject or list(path.glob("*.py")):
        setup = f"{py} -m pip install -r requirements.txt" if has_reqs else None
        # Look for FastAPI app.
        for entry_name in ("main.py", "app.py", "server.py", "api.py"):
            f = path / entry_name
            if f.exists():
                try:
                    head = f.read_text(encoding="utf-8", errors="ignore")[:4000]
                except Exception:
                    head = ""
                module = entry_name[:-3]
                if "FastAPI(" in head or "from fastapi" in head:
                    result.update(
                        type="fastapi",
                        language="python (fastapi)",
                        start_command=f"{py} -m uvicorn {module}:app --reload --port 8000",
                        setup_command=setup,
                        default_port=8000,
                    )
                    return result
                if "Flask(" in head or "from flask" in head:
                    result.update(
                        type="flask",
                        language="python (flask)",
                        start_command=f"{py} {entry_name}",
                        setup_command=setup,
                        default_port=5000,
                    )
                    return result
                result.update(
                    type="python",
                    language="python",
                    start_command=f"{py} {entry_name}",
                    setup_command=setup,
                )
                return result
        result.update(
            type="python",
            language="python",
            start_command=None,
            setup_command=setup,
            notes="Python project but no obvious entry file (main/app/server).",
        )
        return result

    # ---- Rust -----------------------------------------------------------
    if (path / "Cargo.toml").exists():
        result.update(
            type="rust",
            language="rust",
            start_command="cargo run",
            setup_command="cargo build",
        )
        return result

    # ---- Go -------------------------------------------------------------
    if (path / "go.mod").exists():
        result.update(type="go", language="go", start_command="go run .")
        return result

    # ---- Static site ----------------------------------------------------
    if (path / "index.html").exists():
        result.update(
            type="static",
            language="html",
            start_command=f"{py} -m http.server 8000",
            default_port=8000,
        )
        return result

    result["notes"] = "Could not detect project type, set a start command manually."
    return result
