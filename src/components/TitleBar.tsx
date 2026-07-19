import { useAppState } from '../state/AppStateProvider';

// Thin draggable title strip. On macOS the native traffic-light buttons
// (close / minimise / zoom) live at the top-left, so we render NO custom window
// controls — just leave the left side clear for them and show the live backend
// status on the right. The app brand lives at the top of the sidebar, below the
// traffic lights.
export function TitleBar() {
  const { backend } = useAppState();
  const status = backend?.status ?? 'stopped';
  const dot =
    status === 'running' && backend?.authOk ? 'bg-kbot-win'
      : status === 'running' ? 'bg-kbot-warn'
        : status === 'crashed' ? 'bg-kbot-loss' : 'bg-kbot-dim';
  const statusEs: Record<string, string> = {
    stopped: 'detenido', starting: 'iniciando', restarting: 'reiniciando',
    crashed: 'caído', running: 'activo',
  };

  return (
    <div className="titlebar-drag flex h-9 items-center justify-end border-b border-kbot-border bg-kbot-void pl-20 pr-4">
      <div className="flex items-center gap-2">
        <span className={`h-1.5 w-1.5 rounded-full ${dot} ${status === 'running' ? 'animate-pulse-slow' : ''}`} />
        <span className="text-[11px] text-kbot-dim">
          {status === 'running' ? (backend?.authOk ? 'conectado' : 'sin clave') : (statusEs[status] ?? status)}
        </span>
      </div>
    </div>
  );
}
