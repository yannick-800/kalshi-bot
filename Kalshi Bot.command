#!/usr/bin/env bash
# Doble clic desde Finder para arrancar Kalshi Bot local.
# Abre una Terminal, corre la app y despues abre el navegador.
DIR="$(cd "$(dirname "$0")" && pwd)"
osascript -e 'tell application "Terminal" to do script "cd \"'"$DIR"'\"; bash run_local.sh"'
sleep 8
open "http://localhost:8502"
