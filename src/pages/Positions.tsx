import { ExternalLink, RotateCcw } from 'lucide-react';
import { useEffect, useState } from 'react';
import { Badge, EmptyState, SectionTitle } from '../components/common';
import { useAppState } from '../state/AppStateProvider';
import { cents, timeAgo, usd } from '../utils/format';
import type { AccountSnapshot, BotPosition } from '../../shared/types';

function Summary({ a }: { a: AccountSnapshot | null }) {
  const wins = a?.wins ?? 0;
  const losses = a?.losses ?? 0;
  const winsUsd = a?.winsUsd ?? 0;
  const lossesUsd = Math.abs(a?.lossesUsd ?? 0);
  const open = (a?.openCount ?? 0) + (a?.pendingCount ?? 0);
  const net = (a?.realizedPnlUsd ?? 0);
  const total = winsUsd + lossesUsd;
  const winShare = total > 0 ? (winsUsd / total) * 100 : 50;

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <div className="kbot-card border-kbot-win/30">
          <div className="kbot-label">Ganadas</div>
          <div className="mt-1 font-mono text-2xl font-semibold text-kbot-win">{wins}</div>
          <div className="mt-0.5 text-xs text-kbot-win/80">{usd(winsUsd, true)}</div>
        </div>
        <div className="kbot-card border-kbot-loss/30">
          <div className="kbot-label">Perdidas</div>
          <div className="mt-1 font-mono text-2xl font-semibold text-kbot-loss">{losses}</div>
          <div className="mt-0.5 text-xs text-kbot-loss/80">-{usd(lossesUsd)}</div>
        </div>
        <div className="kbot-card">
          <div className="kbot-label">Abiertas</div>
          <div className="mt-1 font-mono text-2xl font-semibold text-white">{open}</div>
          <div className="mt-0.5 text-xs text-kbot-dim">en juego</div>
        </div>
        <div className="kbot-card">
          <div className="kbot-label">Neto</div>
          <div className={`mt-1 font-mono text-2xl font-semibold ${net >= 0 ? 'text-kbot-win' : 'text-kbot-loss'}`}>
            {usd(net, true)}
          </div>
          <div className="mt-0.5 text-xs text-kbot-dim">
            {wins + losses > 0 ? `${((wins / (wins + losses)) * 100).toFixed(0)}% acierto` : '—'}
          </div>
        </div>
      </div>

      {total > 0 && (
        <div>
          <div className="mb-1 flex justify-between text-xs">
            <span className="text-kbot-win">Ganado {usd(winsUsd)}</span>
            <span className="text-kbot-loss">Perdido {usd(lossesUsd)}</span>
          </div>
          <div className="flex h-2.5 overflow-hidden rounded-full bg-kbot-surface2">
            <div className="bg-kbot-win" style={{ width: `${winShare}%` }} />
            <div className="bg-kbot-loss" style={{ width: `${100 - winShare}%` }} />
          </div>
        </div>
      )}
    </div>
  );
}

function formatClose(iso: string): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleString('es-ES', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
}

const STATUS_ES: Record<string, string> = {
  filled: 'ejecutada', partial: 'parcial', submitted: 'enviada',
  resolved: 'resuelta', canceled: 'cancelada', error: 'error',
};

function statusTone(s: string): 'win' | 'loss' | 'warn' | 'neutral' | 'info' {
  if (s === 'filled') return 'info';
  if (s === 'partial' || s === 'submitted') return 'warn';
  if (s === 'error' || s === 'canceled') return 'loss';
  return 'neutral';
}

// Status cell: a RESOLVED position is coloured by outcome (ganada = green,
// perdida = red) so wins/losses jump out. Otherwise show the working state.
function StatusCell({ p }: { p: BotPosition }) {
  if (p.status === 'stopped') return <Badge tone="loss">cortada ✂</Badge>;
  if (p.resolved) {
    const won = p.outcomeCorrect === 1;
    return <Badge tone={won ? 'win' : 'loss'}>{won ? 'ganada ✓' : 'perdida ✗'}</Badge>;
  }
  return <Badge tone={statusTone(p.status)}>{STATUS_ES[p.status] ?? p.status}</Badge>;
}

export function Positions() {
  const { account } = useAppState();
  const [rows, setRows] = useState<BotPosition[]>([]);
  const [showResolved, setShowResolved] = useState(false);

  const load = async () => {
    setRows(await window.kbot.data.positions({ resolved: showResolved ? undefined : false, limit: 300 }));
  };

  const handleReset = async () => {
    if (!window.confirm(
      '¿Reiniciar todas las posiciones a cero?\n\nSe archivan en reserva (no se pierden, quedan para análisis) y el marcador vuelve a empezar.'
    )) return;
    await window.kbot.app.resetPaper();
    void load();
  };

  useEffect(() => {
    void load();
    const off = window.kbot.data.onPosition(() => void load());
    const id = setInterval(load, 4000);
    return () => { off(); clearInterval(id); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showResolved]);

  return (
    <div className="space-y-4">
      <Summary a={account} />

      <div className="flex items-center justify-between">
        <SectionTitle>Posiciones</SectionTitle>
        <div className="flex gap-2">
          <button className={showResolved ? 'kbot-btn-primary' : 'kbot-btn-default'} onClick={() => setShowResolved((v) => !v)}>
            {showResolved ? 'Mostrando todas' : 'Ver resueltas'}
          </button>
          <button className="kbot-btn-danger" onClick={handleReset} title="Archiva a reserva y vuelve a cero">
            <RotateCcw size={14} /> Reiniciar a cero
          </button>
        </div>
      </div>

      {rows.length === 0 ? (
        <EmptyState title="Sin posiciones" hint="Cuando una señal pasa todos los filtros y las operaciones están activas, las órdenes aparecen aquí con su ejecución y P&L en vivo." />
      ) : (
        <div className="kbot-card overflow-x-auto p-0">
          <table className="kbot-table">
            <thead>
              <tr>
                <th>Abierta</th><th>Tipo</th><th>Mercado</th><th>Puesta</th><th>Lado</th><th>Cierre</th>
                <th>Ejecutado</th><th>Precio</th><th>Costo</th><th>P&L</th><th>Estado</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((p) => {
                const pnl = p.resolved ? p.pnlUsd : p.livePnlUsd;
                const up = (pnl ?? 0) >= 0;
                // Whole-row tint: strong when resolved, subtle while still open.
                const rowTint = pnl == null
                  ? ''
                  : p.resolved
                    ? (up ? 'bg-kbot-win/10' : 'bg-kbot-loss/10')
                    : (up ? 'bg-kbot-win/[0.04]' : 'bg-kbot-loss/[0.04]');
                return (
                  <tr key={p.id} className={rowTint}>
                    <td className="whitespace-nowrap text-kbot-dim">{timeAgo(p.createdAt)}</td>
                    <td>{p.mtype && p.mtype !== '—' ? <Badge tone="info">{p.mtype}</Badge> : <span className="text-kbot-dim">—</span>}</td>
                    <td className="max-w-[16rem]">
                      <button
                        onClick={() => window.kbot.app.openExternal(p.marketUrl)}
                        title={`${p.title || p.ticker} — abrir en Kalshi`}
                        className="inline-flex min-w-0 max-w-full items-center gap-1 text-left text-kbot-indigo transition-colors hover:text-kbot-purple hover:underline"
                      >
                        <span className="truncate">{p.eventTitle || p.title || p.ticker}</span>
                        <ExternalLink size={11} className="shrink-0 opacity-60" />
                      </button>
                    </td>
                    <td className="max-w-[12rem] truncate" title={p.yesLabel}>{p.yesLabel || '—'}</td>
                    <td className="uppercase">{p.direction}</td>
                    <td className="whitespace-nowrap text-kbot-dim">
                      {formatClose(p.resolved ? (p.resolvedAt || p.closeTime) : p.closeTime)}
                    </td>
                    <td className="font-mono">{p.filledContracts}/{p.targetContracts}</td>
                    <td className="font-mono">{cents(p.limitPriceCents)}</td>
                    <td className="font-mono text-kbot-dim">{usd(p.costUsd)}</td>
                    <td>
                      {pnl != null ? (
                        <span className={`inline-block rounded px-2 py-0.5 font-mono font-semibold ${
                          up ? 'bg-kbot-win/15 text-kbot-win' : 'bg-kbot-loss/15 text-kbot-loss'
                        }`}>
                          {usd(pnl, true)}
                        </span>
                      ) : (
                        <span className="text-kbot-dim">—</span>
                      )}
                    </td>
                    <td><StatusCell p={p} /></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
