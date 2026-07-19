import { CheckCircle2, RefreshCw, XCircle } from 'lucide-react';
import { useEffect, useState } from 'react';
import {
  Area, AreaChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { SectionTitle, StatCard } from '../components/common';
import { useAppState } from '../state/AppStateProvider';
import { usd } from '../utils/format';
import type { PnlPoint, ScannerStats, TradingStatus } from '../../shared/types';

export function Dashboard() {
  const { account } = useAppState();
  const [pnl, setPnl] = useState<PnlPoint[]>([]);
  const [scanner, setScanner] = useState<ScannerStats | null>(null);
  const [status, setStatus] = useState<TradingStatus | null>(null);

  const load = async () => {
    const [p, s, t] = await Promise.all([
      window.kbot.data.pnlSeries(168),
      window.kbot.data.scannerStats(),
      window.kbot.trading.status(),
    ]);
    setPnl(p ?? []);
    setScanner(s);
    setStatus(t);
  };

  useEffect(() => {
    void load();
    const id = setInterval(load, 4000);
    return () => clearInterval(id);
  }, []);

  const wl = (account?.wins ?? 0) + (account?.losses ?? 0);

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard label="Balance total" value={usd(account?.totalUsd)} sub={`efectivo ${usd(account?.cashUsd)}`} />
        <StatCard
          label="P&L realizado"
          value={usd(account?.realizedPnlUsd, true)}
          tone={(account?.realizedPnlUsd ?? 0) >= 0 ? 'win' : 'loss'}
          sub={`comisiones ${usd(account?.feesUsd)}`}
        />
        <StatCard
          label="Tasa de acierto"
          value={wl ? `${(account?.winRate ?? 0).toFixed(0)}%` : '—'}
          sub={`${account?.wins ?? 0}G · ${account?.losses ?? 0}P`}
        />
        <StatCard label="Posiciones abiertas" value={account?.openCount ?? 0} sub={`${account?.pendingCount ?? 0} pendientes`} />
      </div>

      <div className="kbot-card">
        <SectionTitle>Balance (7 días)</SectionTitle>
        {pnl.length > 1 ? (
          <ResponsiveContainer width="100%" height={220}>
            <AreaChart data={pnl.map((p) => ({ t: new Date(p.at).getTime(), v: p.totalUsd }))}>
              <defs>
                <linearGradient id="g" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#A855F7" stopOpacity={0.5} />
                  <stop offset="100%" stopColor="#A855F7" stopOpacity={0} />
                </linearGradient>
              </defs>
              <XAxis dataKey="t" hide />
              <YAxis hide domain={['dataMin', 'dataMax']} />
              <Tooltip
                contentStyle={{ background: '#11111A', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 8, fontSize: 12 }}
                labelFormatter={(t) => new Date(t as number).toLocaleString('es-ES')}
                formatter={(v) => [usd(v as number), 'Total']}
              />
              <Area type="monotone" dataKey="v" stroke="#A855F7" strokeWidth={2} fill="url(#g)" />
            </AreaChart>
          </ResponsiveContainer>
        ) : (
          <div className="flex h-[220px] items-center justify-center text-sm text-kbot-dim">
            Aún no hay datos suficientes — las mediciones se acumulan mientras el backend corre.
          </div>
        )}
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <div className="kbot-card">
          <SectionTitle>¿Por qué no opera?</SectionTitle>
          <ul className="space-y-2">
            {(status?.checks ?? []).map((c, i) => (
              <li key={i} className="flex items-center gap-2 text-sm">
                {c.ok ? <CheckCircle2 size={16} className="text-kbot-win" /> : <XCircle size={16} className="text-kbot-loss" />}
                <span className={c.ok ? 'text-white/90' : 'text-kbot-muted'}>{c.label}</span>
                {c.detail && <span className="text-xs text-kbot-dim">— {c.detail}</span>}
              </li>
            ))}
            {!status?.checks?.length && <li className="text-sm text-kbot-dim">Esperando al backend…</li>}
          </ul>
        </div>

        <div className="kbot-card">
          <div className="flex items-center justify-between">
            <SectionTitle>Escáner</SectionTitle>
            <button className="kbot-btn-ghost" onClick={() => window.kbot.backend.runOnce('syncMarkets')}>
              <RefreshCw size={14} /> Sincronizar mercados
            </button>
          </div>
          <div className="grid grid-cols-3 gap-3 text-center">
            <div>
              <div className="font-mono text-xl font-semibold">{scanner?.marketsTracked ?? 0}</div>
              <div className="text-xs text-kbot-dim">mercados</div>
            </div>
            <div>
              <div className="font-mono text-xl font-semibold">{scanner?.whales.total ?? 0}</div>
              <div className="text-xs text-kbot-dim">ballenas</div>
            </div>
            <div>
              <div className="font-mono text-xl font-semibold">{scanner?.momentum.total ?? 0}</div>
              <div className="text-xs text-kbot-dim">momentum</div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
