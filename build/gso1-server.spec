# PyInstaller spec for the GSO-1 backend.
#
# Produces a self-contained `gso1-server` that the Electron shell ships as an
# extra resource. The goal is that a packaged GSO-1 needs no Python on the
# user's machine at all.
#
# Build:  pyinstaller build/gso1-server.spec --noconfirm --distpath build/dist

from PyInstaller.utils.hooks import collect_submodules

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
    ["gso1_server_entry.py"],
    pathex=[".."],
    binaries=[],
    # The dashboard is a static bundle read from disk at request time; without
    # it the server starts and serves nothing.
    datas=[("../thecmanager/static", "thecmanager/static")],
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
