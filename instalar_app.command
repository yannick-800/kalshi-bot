#!/usr/bin/env bash
# Doble clic para (re)crear el icono "Kalshi Bot" en Aplicaciones.
# El .app es solo un lanzador: arranca streamlit desde esta carpeta con los
# datos en ./userdata. Correlo de nuevo si cambiás de Mac o borrás el icono.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
APP="/Applications/Kalshi Bot.app"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$DIR/assets/AppIcon.icns" "$APP/Contents/Resources/AppIcon.icns"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleExecutable</key><string>launcher</string>
  <key>CFBundleIdentifier</key><string>com.manah.kalshibot</string>
  <key>CFBundleName</key><string>Kalshi Bot</string>
  <key>CFBundleDisplayName</key><string>Kalshi Bot</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>LSUIElement</key><false/>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST

cat > "$APP/Contents/MacOS/launcher" <<LAUNCH
#!/bin/bash
DIR="$DIR"
cd "\$DIR"
export KALSHI_BOT_USERDATA="\$DIR/userdata"
mkdir -p "\$KALSHI_BOT_USERDATA"
PORT=8502
LOG=/tmp/kalshi_bot.log

PID=\$(lsof -ti :\$PORT -sTCP:LISTEN 2>/dev/null)
if [ -n "\$PID" ]; then
  PID_START=\$(ps -o lstart= -p "\$PID" 2>/dev/null | xargs -I{} date -j -f "%a %b %d %T %Y" "{}" "+%s" 2>/dev/null)
  CODE_MTIME=\$(stat -f "%m" streamlit_app.py)
  if [ -z "\$PID_START" ] || [ "\$CODE_MTIME" -gt "\$PID_START" ]; then
    kill "\$PID" 2>/dev/null; sleep 1
  fi
fi

if ! lsof -i :\$PORT -sTCP:LISTEN &>/dev/null; then
  [ -d ".venv" ] || /usr/bin/python3 -m venv .venv
  .venv/bin/python -m pip install --quiet --upgrade pip >>"\$LOG" 2>&1
  .venv/bin/python -m pip install --quiet -r requirements.txt >>"\$LOG" 2>&1
  nohup .venv/bin/python -m streamlit run streamlit_app.py \\
    --server.port \$PORT --server.headless true \\
    --browser.gatherUsageStats false >>"\$LOG" 2>&1 &
  for i in \$(seq 1 120); do sleep 0.5; lsof -i :\$PORT -sTCP:LISTEN &>/dev/null && break; done
fi
open "http://localhost:\$PORT"
LAUNCH

chmod +x "$APP/Contents/MacOS/launcher"
touch "$APP"
echo "Listo. Icono 'Kalshi Bot' creado en Aplicaciones."
open -R "$APP"
