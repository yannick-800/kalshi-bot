# Desplegar Kalshi Bot online (Streamlit Cloud)

Versión web de solo lectura para ver el bot corriendo online (paper trading, sin
claves). Reusa la misma lógica que la app de escritorio — no cambia nada.

## 1. Subir el código a GitHub

Desde la carpeta `kalshi-bot/`:

```bash
git init
git add .
git commit -m "Kalshi Bot + Streamlit online"
git branch -M main
git remote add origin https://github.com/TU_USUARIO/TU_REPO.git
git push -u origin main
```

(El `.gitignore` ya excluye `node_modules/`, `.venv/`, datos y credenciales.)

## 2. Desplegar en Streamlit Cloud

1. Entrá a **https://share.streamlit.io** → **Create app** / **New app**.
2. Conectá tu cuenta de GitHub y elegí el repo.
3. Configuración:
   - **Branch**: `main`
   - **Main file path**: `streamlit_app.py`
4. **Deploy**. Streamlit instala `requirements.txt` y arranca (~1-2 min).

Te queda una URL pública tipo `https://TU-APP.streamlit.app`.

## Cómo se comporta

- **Motor en vivo**: un hilo en segundo plano corre el mismo loop de la app
  (escanea → apuesta paper → resuelve). Se ve el marcador actualizándose.
- **Controles**: en la barra lateral activás/desactivás las estrategias.
- **Reinicio**: botón "Reiniciar a cero" (archiva a reserva).

## ⚠️ Límites de Streamlit Cloud (gratis)

- La app **se duerme** tras un rato sin tráfico. El auto-refresh (cada 6s)
  la mantiene despierta **mientras tengas la pestaña abierta** → dejá la
  pestaña abierta en el celu/laptop para que corra de noche.
- El disco es **efímero**: si Streamlit reinicia la app, la base se reinicia
  (los datos de la sesión se pierden). Para paper testing está perfecto.
- Para 24/7 real sin pestaña abierta, conviene un host siempre-encendido
  (Railway, Fly.io, Render) — pero para "verlo mientras duermo" con la
  pestaña abierta, Streamlit alcanza.
