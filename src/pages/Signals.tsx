import { useEffect, useState } from 'react';
import { Badge, EmptyState, SectionTitle } from '../components/common';
import { cents, timeAgo, usd } from '../utils/format';
import type { SignalRow } from '../../shared/types';

export function Signals() {
  const [rows, setRows] = useState<SignalRow[]>([]);
  const [filter, setFilter] = useState<'all' | 'whale' | 'momentum'>('all');

  const load = async () => {
    setRows(await window.kbot.data.signals({
      source: filter === 'all' ? undefined : filter, limit: 200,
    }));
  };

  useEffect(() => {
    void load();
    const off = window.kbot.data.onSignal(() => void load());
    const id = setInterval(load, 5000);
    return () => { off(); clearInterval(id); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filter]);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <SectionTitle>Señales</SectionTitle>
        <div className="flex gap-2">
          {(['all', 'whale', 'momentum'] as const).map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={filter === f ? 'kbot-btn-primary' : 'kbot-btn-default'}
            >
              {f === 'all' ? 'todas' : f === 'whale' ? 'ballenas' : 'momentum'}
            </button>
          ))}
        </div>
      </div>

      {rows.length === 0 ? (
        <EmptyState title="Aún no hay señales" hint="El escáner detecta órdenes grandes (ballenas) y clústeres de momentum en tiempo real sobre el feed de Kalshi." />
      ) : (
        <div className="kbot-card overflow-x-auto p-0">
          <table className="kbot-table">
            <thead>
              <tr>
                <th>Cuándo</th><th>Fuente</th><th>Mercado</th><th>Lado</th>
                <th>Precio</th><th>Conf</th><th>Edge</th><th>Tamaño</th><th>Estado</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((s) => (
                <tr key={`${s.source}-${s.id}`}>
                  <td className="whitespace-nowrap text-kbot-dim">{timeAgo(s.createdAt)}</td>
                  <td><Badge tone={s.source === 'whale' ? 'info' : 'neutral'}>{s.source === 'whale' ? 'ballena' : 'momentum'}</Badge></td>
                  <td className="max-w-xs truncate" title={s.title}>{s.title}</td>
                  <td className="uppercase">{s.direction}</td>
                  <td className="font-mono">{cents(s.priceCents)}</td>
                  <td className="font-mono">{s.confidence.toFixed(0)}</td>
                  <td className={`font-mono ${s.edgePts >= 0 ? 'text-kbot-win' : 'text-kbot-loss'}`}>{s.edgePts.toFixed(1)}</td>
                  <td className="font-mono text-kbot-dim">{s.dollarValue ? usd(s.dollarValue) : '—'}</td>
                  <td>{s.traded ? <Badge tone="win">operada</Badge> : <span className="text-kbot-dim">—</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
