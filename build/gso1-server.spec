# PyInstaller spec for the GSO-1 backend.
#
# Produces a self-contained `gso1-server` that the Electron shell ships as an
# extra resource. The goal is that a packaged GSO-1 needs no Python on the
# user's machine at all.
#
# Build:  pyinstaller build/gso1-server.spec --noconfirm --distpath build/dist

import os

from PyInstaller.utils.hooks import collect_submodules

# Everything below is anchored to the spec's own directory, never to the
# working directory. PyInstaller can be invoked from anywhere — locally from
# build/, in CI from the repo root — and a relative pathex silently resolves
# to the wrong tree, producing a binary that builds fine and then cannot
# import its own package.
ROOT = os.path.abspath(os.path.join(SPECPATH, os.pardir))

# uvicorn and fastapi resolve much of their machinery by string at runtime, so
# static analysis misses it. Collecting the packages wholesale is the only
# reliable way to get a server that actually boots.
hidden = (
    collect_submodules("uvicorn")
    + collect_submodules("fastapi")
    + collect_submodules("starlette")
    + collect_submodules("anyio")
    + ["thecmanager", "supervisor"]
)

a = Analysis(
    [os.path.join(SPECPATH, "gso1_server_entry.py")],
    pathex=[ROOT],
    binaries=[],
    # The dashboard is a static bundle read from disk at request time; without
    # it the server starts and serves nothing.
    datas=[(os.path.join(ROOT, "thecmanager", "static"), "thecmanager/static")],
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "PIL"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="gso1-server",
    debug=False,
    strip=False,
    upx=False,
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="gso1-server",
)
