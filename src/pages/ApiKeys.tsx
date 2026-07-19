import { KeyRound, ShieldCheck, Trash2 } from 'lucide-react';
import { useState } from 'react';
import { Badge, Field, SectionTitle } from '../components/common';
import { useAppState } from '../state/AppStateProvider';
import type { KalshiEnv } from '../../shared/types';

// Credential entry. The key + RSA private key are handed to the local backend,
// stored with 0600 perms on this machine, and used ONLY to sign your own Kalshi
// requests. They are never transmitted anywhere else.

export function ApiKeys() {
  const { credentials, refreshCredentials } = useAppState();
  const [env, setEnv] = useState<KalshiEnv>('demo');
  const [apiKey, setApiKey] = useState('');
  const [rsaPem, setRsaPem] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ tone: 'win' | 'loss'; text: string } | null>(null);

  const present = credentials?.[env]?.present ?? false;

  const save = async () => {
    setBusy(true); setMsg(null);
    try {
      await window.kbot.credentials.save({ apiKey: apiKey.trim(), rsaPem, env });
      await refreshCredentials();
      setApiKey(''); setRsaPem('');
      setMsg({ tone: 'win', text: `Credenciales guardadas para ${env === 'demo' ? 'demo' : 'producción'}.` });
    } catch (e: any) {
      setMsg({ tone: 'loss', text: String(e?.message || e) });
    } finally { setBusy(false); }
  };

  const test = async () => {
    setBusy(true); setMsg(null);
    const r = await window.kbot.credentials.test(env);
    setMsg(r.ok
      ? { tone: 'win', text: `Conectado — saldo ${(r as any).balanceUsd != null ? `$${(r as any).balanceUsd.toFixed(2)}` : 'ok'}.` }
      : { tone: 'loss', text: r.error || 'Falló la prueba' });
    setBusy(false);
  };

  const clear = async () => {
    setBusy(true); setMsg(null);
    await window.kbot.credentials.clear(env);
    await refreshCredentials();
    setMsg({ tone: 'win', text: `Credenciales de ${env === 'demo' ? 'demo' : 'producción'} borradas.` });
    setBusy(false);
  };

  return (
    <div className="max-w-2xl space-y-4">
      <SectionTitle><KeyRound size={14} /> Credenciales de Kalshi</SectionTitle>

      <div className="kbot-card space-y-4">
        <div className="flex items-center gap-2">
          {(['demo', 'production'] as const).map((e) => (
            <button key={e} onClick={() => setEnv(e)} className={env === e ? 'kbot-btn-primary' : 'kbot-btn-default'}>
              {e === 'demo' ? 'demo' : 'producción'}
            </button>
          ))}
          <div className="ml-auto">
            {present ? <Badge tone="win">clave guardada</Badge> : <Badge tone="neutral">sin configurar</Badge>}
          </div>
        </div>

        <Field label="ID de clave API" help="En Kalshi → Settings → API Keys.">
          <input className="kbot-input font-mono" value={apiKey} onChange={(e) => setApiKey(e.target.value)}
            placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" autoComplete="off" spellCheck={false} />
        </Field>

        <Field label="Clave privada RSA (PEM)" help="El archivo .pem que te dio Kalshi al crear la clave. Se guarda localmente y nunca sale de este equipo.">
          <textarea className="kbot-input h-40 font-mono text-xs" value={rsaPem} onChange={(e) => setRsaPem(e.target.value)}
            placeholder={'-----BEGIN RSA PRIVATE KEY-----\n...\n-----END RSA PRIVATE KEY-----'} spellCheck={false} />
        </Field>

        {msg && <div className={`text-sm ${msg.tone === 'win' ? 'text-kbot-win' : 'text-kbot-loss'}`}>{msg.text}</div>}

        <div className="flex flex-wrap gap-2">
          <button className="kbot-btn-primary" onClick={save} disabled={busy || !apiKey || !rsaPem}>Guardar</button>
          <button className="kbot-btn-default" onClick={test} disabled={busy || !present}>
            <ShieldCheck size={15} /> Probar conexión
          </button>
          {present && (
            <button className="kbot-btn-danger ml-auto" onClick={clear} disabled={busy}>
              <Trash2 size={15} /> Borrar {env === 'demo' ? 'demo' : 'producción'}
            </button>
          )}
        </div>
      </div>

      <div className="rounded-lg border border-kbot-border bg-kbot-surface/50 p-4 text-xs leading-relaxed text-kbot-dim">
        <strong className="text-kbot-muted">Privacidad:</strong> las claves se escriben en un archivo legible solo
        por tu usuario (0600) dentro de la carpeta de datos de esta app. La app no hace ninguna llamada de red salvo
        a Kalshi (y, si los activas, a feeds públicos de precios cripto). No hay telemetría ni cuenta.
      </div>
    </div>
  );
}
