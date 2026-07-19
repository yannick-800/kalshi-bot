import { FolderOpen, Trash2 } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { SectionTitle } from '../components/common';
import type { LogEntry } from '../../shared/types';

const LEVEL_COLOR: Record<string, string> = {
  ERROR: 'text-kbot-loss',
  WARNING: 'text-kbot-warn',
  WARN: 'text-kbot-warn',
  INFO: 'text-kbot-muted',
  DEBUG: 'text-kbot-dim',
};

export function Logs() {
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [autoscroll, setAutoscroll] = useState(true);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    void window.kbot.logs.tail(500).then(setLogs);
    const off = window.kbot.logs.onAppend((e) => setLogs((prev) => [...prev.slice(-999), e]));
    return off;
  }, []);

  useEffect(() => {
    if (autoscroll) endRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [logs, autoscroll]);

  return (
    <div className="flex h-full flex-col space-y-3">
      <div className="flex items-center justify-between">
        <SectionTitle>Registros</SectionTitle>
        <div className="flex items-center gap-2">
          <label className="flex items-center gap-2 text-xs text-kbot-dim">
            <input type="checkbox" checked={autoscroll} onChange={(e) => setAutoscroll(e.target.checked)} />
            seguir
          </label>
          <button className="kbot-btn-default" onClick={() => window.kbot.logs.openFolder()}><FolderOpen size={14} /> Carpeta</button>
          <button className="kbot-btn-default" onClick={() => { window.kbot.logs.clear(); setLogs([]); }}><Trash2 size={14} /> Limpiar</button>
        </div>
      </div>

      <div className="kbot-card min-h-0 flex-1 overflow-y-auto p-3 font-mono text-xs leading-relaxed">
        {logs.length === 0 && <div className="text-kbot-dim">Sin registros aún.</div>}
        {logs.map((l, i) => (
          <div key={i} className="flex gap-2">
            <span className="shrink-0 text-kbot-dim">{new Date(l.ts).toLocaleTimeString()}</span>
            <span className={`shrink-0 w-16 ${LEVEL_COLOR[l.level] ?? 'text-kbot-muted'}`}>{l.level}</span>
            <span className="shrink-0 w-16 text-kbot-purple">{l.source}</span>
            <span className="whitespace-pre-wrap break-all text-white/80">{l.msg}</span>
          </div>
        ))}
        <div ref={endRef} />
      </div>
    </div>
  );
}
