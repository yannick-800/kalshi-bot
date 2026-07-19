import { contextBridge, ipcRenderer } from 'electron';
import type {
  AccountSnapshot, ActionResult, AppState, BackendInfo, BotPosition, BotRun,
  CredentialsInput, CredentialsState, KalshiBotApi, LogEntry, PnlPoint,
  PositionFilter, ScannerStats, SignalFilter, SignalRow, TraderConfig, TradingStatus,
} from '../shared/types';

// Exposes a locked-down, typed bridge on window.kbot. contextIsolation is on;
// the renderer never touches Node or ipcRenderer directly.

const sub = <T>(channel: string, cb: (v: T) => void): (() => void) => {
  const handler = (_e: unknown, v: T) => cb(v);
  ipcRenderer.on(channel, handler);
  return () => ipcRenderer.removeListener(channel, handler);
};

const api: KalshiBotApi = {
  app: {
    version: () => ipcRenderer.invoke('app:version'),
    openExternal: (url) => ipcRenderer.invoke('app:openExternal', url),
    getUserDataPath: () => ipcRenderer.invoke('app:getUserDataPath'),
    factoryReset: () => ipcRenderer.invoke('app:factoryReset'),
    resetPaper: () => ipcRenderer.invoke('app:resetPaper'),
    onDataReset: (cb) => sub<unknown>('data:reset', cb),
  },
  state: {
    get: (): Promise<AppState> => ipcRenderer.invoke('state:get'),
    onChange: (cb) => sub<AppState>('state:changed', cb),
    acceptDisclaimer: () => ipcRenderer.invoke('state:acceptDisclaimer'),
  },
  config: {
    get: (): Promise<TraderConfig> => ipcRenderer.invoke('config:get'),
    update: (patch) => ipcRenderer.invoke('config:update', patch),
    reset: () => ipcRenderer.invoke('config:reset'),
  },
  credentials: {
    status: (): Promise<CredentialsState> => ipcRenderer.invoke('credentials:status'),
    save: (input: CredentialsInput) => ipcRenderer.invoke('credentials:save', input),
    test: (env?: string) => ipcRenderer.invoke('credentials:test', env),
    clear: (env?: string) => ipcRenderer.invoke('credentials:clear', env),
    onChanged: (cb) => sub<unknown>('credentials:changed', cb),
  },
  backend: {
    info: (): Promise<BackendInfo> => ipcRenderer.invoke('backend:info'),
    restart: () => ipcRenderer.invoke('backend:restart'),
    onInfo: (cb) => sub<BackendInfo>('backend:info', cb),
    runOnce: (action) => ipcRenderer.invoke('backend:runOnce', action),
  },
  trading: {
    setEnabled: (v: boolean): Promise<ActionResult> => ipcRenderer.invoke('trading:setEnabled', v),
    cancelAllOpen: () => ipcRenderer.invoke('trading:cancelAllOpen'),
    status: (): Promise<TradingStatus> => ipcRenderer.invoke('trading:status'),
    flatten: () => ipcRenderer.invoke('trading:flatten'),
  },
  data: {
    account: (): Promise<AccountSnapshot> => ipcRenderer.invoke('data:account'),
    pnlSeries: (sinceHours?: number): Promise<PnlPoint[]> => ipcRenderer.invoke('data:pnlSeries', sinceHours),
    positions: (filter?: PositionFilter): Promise<BotPosition[]> => ipcRenderer.invoke('data:positions', filter),
    signals: (filter?: SignalFilter): Promise<SignalRow[]> => ipcRenderer.invoke('data:signals', filter),
    scannerStats: (): Promise<ScannerStats> => ipcRenderer.invoke('data:scannerStats'),
    botRuns: (env, limit): Promise<{ runs: BotRun[]; activeRun: BotRun | null }> =>
      ipcRenderer.invoke('data:botRuns', env ?? null, limit),
    onAccount: (cb) => sub<AccountSnapshot>('data:account', cb),
    onPosition: (cb) => sub<BotPosition>('data:position', cb),
    onSignal: (cb) => sub<SignalRow>('data:signal', cb),
  },
  logs: {
    tail: (limit?: number): Promise<LogEntry[]> => ipcRenderer.invoke('logs:tail', limit),
    onAppend: (cb) => sub<LogEntry>('logs:append', cb),
    clear: () => ipcRenderer.invoke('logs:clear'),
    openFolder: () => ipcRenderer.invoke('logs:openFolder'),
  },
  window: {
    minimize: () => ipcRenderer.send('window:minimize'),
    maximize: () => ipcRenderer.send('window:maximize'),
    close: () => ipcRenderer.send('window:close'),
    isMaximized: () => ipcRenderer.invoke('window:isMaximized'),
    onMaximizeChange: (cb) => sub<boolean>('window:maximizeChange', cb),
  },
};

contextBridge.exposeInMainWorld('kbot', api);
