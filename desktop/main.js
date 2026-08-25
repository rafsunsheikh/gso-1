/**
 * GSO-1 desktop shell.
 *
 * Electron owns nothing but the window and the child lifecycle. It spawns the
 * supervisor, which spawns GSO-1 from the promoted release; the renderer just
 * loads the existing dashboard at 127.0.0.1:8420 — the same single index.html
 * that already works in a browser, unmodified.
 *
 * The one hard requirement: quitting must leave no orphans. The supervisor runs
 * in its own process group and is signalled on the way out, so closing the
 * window cannot leave a headless uvicorn holding port 8420.
 */

const { app, BrowserWindow, Tray, Menu, shell, dialog, nativeImage } = require("electron");
const { spawn } = require("node:child_process");
const path = require("node:path");
const fs = require("node:fs");
const http = require("node:http");

const REPO = path.resolve(__dirname, "..");
const HOST = process.env.MANAGER_HOST || "127.0.0.1";
const PORT = Number(process.env.MANAGER_PORT || 8420);
const URL = `http://${HOST}:${PORT}`;
const PY = path.join(REPO, ".venv/bin/python");

/**
 * A shipped GSO-1 carries its own backend and knows nothing about a checkout:
 * no venv, no supervisor, no promoted release. A developer build keeps the
 * original path, so `npm start` in the repo behaves exactly as before.
 */
const PACKAGED = app.isPackaged;
const SERVER_BIN = path.join(
  process.resourcesPath || "",
  "server",
  process.platform === "win32" ? "gso1-server.exe" : "gso1-server",
);

/** Boot can take a while: uvicorn start plus the first project scan. */
const BOOT_TIMEOUT_MS = 90_000;

let win = null;
let tray = null;
let supervisor = null;
let quitting = false;
/** True when we started the supervisor ourselves and must therefore stop it. */
let weOwnSupervisor = false;

// ---------------------------------------------------------------- health

function probe(timeoutMs = 4000) {
  return new Promise((resolve) => {
    const req = http.get(`${URL}/api/llm/status`, { timeout: timeoutMs }, (res) => {
      res.resume();
      resolve(res.statusCode > 0);
    });
    req.on("timeout", () => {
      req.destroy();
      resolve(false);
    });
    req.on("error", () => resolve(false));
  });
}

async function waitForApp(timeoutMs = BOOT_TIMEOUT_MS) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await probe()) return true;
    await new Promise((r) => setTimeout(r, 1000));
  }
  return false;
}

// ------------------------------------------------------------ supervisor

function currentReleaseExists() {
  try {
    return fs.statSync(path.join(REPO, "var/current/thecmanager")).isDirectory();
  } catch {
    return false;
  }
}

/**
 * Launch the backend that ships inside the app bundle.
 *
 * Nothing here may assume a writable install directory: on macOS the bundle is
 * read-only and on Windows it sits in Program Files, so the server is told to
 * keep its state in the user's data directory and is started from the user's
 * home rather than from wherever the bundle happens to live.
 */
function startBundledServer() {
  if (!fs.existsSync(SERVER_BIN)) {
    dialog.showErrorBox(
      "GSO-1 is incomplete",
      `The bundled server is missing at:\n${SERVER_BIN}\n\n` +
        "Reinstall GSO-1 from the latest release.",
    );
    return Promise.resolve(false);
  }

  supervisor = spawn(SERVER_BIN, [], {
    cwd: app.getPath("home"),
    env: {
      ...process.env,
      MANAGER_NO_BROWSER: "1",
      MANAGER_HOST: HOST,
      MANAGER_PORT: String(PORT),
      MANAGER_DATA_DIR: process.env.MANAGER_DATA_DIR || app.getPath("userData"),
    },
    stdio: "ignore",
    // Own process group, so quitting can signal the whole tree at once.
    detached: process.platform !== "win32",
  });
  weOwnSupervisor = true;

  supervisor.on("exit", (code) => {
    supervisor = null;
    if (!quitting) {
      dialog.showErrorBox("GSO-1 stopped", `The server exited unexpectedly (code ${code}).`);
    }
  });

  return waitForApp();
}


async function startSupervisor() {
  // Someone may already be running GSO-1 in a terminal. Adopt it rather than
  // fighting for the port — and remember not to kill it on quit.
  if (await probe()) {
    weOwnSupervisor = false;
    return true;
  }

  if (PACKAGED) return startBundledServer();

  if (!fs.existsSync(PY)) {
    dialog.showErrorBox(
      "GSO-1 cannot start",
      `Python virtualenv not found at:\n${PY}\n\nRun ./run.sh once in ${REPO} to create it.`,
    );
    return false;
  }
  if (!currentReleaseExists()) {
    dialog.showErrorBox(
      "No promoted release",
      "var/current does not point at a usable release.\n\n" +
        "Run in the repo:\n  python -m supervisor release create\n  python -m supervisor promote latest",
    );
    return false;
  }

  supervisor = spawn(PY, ["-m", "supervisor", "start"], {
    cwd: REPO,
    env: { ...process.env, MANAGER_NO_BROWSER: "1" },
    stdio: "ignore",
    detached: true, // own process group, so we can signal the whole tree
  });
  weOwnSupervisor = true;

  supervisor.on("exit", (code) => {
    supervisor = null;
    if (!quitting) {
      dialog.showErrorBox("GSO-1 stopped", `The supervisor exited unexpectedly (code ${code}).`);
    }
  });

  return waitForApp();
}

function stopSupervisor() {
  if (!supervisor || !weOwnSupervisor) return;
  try {
    // Negative pid signals the group: supervisor + the GSO-1 child it spawned.
    // Windows has no process groups in this sense, so signal the child itself.
    if (process.platform === "win32") supervisor.kill();
    else process.kill(-supervisor.pid, "SIGTERM");
  } catch {
    try {
      supervisor.kill("SIGTERM");
    } catch {
      /* already gone */
    }
  }
  supervisor = null;
}

// ---------------------------------------------------------------- window

function iconPath(name) {
  const p = path.join(__dirname, "assets", name);
  return fs.existsSync(p) ? p : null;
}

function createWindow() {
  const icon = iconPath("icon.png");
  win = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 900,
    minHeight: 600,
    title: "GSO-1",
    // Matches --bg so the window does not flash a different dark before paint.
    backgroundColor: "#09080f",
    ...(icon ? { icon } : {}),
    titleBarStyle: "hiddenInset",
    // The page draws its own 38px title bar; centre the lights in it.
    trafficLightPosition: { x: 14, y: 12 },
    show: false,
    webPreferences: {
      // The renderer only shows a local dashboard; it needs no Node access.
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
    },
  });

  win.once("ready-to-show", () => win.show());
  win.loadURL(URL);

  // External links open in the real browser, never inside the shell.
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (!url.startsWith(URL)) {
      shell.openExternal(url);
      return { action: "deny" };
    }
    return { action: "allow" };
  });
  win.webContents.on("will-navigate", (event, url) => {
    if (!url.startsWith(URL)) {
      event.preventDefault();
      shell.openExternal(url);
    }
  });

  // Closing the window hides to tray; quitting is explicit.
  win.on("close", (event) => {
    if (!quitting) {
      event.preventDefault();
      win.hide();
    }
  });
  win.on("closed", () => (win = null));
}

function showWindow() {
  if (!win) createWindow();
  else {
    win.show();
    win.focus();
  }
}

/** Ask the page to toggle its panel; fall back to a reload if the page is stale. */
function toggleOpsPanel() {
  if (!win) return showWindow();
  win.webContents
    .executeJavaScript(
      `(() => {
         const b = document.getElementById('ops-open') || document.getElementById('ops-toggle');
         if (b) { b.click(); return true; }
         return false;
       })()`,
    )
    .then((ok) => {
      if (!ok) {
        // The loaded page predates the panel — reload to pick it up.
        win.reload();
      }
    })
    .catch(() => win.reload());
}

/**
 * An application menu. Without one, ⌘R and ⌘J only work if the loaded page
 * happens to implement them — and a page loaded before an update does not.
 * Driving the toggle from the menu makes it work regardless.
 */
function createMenu() {
  const isMac = process.platform === "darwin";
  Menu.setApplicationMenu(
    Menu.buildFromTemplate([
      ...(isMac ? [{ role: "appMenu" }] : []),
      {
        label: "View",
        submenu: [
          { label: "Reload", accelerator: "CmdOrCtrl+R", click: () => win?.reload() },
          {
            label: "Force Reload (clear cache)",
            accelerator: "CmdOrCtrl+Shift+R",
            click: () => win?.webContents.reloadIgnoringCache(),
          },
          { type: "separator" },
          { role: "resetZoom" },
          { role: "zoomIn" },
          { role: "zoomOut" },
          { type: "separator" },
          { role: "toggleDevTools" },
          { role: "togglefullscreen" },
        ],
      },
      {
        label: "Ops Room",
        submenu: [
          { label: "Toggle Ops Room", accelerator: "CmdOrCtrl+J", click: toggleOpsPanel },
          { type: "separator" },
          { label: "Open dashboard in browser", click: () => shell.openExternal(URL) },
        ],
      },
      { role: "windowMenu" },
    ]),
  );
}

function createTray() {
  const p = iconPath("trayTemplate.png");
  if (!p) return;
  const img = nativeImage.createFromPath(p);
  img.setTemplateImage(true); // adapts to light/dark menu bar on macOS
  tray = new Tray(img);
  tray.setToolTip("GSO-1");
  tray.setContextMenu(
    Menu.buildFromTemplate([
      { label: "Open GSO-1", click: showWindow },
      { label: "Toggle Ops Room  (Cmd+J)", click: toggleOpsPanel },
      { label: "Open in browser", click: () => shell.openExternal(URL) },
      { type: "separator" },
      { label: "Reload", click: () => win?.reload() },
      { type: "separator" },
      {
        label: "Quit GSO-1",
        click: () => {
          quitting = true;
          app.quit();
        },
      },
    ]),
  );
  tray.on("click", showWindow);
}

// -------------------------------------------------------------- lifecycle

// Only one instance may own port 8420.
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", showWindow);

  app.whenReady().then(async () => {
    createMenu();
    createTray();
    const ok = await startSupervisor();
    if (!ok) {
      dialog.showErrorBox(
        "GSO-1 did not start",
        `The dashboard did not answer on ${URL} within ${BOOT_TIMEOUT_MS / 1000}s.\n\n` +
          (PACKAGED
            // A packaged build has no repo to point at; the port is the usual culprit.
            ? `Something may already be using port ${PORT}. ` +
              `Set MANAGER_PORT to pick another one.`
            : `Check: ${path.join(REPO, "var/supervisor.log")}`),
      );
      quitting = true;
      app.quit();
      return;
    }
    createWindow();
  });

  app.on("activate", showWindow);

  // Children must not outlive the shell — on normal quit, on SIGINT, or on a
  // crash of the main process.
  app.on("before-quit", () => {
    quitting = true;
    stopSupervisor();
  });
  app.on("will-quit", stopSupervisor);
  process.on("exit", stopSupervisor);
  for (const sig of ["SIGINT", "SIGTERM"]) {
    process.on(sig, () => {
      quitting = true;
      stopSupervisor();
      app.quit();
    });
  }

  // Closing the last window does not quit — the tray keeps it alive.
  app.on("window-all-closed", () => {});
}
