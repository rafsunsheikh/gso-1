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
# working directory. PyInstaller can be invoked from anywhere, locally from
# build/, in CI from the repo root: and a relative pathex silently resolves
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

# The dashboard is a static bundle read from disk at request time; without
# it the server starts and serves nothing.
datas = [(os.path.join(ROOT, "thecmanager", "static"), "thecmanager/static")]
binaries = []
pathex = [ROOT]

# A Pro build: add-ons in pro/ (a private checkout, absent from this repository)
# are bundled as compiled code, with their static files and the list of names
# the loader reads (pro/ADDONS). Each add-on can name extra packages it needs
# in pro/<name>/BUNDLE (one per line). A plain checkout builds the free app.
PRO = os.path.join(ROOT, "pro")
if os.path.isfile(os.path.join(PRO, "ADDONS")):
    from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

    pathex.append(PRO)
    datas.append((os.path.join(PRO, "ADDONS"), "pro"))
    for name in [n.strip() for n in open(os.path.join(PRO, "ADDONS")) if n.strip() and not n.startswith("#")]:
        hidden += collect_submodules(name)
        web = os.path.join(PRO, name, "web")
        if os.path.isdir(web):
            datas.append((web, f"{name}/web"))
        extra = os.path.join(PRO, name, "BUNDLE")
        for pkg in [p.strip() for p in open(extra)] if os.path.isfile(extra) else []:
            if pkg and not pkg.startswith("#"):
                hidden += collect_submodules(pkg)
                datas += collect_data_files(pkg)
                binaries += collect_dynamic_libs(pkg)

a = Analysis(
    [os.path.join(SPECPATH, "gso1_server_entry.py")],
    pathex=pathex,
    binaries=binaries,
    datas=datas,
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
