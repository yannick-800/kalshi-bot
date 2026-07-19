import type { ReactNode } from 'react';

// Small shared building blocks used across pages.

export function StatCard({ label, value, sub, tone }: {
  label: string; value: ReactNode; sub?: ReactNode;
  tone?: 'win' | 'loss' | 'neutral';
}) {
  const toneClass = tone === 'win' ? 'text-kbot-win' : tone === 'loss' ? 'text-kbot-loss' : 'text-white';
  return (
    <div className="kbot-card kbot-card-hover">
      <div className="kbot-label">{label}</div>
      <div className={`mt-1 font-mono text-2xl font-semibold ${toneClass}`}>{value}</div>
      {sub != null && <div className="mt-1 text-xs text-kbot-dim">{sub}</div>}
    </div>
  );
}

export function Toggle({ checked, onChange, disabled }: {
  checked: boolean; onChange: (v: boolean) => void; disabled?: boolean;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors disabled:opacity-50 ${
        checked ? 'bg-gradient-to-r from-kbot-indigo to-kbot-pink' : 'bg-kbot-surface2 border border-kbot-border'
      }`}
      aria-pressed={checked}
    >
      <span className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${checked ? 'translate-x-6' : 'translate-x-1'}`} />
    </button>
  );
}

export function Badge({ children, tone = 'neutral' }: {
  children: ReactNode; tone?: 'win' | 'loss' | 'warn' | 'neutral' | 'info';
}) {
  const map = {
    win: 'border-kbot-win/40 bg-kbot-win/10 text-kbot-win',
    loss: 'border-kbot-loss/40 bg-kbot-loss/10 text-kbot-loss',
    warn: 'border-kbot-warn/40 bg-kbot-warn/10 text-kbot-warn',
    info: 'border-kbot-indigo/40 bg-kbot-indigo/10 text-kbot-indigo',
    neutral: 'border-kbot-border bg-kbot-surface2 text-kbot-muted',
  } as const;
  return <span className={`kbot-pill ${map[tone]}`}>{children}</span>;
}

export function EmptyState({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-kbot-border py-16 text-center">
      <div className="text-sm font-medium text-kbot-muted">{title}</div>
      {hint && <div className="mt-1 max-w-md text-xs text-kbot-dim">{hint}</div>}
    </div>
  );
}

export function SectionTitle({ children }: { children: ReactNode }) {
  return <div className="kbot-section-title">{children}</div>;
}

export function Field({ label, help, children }: { label: string; help?: string; children: ReactNode }) {
  return (
    <div>
      <label className="kbot-label">{label}</label>
      {children}
      {help && <div className="kbot-help">{help}</div>}
    </div>
  );
}
