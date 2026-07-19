import { AlertTriangle, Power, ShieldAlert } from 'lucide-react';
import { useState } from 'react';
import { Badge, Toggle } from './components/common';
import { Sidebar, type PageId } from './components/Sidebar';
import { TitleBar } from './components/TitleBar';
import { ApiKeys } from './pages/ApiKeys';
import { Dashboard } from './pages/Dashboard';
import { Logs } from './pages/Logs';
import { Positions } from './pages/Positions';
import { Settings } from './pages/Settings';
import { Signals } from './pages/Signals';
import { useAppState } from './state/AppStateProvider';

function TopBar() {
  const { config, backend, updateConfig } = useAppState();
  const [busy, setBusy] = useState(false);
  if (!config) return null;

  const env = config.kalshiEnv;
  const isProd = env === 'production';
  const trading = config.enableTrading;

  const toggleTrading = async (v: boolean) => {
    setBusy(true);
    try { await window.kbot.trading.setEnabled(v); await updateConfig({ enableTrading: v }); }
    finally { setBusy(false); }
  };

  const killSwitch = async () => {
    setBusy(true);
    try { await window.kbot.trading.setEnabled(false); await updateConfig({ enableTrading: false }); await window.kbot.trading.cancelAllOpen(); }
    finally { setBusy(false); }
  };

  return (
    <header className="flex items-center justify-between gap-3 border-b border-kbot-border bg-kbot-void/80 px-5 py-3 backdrop-blur">
      <div className="flex items-center gap-2">
        <Badge tone={isProd ? 'loss' : 'info'}>{isProd ? 'PRODUCCIÓN · dinero real' : 'DEMO'}</Badge>
        {!backend?.authOk && <Badge tone="warn">sin autenticar</Badge>}
        {trading ? <Badge tone="win">operando en vivo</Badge> : <Badge tone="neutral">simulación</Badge>}
      </div>
      <div className="flex items-center gap-3">
        <button className="kbot-btn-danger" onClick={killSwitch} disabled={busy}>
          <ShieldAlert size={15} /> Parar todo
        </button>
        <div className="flex items-center gap-2 rounded-md border border-kbot-border bg-kbot-surface px-3 py-1.5">
          <Power size={15} className={trading ? 'text-kbot-win' : 'text-kbot-dim'} />
          <span className="text-sm text-kbot-muted">Operar</span>
          <Toggle checked={trading} onChange={toggleTrading} disabled={busy || !backend?.authOk} />
        </div>
      </div>
    </header>
  );
}

function DisclaimerGate({ onAccept }: { onAccept: () => void }) {
  return (
    <div className="flex h-full items-center justify-center bg-kbot-radial p-8">
      <div className="kbot-card max-w-xl">
        <div className="mb-3 flex items-center gap-2 text-kbot-warn">
          <AlertTriangle size={18} />
          <h2 className="text-lg font-semibold">Antes de empezar</h2>
        </div>
        <div className="space-y-3 text-sm text-kbot-muted">
          <p>
            Kalshi Bot puede colocar <strong className="text-white">órdenes reales</strong> en tu cuenta de Kalshi.
            Operar conlleva riesgo financiero real. Las estrategias incluidas son heurísticas
            <strong className="text-white"> sin edge probado neto de comisiones</strong> y pueden perder dinero.
            Esto <strong className="text-white">no es asesoría financiera</strong>.
          </p>
          <p>
            Arranca en el entorno <strong className="text-white">demo</strong> de Kalshi con las operaciones
            <strong className="text-white"> apagadas</strong>. Nada opera de verdad hasta que cambies el
            entorno a producción <em>y</em> actives las operaciones — dos pasos deliberados.
          </p>
          <p className="text-xs text-kbot-dim">
            Tu clave API y tu clave privada RSA se guardan localmente en este equipo y nunca se envían a
            ningún lado salvo a Kalshi para firmar tus propias peticiones.
          </p>
        </div>
        <button className="kbot-btn-primary mt-5 w-full" onClick={onAccept}>Entiendo — continuar</button>
      </div>
    </div>
  );
}

export default function App() {
  const { ready, state } = useAppState();
  const [page, setPage] = useState<PageId>('dashboard');

  if (!ready || !state) {
    return (
      <div className="flex h-full items-center justify-center bg-kbot-void text-kbot-dim">
        <span className="animate-pulse-slow font-pixel text-[10px]">CARGANDO…</span>
      </div>
    );
  }

  const accept = async () => { await window.kbot.state.acceptDisclaimer(); };

  return (
    <div className="flex h-full flex-col bg-kbot-void text-white">
      <TitleBar />
      {!state.disclaimerAccepted ? (
        <DisclaimerGate onAccept={accept} />
      ) : (
        <div className="flex min-h-0 flex-1">
          <Sidebar page={page} onNavigate={setPage} />
          <div className="flex min-h-0 flex-1 flex-col bg-kbot-radial">
            <TopBar />
            <main className="min-h-0 flex-1 overflow-y-auto p-6">
              <div className="animate-fade-in">
                {page === 'dashboard' && <Dashboard />}
                {page === 'signals' && <Signals />}
                {page === 'positions' && <Positions />}
                {page === 'apikeys' && <ApiKeys />}
                {page === 'settings' && <Settings />}
                {page === 'logs' && <Logs />}
              </div>
            </main>
          </div>
        </div>
      )}
    </div>
  );
}
