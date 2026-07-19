export function usd(n: number | null | undefined, signed = false): string {
  if (n == null || Number.isNaN(n)) return '—';
  const s = Math.abs(n).toLocaleString('es-ES', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const sign = n < 0 ? '-' : signed ? '+' : '';
  return `${sign}$${s}`;
}

export function pct(n: number | null | undefined, signed = false): string {
  if (n == null || Number.isNaN(n)) return '—';
  const sign = n > 0 && signed ? '+' : '';
  return `${sign}${n.toFixed(1)}%`;
}

export function cents(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return '—';
  return `${Math.round(n)}¢`;
}

export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return '—';
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return '—';
  const secs = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (secs < 60) return `hace ${secs}s`;
  if (secs < 3600) return `hace ${Math.floor(secs / 60)}m`;
  if (secs < 86400) return `hace ${Math.floor(secs / 3600)}h`;
  return `hace ${Math.floor(secs / 86400)}d`;
}
