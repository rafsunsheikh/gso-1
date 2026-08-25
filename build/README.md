# Packaging

How a GSO-1 release is built. Two stages: freeze the Python backend, then wrap
it in the Electron shell.

## 1. Freeze the backend

```bash
pip install pyinstaller
pyinstaller build/gso1-server.spec --noconfirm \
  --distpath build/dist --workpath build/work
```

Produces `build/dist/gso1-server/` — a self-contained server (~34 MB) that
needs no Python on the target machine. It serves the dashboard from static
files collected into the bundle, defaults to loopback, and keeps its state in
the platform user-data directory.

Verify it before going further:

```bash
MANAGER_PORT=8534 build/dist/gso1-server/gso1-server
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8534/   # expect 200
```

## 2. Build the installers

```bash
cd desktop
npm install
npm run dist:mac      # .dmg + .zip  (arm64, x64)
npm run dist:win      # NSIS .exe
npm run dist:linux    # AppImage + .deb
```

electron-builder copies `build/dist/gso1-server` into the bundle as
`Contents/Resources/server` (macOS) or `resources/server` (Windows, Linux).
Output lands in `build/release/`.

`npm run pack` builds an unpacked app without an installer — much faster when
you only need to test that the shell finds the server.

## Signing

Unsigned builds work but trip Gatekeeper on macOS and SmartScreen on Windows;
users see a warning and must explicitly allow the app.

To sign macOS builds, set `CSC_LINK` and `CSC_KEY_PASSWORD` (and
`APPLE_ID`, `APPLE_APP_SPECIFIC_PASSWORD`, `APPLE_TEAM_ID` to notarize).
Local development builds skip signing with
`CSC_IDENTITY_AUTO_DISCOVERY=false`.

## Layout

| Path | What |
|---|---|
| `gso1-server.spec` | PyInstaller spec for the backend |
| `gso1_server_entry.py` | Entry point used by the frozen build |
| `dist/` | Frozen backend (gitignored) |
| `release/` | Installers (gitignored) |
| `work/` | PyInstaller scratch (gitignored) |
