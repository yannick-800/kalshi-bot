#!/usr/bin/env bash
# Corre Kalshi Bot en tu Mac, con los datos guardados en disco REAL.
#
# La diferencia con Streamlit Cloud: alla la base vive en /tmp y se borra en
# cada reinicio. Aca la apuntamos a ./userdata, que persiste — reinicies la
# app o la Mac, las apuestas y los ajustes siguen ahi.
set -e
cd "$(dirname "$0")"

# Datos persistentes en el proyecto (el codigo respeta esta variable).
export KALSHI_BOT_USERDATA="$(pwd)/userdata"
mkdir -p "$KALSHI_BOT_USERDATA"

# Entorno aislado, se crea la primera vez y se reutiliza despues.
if [ ! -d ".venv" ]; then
  echo "Creando entorno (solo la primera vez)..."
  python3 -m venv .venv
fi
source .venv/bin/activate
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt

echo ""
echo "  Kalshi Bot corriendo en:  http://localhost:8502"
echo "  Datos guardados en:       $KALSHI_BOT_USERDATA"
echo "  Para frenarlo: Ctrl+C en esta ventana."
echo ""

python -m streamlit run streamlit_app.py \
  --server.port 8502 \
  --server.headless true \
  --browser.gatherUsageStats false
