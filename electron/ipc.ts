import { BrowserWindow, ipcMain, shell, app } from 'electron';
import { join } from 'node:path';
import type { AppState, LogEntry, TraderConfig } from '../shared/types';
import { pythonBackend } from './system/python-backend';
import * as store from './system/settings-store';

// Bridges renderer IPC channels to the settings store and the Python backend.
// Also keeps a bounded in-memory log ring the Logs page can tail.

const LOG_RING_MAX = 2000;
const logRing: LogEntry[] = [];

export function appendLog(entry: LogEntry): void {
  logRing.push(entry);
  if (logRing.length > LOG_RING_MAX) logRing.splice(0, logRing.length - LOG_RING_MAX);
}

export function broadcastState(state: AppState): void {
  for (const w of BrowserWindow.getAllWindows()) {
    if (!w.isDestroyed()) w.webContents.send('state:changed', state);
  }
}

async function pushConfig(state: AppState): Promise<void> {
  if (pythonBackend.isRunning()) {
    try { await pythonBackend.request('setConfig', { config: store.toBackendConfig(state.config) }); }
    catch { /* backend will re-sync on ready */ }
  }
}

export function registerIpc(): void {
  // ── app ──
  ipcMain.handle('app:version', () => app.getVersion());
  ipcMain.handle('app:openExternal', (_e, url: string) => {
    if (/^https?:\/\//i.test(url)) return shell.openExternal(url);
  });
  ipcMain.handle('app:getUserDataPath', () => app.getPath('userData'));
  ipcMain.handle('app:factoryReset', async () => {
    try { return await pythonBackend.request('factoryReset', {}); }
    catch (e: any) { return { ok: false, error: String(e?.message || e) }; }
  });
  ipcMain.handle('app:resetPaper', async () => {
    try { return await pythonBackend.request('resetPaper', {}); }
    catch (e: any) { return { ok: false, error: String(e?.message || e) }; }
  });

  // ── state / config ──
  ipcMain.handle('state:get', () => store.get());
  ipcMain.handle('state:acceptDisclaimer', () => {
    const next = store.save({ ...store.get(), disclaimerAccepted: true });
    broadcastState(next);
    return next;
  });
  ipcMain.handle('config:get', () => store.get().config);
  ipcMain.handle('config:update', async (_e, patch: Partial<TraderConfig>) => {
    const next = store.patchConfig(patch);
    broadcastState(next);
    await pushConfig(next);
    return next;
  });
  ipcMain.handle('config:reset', async () => {
    const fresh = store.load();
    const next = store.save({ ...fresh });
    broadcastState(next);
    await pushConfig(next);
    return next;
  });

  // ── credentials ──
  ipcMain.handle('credentials:status', async () => {
    try { return await pythonBackend.request('credentialStatus', {}); }
    catch { return { demo: { present: false }, production: { present: false } }; }
  });
  ipcMain.handle('credentials:save', async (_e, input: { apiKey: string; rsaPem: string; env: string }) => {
    return pythonBackend.request('setCredentials', input);
  });
  ipcMain.handle('credentials:test', async (_e, env?: string) => {
    try { return { ok: true, ...(await pythonBackend.request('testCredentials', { env })) }; }
    catch (e: any) { return { ok: false, error: String(e?.message || e) }; }
  });
  ipcMain.handle('credentials:clear', async (_e, env?: string) => {
    return pythonBackend.request('clearCredentials', { env });
  });

  // ── backend ──
  ipcMain.handle('backend:info', () => pythonBackend.info());
  ipcMain.handle('backend:restart', () => pythonBackend.restart());
  ipcMain.handle('backend:runOnce', async (_e, action: string) => {
    try { return { ok: true, ...(await pythonBackend.request('runOnce', { action })) }; }
    catch (e: any) { return { ok: false, error: String(e?.message || e) }; }
  });

  // ── trading ──
  ipcMain.handle('trading:setEnabled', async (_e, v: boolean) => {
    const next = store.patchConfig({ enableTrading: v });
    broadcastState(next);
    try { await pushConfig(next); return { ok: true }; }
    catch (e: any) { return { ok: false, error: String(e?.message || e) }; }
  });
  ipcMain.handle('trading:cancelAllOpen', async () => {
    try { return { ok: true, ...(await pythonBackend.request('cancelAllOpen', {})) }; }
    catch (e: any) { return { ok: false, error: String(e?.message || e) }; }
  });
  ipcMain.handle('trading:flatten', async () => {
    try { return { ok: true, ...(await pythonBackend.request('flatten', {})) }; }
    catch (e: any) { return { ok: false, error: String(e?.message || e) }; }
  });
  ipcMain.handle('trading:status', async () => {
    try { return await pythonBackend.request('tradingStatus', {}); }
    catch { return { env: store.get().config.kalshiEnv, checks: [], trading: false }; }
  });

  // ── data ──
  const rpc = (method: string) => async (_e: unknown, ...args: any[]) => {
    try { return await pythonBackend.request(method, args[0] ?? {}); }
    catch { return null; }
  };
  ipcMain.handle('data:account', rpc('account'));
  ipcMain.handle('data:pnlSeries', async (_e, sinceHours?: number) => {
    try { return await pythonBackend.request('pnlSeries', { sinceHours }); } catch { return []; }
  });
  ipcMain.handle('data:positions', async (_e, filter?: unknown) => {
    try { return await pythonBackend.request('positions', filter ?? {}); } catch { return []; }
  });
  ipcMain.handle('data:signals', async (_e, filter?: unknown) => {
    try { return await pythonBackend.request('signals', filter ?? {}); } catch { return []; }
  });
  ipcMain.handle('data:scannerStats', rpc('scannerStats'));
  ipcMain.handle('data:botRuns', async (_e, env?: string | null, limit?: number) => {
    try { return await pythonBackend.request('botRuns', { env, limit }); }
    catch { return { runs: [], activeRun: null }; }
  });

  // ── logs ──
  ipcMain.handle('logs:tail', (_e, limit?: number) => logRing.slice(-(limit ?? 500)));
  ipcMain.handle('logs:clear', () => { logRing.length = 0; });
  ipcMain.handle('logs:openFolder', () => shell.openPath(join(app.getPath('userData'), 'logs')));

  // ── window ──
  ipcMain.on('window:minimize', (e) => BrowserWindow.fromWebContents(e.sender)?.minimize());
  ipcMain.on('window:maximize', (e) => {
    const w = BrowserWindow.fromWebContents(e.sender);
    if (!w) return;
    if (w.isMaximized()) w.unmaximize(); else w.maximize();
  });
  ipcMain.on('window:close', (e) => BrowserWindow.fromWebContents(e.sender)?.hide());
  ipcMain.handle('window:isMaximized', (e) => BrowserWindow.fromWebContents(e.sender)?.isMaximized() ?? false);
}
