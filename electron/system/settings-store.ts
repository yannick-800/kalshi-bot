import { app } from 'electron';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import type { AppState, TraderConfig } from '../../shared/types';

// Persists renderer-facing app state (config + preferences + window bounds) to
// a JSON file in the user-data dir. The trading config here is the source of
// truth the Python backend is told about via setConfig.

const DEFAULT_CONFIG: TraderConfig = {
  kalshiEnv: 'demo',
  enableTrading: false,
  tradeWhales: true,
  tradeMomentum: true,
  minEdgePtsWhale: 6,
  minEdgePtsMomentum: 6,
  minConfidenceWhale: 55,
  minConfidenceMomentum: 55,
  feeAwareEdge: true,
  minMarketVolume: 100,
  minEntryPriceCents: 30,
  maxEntryPriceCents: 55,
  maxResolutionDays: 30,
  maxResolutionHours: 0,
  strategyPreset: 'Conservadora',
  contrarianOnly: true,
  sizingMode: 'percent',
  fixedTradeUsd: 5,
  hardMaxPositionUsd: 12,
  maxOpenPositions: 25,
  maxDailyNewPositions: 40,
  unlimitedDailyNewPositions: false,
  maxTotalExposureFraction: 0.20,
  stopLossOnDay: -30,
  takeProfitOnDay: 0,
  minWhaleUsd: 2500,
  tradingHoursEnabled: false,
  cryptoSignalEnabled: false,
  tennisSignalEnabled: false,
  tennisFavoriteEnabled: false,
};

const DEFAULT_STATE: AppState = {
  config: DEFAULT_CONFIG,
  startMinimized: false,
  startWithWindows: false,
  disclaimerAccepted: false,
};

let cache: AppState | null = null;

function file(): string {
  const dir = app.getPath('userData');
  if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
  return join(dir, 'settings.json');
}

export function load(): AppState {
  try {
    const raw = readFileSync(file(), 'utf-8');
    const parsed = JSON.parse(raw) as Partial<AppState>;
    cache = {
      ...DEFAULT_STATE,
      ...parsed,
      config: { ...DEFAULT_CONFIG, ...(parsed.config ?? {}) },
    };
  } catch {
    cache = { ...DEFAULT_STATE, config: { ...DEFAULT_CONFIG } };
  }
  return cache;
}

export function get(): AppState {
  return cache ?? load();
}

export function save(state: AppState): AppState {
  cache = state;
  try {
    writeFileSync(file(), JSON.stringify(state, null, 2), 'utf-8');
  } catch {
    // best-effort
  }
  return cache;
}

export function patchConfig(patch: Partial<TraderConfig>): AppState {
  const cur = get();
  return save({ ...cur, config: { ...cur.config, ...patch } });
}

// snake_case config for the Python backend (its DEFAULT_CONFIG keys).
export function toBackendConfig(cfg: TraderConfig): Record<string, unknown> {
  const map: Record<string, string> = {
    kalshiEnv: 'kalshi_env', enableTrading: 'enable_trading',
    tradeWhales: 'trade_whales', tradeMomentum: 'trade_momentum',
    minEdgePtsWhale: 'min_edge_pts_whale', minEdgePtsMomentum: 'min_edge_pts_momentum',
    minConfidenceWhale: 'min_confidence_whale', minConfidenceMomentum: 'min_confidence_momentum',
    feeAwareEdge: 'fee_aware_edge', minMarketVolume: 'min_market_volume',
    minEntryPriceCents: 'min_entry_price_cents', maxEntryPriceCents: 'max_entry_price_cents',
    maxResolutionDays: 'max_resolution_days', maxResolutionHours: 'max_resolution_hours',
    strategyPreset: 'strategy_preset', contrarianOnly: 'contrarian_only',
    sizingMode: 'sizing_mode', fixedTradeUsd: 'fixed_trade_usd',
    hardMaxPositionUsd: 'hard_max_position_usd', maxOpenPositions: 'max_open_positions',
    maxDailyNewPositions: 'max_daily_new_positions',
    unlimitedDailyNewPositions: 'unlimited_daily_new_positions',
    maxTotalExposureFraction: 'max_total_exposure_fraction',
    stopLossOnDay: 'stop_loss_on_day', takeProfitOnDay: 'take_profit_on_day',
    minWhaleUsd: 'min_whale_usd', tradingHoursEnabled: 'trading_hours_enabled',
    cryptoSignalEnabled: 'crypto_signal_enabled', tennisSignalEnabled: 'tennis_signal_enabled',
    tennisFavoriteEnabled: 'tennis_favorite_enabled',
  };
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(cfg)) {
    out[map[k] ?? k] = v;
  }
  return out;
}
