import { FlaskConical, RotateCcw } from 'lucide-react';
import { Field, SectionTitle, Toggle } from '../components/common';
import { useAppState } from '../state/AppStateProvider';
import type { TraderConfig } from '../../shared/types';

// Named strategy presets — each applies a bundle of parameters at once and
// records its name in `strategyPreset` so you can reuse it later.
const RISK_BASE: Partial<TraderConfig> = {
  minEntryPriceCents: 30, maxEntryPriceCents: 55,
  minEdgePtsWhale: 6, minEdgePtsMomentum: 6,
  minConfidenceWhale: 55, minConfidenceMomentum: 55,
  hardMaxPositionUsd: 12, maxTotalExposureFraction: 0.2, stopLossOnDay: -30,
};

const PRESETS: { name: string; desc: string; cfg: Partial<TraderConfig> }[] = [
  {
    name: 'Conservadora',
    desc: 'Banda 30-55¢, selectiva, cualquier horizonte. Pocas operaciones, foco en calidad.',
    cfg: { ...RISK_BASE, minWhaleUsd: 500, maxResolutionHours: 0 },
  },
  {
    name: 'Horizonte corto (test rápido)',
    desc: 'Solo mercados que cierran en ≤8h y umbrales de calidad más accesibles para juntar muestra rápido. Mantiene el control de pérdidas (tamaño chico + stop).',
    cfg: {
      ...RISK_BASE, minEdgePtsWhale: 3, minEdgePtsMomentum: 3,
      minConfidenceWhale: 50, minConfidenceMomentum: 50, maxEntryPriceCents: 60,
      minWhaleUsd: 300, maxResolutionHours: 8,
    },
  },
  {
    name: 'Agresiva (solo demo)',
    desc: 'Umbrales bajos, muchas operaciones. Para ver flujo — NO es una estrategia rentable.',
    cfg: {
      minEntryPriceCents: 15, maxEntryPriceCents: 85, minEdgePtsWhale: 0, minEdgePtsMomentum: 0,
      minConfidenceWhale: 30, minConfidenceMomentum: 30, hardMaxPositionUsd: 50,
      maxTotalExposureFraction: 0.35, stopLossOnDay: -50, minWhaleUsd: 300, maxResolutionHours: 0,
    },
  },
];

function NumberInput({ value, onCommit, step, min }: {
  value: number; onCommit: (v: number) => void; step?: number; min?: number;
}) {
  return (
    <input
      type="number" className="kbot-input font-mono" defaultValue={value} step={step ?? 1} min={min}
      onBlur={(e) => { const v = Number(e.target.value); if (!Number.isNaN(v)) onCommit(v); }}
    />
  );
}

export function Settings() {
  const { config, updateConfig } = useAppState();
  if (!config) return null;
  const set = (patch: Partial<TraderConfig>) => void updateConfig(patch);

  return (
    <div className="max-w-3xl space-y-6">
      <div className="flex items-center justify-between">
        <SectionTitle>Ajustes del motor</SectionTitle>
        <button className="kbot-btn-default" onClick={() => window.kbot.config.reset()}>
          <RotateCcw size={14} /> Restablecer
        </button>
      </div>

      <div className="kbot-card space-y-3">
        <div className="kbot-label flex items-center gap-2"><FlaskConical size={13} /> Preset de estrategia</div>
        <div className="grid gap-2 sm:grid-cols-3">
          {PRESETS.map((p) => {
            const active = config.strategyPreset === p.name;
            return (
              <button
                key={p.name}
                onClick={() => set({ ...p.cfg, strategyPreset: p.name })}
                className={`rounded-lg border p-3 text-left transition-colors ${
                  active
                    ? 'border-kbot-purple bg-kbot-purple/10 shadow-kbot-soft'
                    : 'border-kbot-border bg-kbot-surface2 hover:border-kbot-borderHi'
                }`}
              >
                <div className={`text-sm font-semibold ${active ? 'text-white' : 'text-kbot-muted'}`}>
                  {p.name}{active && ' ✓'}
                </div>
                <div className="mt-1 text-[11px] leading-snug text-kbot-dim">{p.desc}</div>
              </button>
            );
          })}
        </div>
        <div className="text-[11px] text-kbot-dim">
          Preset activo: <span className="text-kbot-muted">{config.strategyPreset || '—'}</span>.
          Cambiar un valor manual abajo no altera el nombre del preset.
        </div>
      </div>

      <div className="kbot-card space-y-4">
        <div className="kbot-label">Entorno</div>
        <div className="flex items-center gap-2">
          {(['demo', 'production'] as const).map((e) => (
            <button key={e} className={config.kalshiEnv === e ? 'kbot-btn-primary' : 'kbot-btn-default'}
              onClick={() => set({ kalshiEnv: e })}>
              {e === 'demo' ? 'demo' : 'producción'}
            </button>
          ))}
          {config.kalshiEnv === 'production' && (
            <span className="text-xs text-kbot-loss">Dinero real — las órdenes son en vivo cuando actives operar.</span>
          )}
        </div>
      </div>

      <div className="kbot-card space-y-4">
        <div className="kbot-label">Motores de señal</div>
        <Row label="Operar ballenas" desc="Actuar sobre órdenes grandes del feed.">
          <Toggle checked={config.tradeWhales} onChange={(v) => set({ tradeWhales: v })} />
        </Row>
        <Row label="Operar momentum" desc="Actuar sobre clústeres de volumen/precio.">
          <Toggle checked={config.tradeMomentum} onChange={(v) => set({ tradeMomentum: v })} />
        </Row>
        <Row label="Solo contrarian" desc="Ir contra la multitud en señales de momentum.">
          <Toggle checked={config.contrarianOnly} onChange={(v) => set({ contrarianOnly: v })} />
        </Row>
        <Row label="Edge neto de comisiones" desc="Restar la comisión de Kalshi antes del filtro de edge.">
          <Toggle checked={config.feeAwareEdge} onChange={(v) => set({ feeAwareEdge: v })} />
        </Row>
      </div>

      <div className="kbot-card space-y-4">
        <div className="kbot-label flex items-center gap-2">
          <FlaskConical size={13} /> Motores predictivos (experimental)
        </div>
        <p className="-mt-2 text-xs text-kbot-dim">
          En vez de imitar ballenas, generan su PROPIA probabilidad desde una fuente externa en tiempo
          real y la comparan con el precio de Kalshi. Solo operan cuando el mercado va rezagado — ahí
          está el edge real.
        </p>
        <Row
          label="🎾 Tenis favorito (90% set decisivo)"
          desc="El plan de Polymarket: apuesta al FAVORITO del mercado (≥90%) cuando el partido llega al 3er set (decisivo). Solo hombres (ATP). No le gana al mercado con un modelo — aprovecha que los favoritos fuertes suelen estar levemente subvaluados. Cobra poco pero acierta mucho."
        >
          <Toggle checked={config.tennisFavoriteEnabled} onChange={(v) => set({ tennisFavoriteEnabled: v })} />
        </Row>
        <Row
          label="🎾 Tenis en vivo (modelo)"
          desc="Lee el marcador de ESPN, estima la probabilidad de cada jugador y apuesta cuando Kalshi va rezagado tras un quiebre. Busca rezagos del mercado, no favoritos."
        >
          <Toggle checked={config.tennisSignalEnabled} onChange={(v) => set({ tennisSignalEnabled: v })} />
        </Row>
        <Row
          label="₿ Cripto spot (BTC/ETH/SOL)"
          desc="Sigue el precio spot real (Coinbase) y proyecta si estará arriba/abajo del strike al cierre de los mercados de 15 min. Apuesta cuando el precio de Kalshi va rezagado del spot."
        >
          <Toggle checked={config.cryptoSignalEnabled} onChange={(v) => set({ cryptoSignalEnabled: v })} />
        </Row>
        <div className="rounded-md border border-kbot-warn/30 bg-kbot-warn/5 p-3 text-[11px] leading-relaxed text-kbot-warn/90">
          ⚠️ Experimentales, sin edge probado — hay que validarlos con datos. Se activan solos cuando
          hay partidos/mercados en vivo. Mantienen todos los controles de riesgo (tamaño chico, stop
          diario, 1 apuesta por evento).
        </div>
      </div>

      <div className="kbot-card grid grid-cols-2 gap-4">
        <Field label="$ mínimo ballena" ><NumberInput value={config.minWhaleUsd} step={100} onCommit={(v) => set({ minWhaleUsd: v })} /></Field>
        <Field label="Confianza mínima (ballena)"><NumberInput value={config.minConfidenceWhale} onCommit={(v) => set({ minConfidenceWhale: v })} /></Field>
        <Field label="Edge mínimo pts (ballena)"><NumberInput value={config.minEdgePtsWhale} onCommit={(v) => set({ minEdgePtsWhale: v })} /></Field>
        <Field label="Edge mínimo pts (momentum)"><NumberInput value={config.minEdgePtsMomentum} onCommit={(v) => set({ minEdgePtsMomentum: v })} /></Field>
        <Field label="Precio entrada mín (¢)"><NumberInput value={config.minEntryPriceCents} onCommit={(v) => set({ minEntryPriceCents: v })} /></Field>
        <Field label="Precio entrada máx (¢)"><NumberInput value={config.maxEntryPriceCents} onCommit={(v) => set({ maxEntryPriceCents: v })} /></Field>
      </div>

      <div className="kbot-card space-y-4">
        <div className="kbot-label">Tamaño de posición</div>
        <div className="flex items-center gap-2">
          {(['percent', 'fixed'] as const).map((m) => (
            <button key={m} className={config.sizingMode === m ? 'kbot-btn-primary' : 'kbot-btn-default'}
              onClick={() => set({ sizingMode: m })}>
              {m === 'percent' ? 'porcentaje' : 'fijo'}
            </button>
          ))}
        </div>
        <div className="grid grid-cols-2 gap-4">
          <Field label="$ por operación (fijo)"><NumberInput value={config.fixedTradeUsd} step={1} onCommit={(v) => set({ fixedTradeUsd: v })} /></Field>
          <Field label="$ máximo por posición"><NumberInput value={config.hardMaxPositionUsd} step={5} onCommit={(v) => set({ hardMaxPositionUsd: v })} /></Field>
        </div>
      </div>

      <div className="kbot-card grid grid-cols-2 gap-4">
        <Field label="Máx. posiciones abiertas"><NumberInput value={config.maxOpenPositions} onCommit={(v) => set({ maxOpenPositions: v })} /></Field>
        <Field label="Máx. nuevas posiciones/día"><NumberInput value={config.maxDailyNewPositions} onCommit={(v) => set({ maxDailyNewPositions: v })} /></Field>
        <Field label="Stop-loss diario $" help="Negativo lo activa (ej. -50)."><NumberInput value={config.stopLossOnDay} step={5} onCommit={(v) => set({ stopLossOnDay: v })} /></Field>
        <Field label="Take-profit diario $" help="Positivo lo activa; 0 = apagado."><NumberInput value={config.takeProfitOnDay} step={5} onCommit={(v) => set({ takeProfitOnDay: v })} /></Field>
      </div>
    </div>
  );
}

function Row({ label, desc, children }: { label: string; desc: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4">
      <div>
        <div className="text-sm text-white">{label}</div>
        <div className="text-xs text-kbot-dim">{desc}</div>
      </div>
      {children}
    </div>
  );
}
