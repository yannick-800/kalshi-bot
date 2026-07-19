import { app, BrowserWindow, Menu, screen, session, shell } from 'electron';
import { existsSync } from 'node:fs';
import { join } from 'node:path';
import { appendLog, broadcastState, registerIpc } from './ipc';
import { pythonBackend } from './system/python-backend';
import * as store from './system/settings-store';

// Electron entry point. Owns the frameless main window, wires backend events to
// the renderer, and starts/stops the Python backend. Local-only: no auto-update,
// no telemetry, no external services.

process.env.DIST_ELECTRON = __dirname;
process.env.DIST = join(__dirname, '..', 'dist');

let mainWindow: BrowserWindow | null = null;
let quitting = false;

function iconPath(): string {
  const candidates = [
    join(process.cwd(), 'resources', 'icon.png'),
    join(__dirname, '..', 'resources', 'icon.png'),
  ];
  for (const c of candidates) if (existsSync(c)) return c;
  return candidates[0];
}

function createMainWindow(): BrowserWindow {
  if (mainWindow && !mainWindow.isDestroyed()) {
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.show();
    mainWindow.focus();
    return mainWindow;
  }
  const state = store.get();
  const bounds = state.windowBounds;
  const primary = screen.getPrimaryDisplay();
  const width = bounds?.width ?? Math.min(1380, primary.workArea.width - 40);
  const height = bounds?.height ?? Math.min(900, primary.workArea.height - 40);

  mainWindow = new BrowserWindow({
    width, height, minWidth: 1100, minHeight: 720,
    x: bounds?.x, y: bounds?.y,
    backgroundColor: '#0A0A0F', show: false, frame: false, titleBarStyle: 'hidden',
    icon: iconPath(),
    webPreferences: {
      preload: join(__dirname, 'preload.js'),
      contextIsolation: true, sandbox: true, nodeIntegration: false,
      backgroundThrottling: false, devTools: !app.isPackaged,
    },
  });

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:\/\//i.test(url)) void shell.openExternal(url);
    return { action: 'deny' };
  });
  mainWindow.webContents.on('will-navigate', (e, url) => {
    const dev = process.env['VITE_DEV_SERVER_URL'];
    if ((dev && url.startsWith(dev)) || url.startsWith('file://')) return;
    e.preventDefault();
    if (/^https?:\/\//i.test(url)) void shell.openExternal(url);
  });

  const url = process.env['VITE_DEV_SERVER_URL'];
  if (url) void mainWindow.loadURL(url);
  else void mainWindow.loadFile(join(process.env.DIST!, 'index.html'));

  mainWindow.once('ready-to-show', () => { mainWindow?.show(); mainWindow?.focus(); });

  const persist = (): void => {
    if (!mainWindow || mainWindow.isDestroyed()) return;
    if (mainWindow.isMaximized() || mainWindow.isMinimized()) return;
    store.save({ ...store.get(), windowBounds: mainWindow.getBounds() });
  };
  mainWindow.on('move', persist);
  mainWindow.on('resize', persist);
  mainWindow.on('maximize', () => mainWindow?.webContents.send('window:maximizeChange', true));
  mainWindow.on('unmaximize', () => mainWindow?.webContents.send('window:maximizeChange', false));
  mainWindow.on('close', (e) => { if (!quitting) { e.preventDefault(); mainWindow?.hide(); } });
  mainWindow.on('closed', () => { mainWindow = null; });
  return mainWindow;
}

function installCsp(): void {
  if (!app.isPackaged) return;
  session.defaultSession.webRequest.onHeadersReceived((details, cb) => {
    cb({
      responseHeaders: {
        ...details.responseHeaders,
        'Content-Security-Policy': [
          "default-src 'self'; script-src 'self'; " +
          "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; " +
          "font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'",
        ],
      },
    });
  });
}

async function bootstrap(): Promise<void> {
  if (process.platform !== 'darwin') Menu.setApplicationMenu(null);
  registerIpc();
  installCsp();
  const state = store.load();

  pythonBackend.onStatusChange((info) => {
    for (const w of BrowserWindow.getAllWindows()) if (!w.isDestroyed()) w.webContents.send('backend:info', info);
  });
  pythonBackend.onEvent((name, data) => {
    if (name === 'backend:ready') {
      void pythonBackend.request('setConfig', { config: store.toBackendConfig(store.get().config) }).catch(() => { /* noop */ });
    }
    for (const w of BrowserWindow.getAllWindows()) {
      if (w.isDestroyed()) continue;
      if (name === 'account:update') w.webContents.send('data:account', data);
      else if (name === 'position:new' || name === 'position:update') w.webContents.send('data:position', data);
      else if (name === 'signal:new') w.webContents.send('data:signal', data);
      else if (name === 'credentials:changed') w.webContents.send('credentials:changed', data);
      else if (name === 'data:reset') w.webContents.send('data:reset', data);
    }
  });
  pythonBackend.onLog((entry) => {
    appendLog(entry);
    for (const w of BrowserWindow.getAllWindows()) if (!w.isDestroyed()) w.webContents.send('logs:append', entry);
  });

  void pythonBackend.start().then(async () => {
    const start = Date.now();
    while (!pythonBackend.isRunning() && Date.now() - start < 8000) await new Promise((r) => setTimeout(r, 200));
    try { await pythonBackend.request('setConfig', { config: store.toBackendConfig(state.config) }); } catch { /* noop */ }
  });

  createMainWindow();
}

const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on('second-instance', () => { const w = createMainWindow(); if (w.isMinimized()) w.restore(); w.show(); w.focus(); });
  app.whenReady().then(bootstrap);
}

app.on('window-all-closed', (e: Electron.Event) => { e.preventDefault(); });
app.on('activate', () => createMainWindow());
app.on('before-quit', async () => { quitting = true; await pythonBackend.stop(); });

void broadcastState; // exported for future use by tray/menu
