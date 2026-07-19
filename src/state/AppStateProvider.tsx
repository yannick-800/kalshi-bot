import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import type {
  AccountSnapshot, AppState, BackendInfo, CredentialsState, TraderConfig,
} from '../../shared/types';

// Central store for the renderer: mirrors main-process app state, config,
// backend health, credentials and the live account snapshot. Subscribes to the
// push channels the backend emits and re-exposes a simple hook to every page.

interface Ctx {
  ready: boolean;
  state: AppState | null;
  config: TraderConfig | null;
  backend: BackendInfo | null;
  credentials: CredentialsState | null;
  account: AccountSnapshot | null;
  updateConfig: (patch: Partial<TraderConfig>) => Promise<void>;
  refreshCredentials: () => Promise<void>;
}

const AppStateContext = createContext<Ctx | null>(null);

export function AppStateProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<AppState | null>(null);
  const [backend, setBackend] = useState<BackendInfo | null>(null);
  const [credentials, setCredentials] = useState<CredentialsState | null>(null);
  const [account, setAccount] = useState<AccountSnapshot | null>(null);
  const [ready, setReady] = useState(false);

  const refreshCredentials = useCallback(async () => {
    setCredentials(await window.kbot.credentials.status());
  }, []);

  useEffect(() => {
    let mounted = true;
    (async () => {
      const [s, b] = await Promise.all([
        window.kbot.state.get(),
        window.kbot.backend.info(),
      ]);
      if (!mounted) return;
      setState(s);
      setBackend(b);
      await refreshCredentials();
      try { setAccount(await window.kbot.data.account()); } catch { /* noop */ }
      setReady(true);
    })();

    const unsubs = [
      window.kbot.state.onChange(setState),
      window.kbot.backend.onInfo(setBackend),
      window.kbot.data.onAccount(setAccount),
      window.kbot.credentials.onChanged(() => void refreshCredentials()),
    ];
    return () => { mounted = false; unsubs.forEach((u) => u()); };
  }, [refreshCredentials]);

  const updateConfig = useCallback(async (patch: Partial<TraderConfig>) => {
    const next = await window.kbot.config.update(patch);
    setState(next);
  }, []);

  const value = useMemo<Ctx>(() => ({
    ready, state, config: state?.config ?? null, backend, credentials, account,
    updateConfig, refreshCredentials,
  }), [ready, state, backend, credentials, account, updateConfig, refreshCredentials]);

  return <AppStateContext.Provider value={value}>{children}</AppStateContext.Provider>;
}

export function useAppState(): Ctx {
  const ctx = useContext(AppStateContext);
  if (!ctx) throw new Error('useAppState must be used within AppStateProvider');
  return ctx;
}
