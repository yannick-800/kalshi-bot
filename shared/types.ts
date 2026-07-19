// Shared types between the Electron main process, the preload bridge and the
// React renderer. Keep this the single source of truth for the IPC contract.

export type KalshiEnv = 'demo' | 'production';

export interface TraderConfig {
  kalshiEnv: KalshiEnv;
  enableTrading: boolean;
  tradeWhales: boolean;
  tradeMomentum: boolean;
  minEdgePtsWhale: number;
  minEdgePtsMomentum: number;
  minConfidenceWhale: number;
  minConfidenceMomentum: number;
  feeAwareEdge: boolean;
  minMarketVolume: number;
  minEntryPriceCents: number;
  maxEntryPriceCents: number;
  maxResolutionDays: number;
  maxResolutionHours: number;
  strategyPreset: string;
  contrarianOnly: boolean;
  sizingMode: 'percent' | 'fixed';
  fixedTradeUsd: number;
  hardMaxPositionUsd: number;
  maxOpenPositions: number;
  maxDailyNewPositions: number;
  unlimitedDailyNewPositions: boolean;
  maxTotalExposureFraction: number;
  stopLossOnDay: number;
  takeProfitOnDay: number;
  minWhaleUsd: number;
  tradingHoursEnabled: boolean;
  cryptoSignalEnabled: boolean;
  tennisSignalEnabled: boolean;
  tennisFavoriteEnabled: boolean;
  [key: string]: unknown; // forward-compat
}

export interface AppState {
  config: TraderConfig;
  startMinimized: boolean;
  startWithWindows: boolean;
  disclaimerAccepted: boolean;
  windowBounds?: { x: number; y: number; width: number; height: number };
}

export type BackendStatus =
  | 'stopped' | 'starting' | 'running' | 'restarting' | 'crashed';

export interface BackendInfo {
  status: BackendStatus;
  pid: number | null;
  startedAt: string | null;
  lastError: string | null;
  pythonOk: boolean;
  authOk: boolean;
}

export interface LogEntry {
  ts: string;
  level: string;
  source: string;
  msg: string;
}

export interface AccountSnapshot {
  cashUsd: number;
  portfolioUsd: number;
  totalUsd: number;
  realizedPnlUsd: number;
  feesUsd: number;
  wins: number;
  losses: number;
  winsUsd: number;
  lossesUsd: number;
  winRate: number;
  todayWins: number;
  todayLosses: number;
  openCount: number;
  pendingCount: number;
  resolvedCount: number;
  totalOpened: number;
  env: string;
}

export interface SignalRow {
  id: number;
  source: 'whale' | 'momentum';
  ticker: string;
  eventTicker: string;
  title: string;
  category: string;
  direction: string;
  priceCents: number;
  confidence: number;
  edgePts: number;
  dollarValue?: number;
  signalType?: string;
  createdAt: string;
  traded: boolean;
}

export interface BotPosition {
  id: number;
  signalSource: string;
  ticker: string;
  eventTicker: string;
  title: string;
  category: string;
  direction: string;
  targetContracts: number;
  limitPriceCents: number;
  filledContracts: number;
  costUsd: number;
  feesUsd: number;
  status: string;
  confidence: number;
  edgePts: number;
  resolved: boolean;
  outcomeCorrect: number | null;
  pnlUsd: number | null;
  livePnlUsd: number | null;
  kalshiEnv: string;
  createdAt: string;
  resolvedAt: string | null;
  closeTime: string;
  marketUrl: string;
  yesLabel: string;
  mtype: string;
  eventTitle: string;
  error: string | null;
}

export interface PnlPoint {
  at: string;
  cashUsd: number;
  portfolioUsd: number;
  totalUsd: number;
  realizedPnlUsd: number;
  openPositions: number;
}

export interface ScannerStats {
  marketsTracked: number;
  whales: { total: number };
  momentum: { total: number };
  lastWhaleScanAt: string | null;
  lastMomentumScanAt: string | null;
  lastTradeScanAt: string | null;
}

export interface TradingStatusCheck {
  label: string;
  ok: boolean;
  detail?: string;
}
export interface TradingStatus {
  env: string;
  checks: TradingStatusCheck[];
  trading: boolean;
}

export interface CredentialsState {
  demo: { present: boolean };
  production: { present: boolean };
}
export interface CredentialsInput {
  apiKey: string;
  rsaPem: string;
  env: KalshiEnv;
}

export interface ActionResult {
  ok: boolean;
  error?: string;
  [key: string]: unknown;
}

export interface PositionFilter {
  status?: string[];
  resolved?: boolean;
  signalSource?: string;
  limit?: number;
}
export interface SignalFilter {
  source?: 'whale' | 'momentum';
  minConfidence?: number;
  limit?: number;
}
export interface BotRun {
  id: number;
  kalshiEnv: string;
  startedAt: string;
  endedAt: string | null;
  startTotalUsd: number;
  endTotalUsd: number | null;
  pnlUsd: number;
  tradesOpened: number;
  tradesWon: number;
  tradesLost: number;
  isActive: boolean;
}

// The API surface exposed on `window.kbot` by the preload bridge.
export interface KalshiBotApi {
  app: {
    version: () => Promise<string>;
    openExternal: (url: string) => Promise<void>;
    getUserDataPath: () => Promise<string>;
    factoryReset: () => Promise<ActionResult>;
    resetPaper: () => Promise<ActionResult>;
    onDataReset: (cb: (v: unknown) => void) => () => void;
  };
  state: {
    get: () => Promise<AppState>;
    onChange: (cb: (v: AppState) => void) => () => void;
    acceptDisclaimer: () => Promise<AppState>;
  };
  config: {
    get: () => Promise<TraderConfig>;
    update: (patch: Partial<TraderConfig>) => Promise<AppState>;
    reset: () => Promise<AppState>;
  };
  credentials: {
    status: () => Promise<CredentialsState>;
    save: (input: CredentialsInput) => Promise<CredentialsState>;
    test: (env?: string) => Promise<ActionResult>;
    clear: (env?: string) => Promise<CredentialsState>;
    onChanged: (cb: (v: unknown) => void) => () => void;
  };
  backend: {
    info: () => Promise<BackendInfo>;
    restart: () => Promise<void>;
    onInfo: (cb: (v: BackendInfo) => void) => () => void;
    runOnce: (action: string) => Promise<ActionResult>;
  };
  trading: {
    setEnabled: (v: boolean) => Promise<ActionResult>;
    cancelAllOpen: () => Promise<ActionResult>;
    status: () => Promise<TradingStatus>;
    flatten: () => Promise<ActionResult>;
  };
  data: {
    account: () => Promise<AccountSnapshot>;
    pnlSeries: (sinceHours?: number) => Promise<PnlPoint[]>;
    positions: (filter?: PositionFilter) => Promise<BotPosition[]>;
    signals: (filter?: SignalFilter) => Promise<SignalRow[]>;
    scannerStats: () => Promise<ScannerStats>;
    botRuns: (env?: string | null, limit?: number) => Promise<{ runs: BotRun[]; activeRun: BotRun | null }>;
    onAccount: (cb: (v: AccountSnapshot) => void) => () => void;
    onPosition: (cb: (v: BotPosition) => void) => () => void;
    onSignal: (cb: (v: SignalRow) => void) => () => void;
  };
  logs: {
    tail: (limit?: number) => Promise<LogEntry[]>;
    onAppend: (cb: (v: LogEntry) => void) => () => void;
    clear: () => Promise<void>;
    openFolder: () => Promise<void>;
  };
  window: {
    minimize: () => void;
    maximize: () => void;
    close: () => void;
    isMaximized: () => Promise<boolean>;
    onMaximizeChange: (cb: (v: boolean) => void) => () => void;
  };
}
