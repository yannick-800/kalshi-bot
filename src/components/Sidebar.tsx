import {
  Activity, KeyRound, LayoutDashboard, ScrollText, Settings, Wallet,
} from 'lucide-react';
import type { ComponentType } from 'react';

export type PageId = 'dashboard' | 'signals' | 'positions' | 'apikeys' | 'settings' | 'logs';

const NAV: { id: PageId; label: string; icon: ComponentType<{ size?: number }> }[] = [
  { id: 'dashboard', label: 'Panel', icon: LayoutDashboard },
  { id: 'signals', label: 'Señales', icon: Activity },
  { id: 'positions', label: 'Posiciones', icon: Wallet },
  { id: 'apikeys', label: 'Claves API', icon: KeyRound },
  { id: 'settings', label: 'Ajustes', icon: Settings },
  { id: 'logs', label: 'Registros', icon: ScrollText },
];

export function Sidebar({ page, onNavigate }: { page: PageId; onNavigate: (p: PageId) => void }) {
  return (
    <nav className="flex w-52 shrink-0 flex-col border-r border-kbot-border bg-kbot-void p-3">
      <div className="mb-5 flex flex-col gap-1.5 px-2 pt-1">
        <span className="bg-kbot-glow bg-clip-text font-pixel text-[12px] leading-none text-transparent">
          KALSHI BOT
        </span>
        <span className="font-pixel text-[8px] text-kbot-dim">v1.0.0</span>
      </div>
      <ul className="flex flex-col gap-1">
        {NAV.map(({ id, label, icon: Icon }) => {
          const active = page === id;
          return (
            <li key={id}>
              <button
                onClick={() => onNavigate(id)}
                className={`flex w-full items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors ${
                  active
                    ? 'border border-kbot-border bg-kbot-surface text-white shadow-kbot-soft'
                    : 'text-kbot-muted hover:bg-white/5 hover:text-white'
                }`}
              >
                <Icon size={16} />
                <span>{label}</span>
              </button>
            </li>
          );
        })}
      </ul>
      <div className="mt-auto px-2 pt-4 text-[10px] leading-relaxed text-kbot-dim">
        Local · tus claves no salen de este equipo
      </div>
    </nav>
  );
}
