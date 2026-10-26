#!/usr/bin/env bash
# Desinstala Libreta. Tus notas y respaldos NO se borran salvo que pases --borrar-datos.
set -uo pipefail
DATOS="${XDG_DATA_HOME:-$HOME/.local/share}/libreta"

systemctl --user disable --now libreta.service >/dev/null 2>&1
rm -f "$HOME/.config/systemd/user/libreta.service"
systemctl --user daemon-reload >/dev/null 2>&1
rm -f "$HOME/.local/bin/libreta"
rm -f "${XDG_DATA_HOME:-$HOME/.local/share}/applications/libreta.desktop"
rm -f "${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps/libreta.svg"
rm -rf "$DATOS/app"

CFG="$HOME/.gemini/antigravity/mcp_config.json"
if [[ -f "$CFG" ]]; then
  python3 - "$CFG" <<'EOF'
import json, sys
p = sys.argv[1]
try:
    cfg = json.load(open(p, encoding="utf-8"))
except ValueError:
    sys.exit(0)
if cfg.get("mcpServers", {}).pop("libreta", None) is not None:
    json.dump(cfg, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("Quitado de la configuración de Antigravity")
EOF
fi

if [[ "${1:-}" == "--borrar-datos" ]]; then
  rm -rf "$DATOS"
  echo "Libreta y TODOS sus datos eliminados."
else
  echo "Libreta desinstalada. Tus notas siguen en $DATOS"
fi
