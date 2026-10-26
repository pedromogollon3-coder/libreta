#!/usr/bin/env bash
# Instalador de Libreta para Linux Mint (y otras distribuciones con systemd).
# Uso:  bash instalar.sh            instala y pregunta por Antigravity
#       bash instalar.sh --sin-servicio   no arranca el servidor al iniciar sesión
set -euo pipefail

ORIGEN="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATOS="${XDG_DATA_HOME:-$HOME/.local/share}/libreta"
APP="$DATOS/app"
BIN="$HOME/.local/bin"
PUERTO="${LIBRETA_PUERTO:-8765}"
CON_SERVICIO=1
[[ "${1:-}" == "--sin-servicio" ]] && CON_SERVICIO=0

say() { printf '\033[1;35m»\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

# 1. Requisitos: Python 3.9+ con SQLite FTS5 (Linux Mint 21/22 ya lo traen).
command -v python3 >/dev/null || fail "Falta python3. Instálalo con: sudo apt install python3"
PY="$(command -v python3)"
"$PY" - <<'EOF' || fail "Tu Python no sirve (se necesita 3.9+ con SQLite FTS5)."
import sys, sqlite3
assert sys.version_info >= (3, 9), "Python demasiado antiguo"
sqlite3.connect(":memory:").execute("CREATE VIRTUAL TABLE t USING fts5(x)")
EOF
say "Python correcto: $("$PY" --version)"

# 2. Copiar la aplicación (los datos quedan aparte, en $DATOS).
mkdir -p "$APP" "$BIN" "$DATOS/respaldos"
cp "$ORIGEN"/store.py "$ORIGEN"/server.py "$ORIGEN"/mcp_server.py "$APP"/
rm -rf "$APP/static" && cp -r "$ORIGEN/static" "$APP/static"
[[ -f "$ORIGEN/README.md" ]] && cp "$ORIGEN/README.md" "$APP/"
say "Aplicación copiada en $APP"

# 3. Lanzador: arranca el servidor si hace falta y abre la ventana.
cat > "$BIN/libreta" <<EOF
#!/usr/bin/env bash
URL="http://127.0.0.1:$PUERTO/"
if ! "$PY" -c "import urllib.request,sys; urllib.request.urlopen('\${URL}api/tree', timeout=1)" 2>/dev/null; then
  if systemctl --user is-enabled libreta.service >/dev/null 2>&1; then
    systemctl --user start libreta.service
  else
    nohup "$PY" "$APP/server.py" --puerto $PUERTO >"$DATOS/servidor.log" 2>&1 &
  fi
  for _ in \$(seq 1 40); do
    "$PY" -c "import urllib.request; urllib.request.urlopen('\${URL}api/tree', timeout=1)" 2>/dev/null && break
    sleep 0.15
  done
fi
# Ventana propia (modo app) si hay un navegador basado en Chromium; si no, el navegador por defecto.
for b in chromium chromium-browser google-chrome google-chrome-stable brave-browser microsoft-edge vivaldi; do
  if command -v "\$b" >/dev/null 2>&1; then exec "\$b" --app="\$URL" --class=Libreta >/dev/null 2>&1; fi
done
exec xdg-open "\$URL" >/dev/null 2>&1
EOF
chmod +x "$BIN/libreta"
say "Lanzador creado: $BIN/libreta"

# 4. Icono y entrada en el menú de Mint.
ICONOS="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"
mkdir -p "$ICONOS" "${XDG_DATA_HOME:-$HOME/.local/share}/applications"
cp "$APP/static/icon.svg" "$ICONOS/libreta.svg"
cat > "${XDG_DATA_HOME:-$HOME/.local/share}/applications/libreta.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Libreta
GenericName=Cuadernos de notas
Comment=Cuadernos, secciones y páginas al estilo OneNote, con respaldo y conexión a Antigravity
Exec=$BIN/libreta
Icon=libreta
Terminal=false
Categories=Office;Utility;
Keywords=notas;onenote;cuaderno;apuntes;
StartupWMClass=Libreta
EOF
command -v update-desktop-database >/dev/null && update-desktop-database "${XDG_DATA_HOME:-$HOME/.local/share}/applications" >/dev/null 2>&1 || true
gtk-update-icon-cache -q "${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor" >/dev/null 2>&1 || true
say "Libreta aparece en el menú (Oficina)"

# 5. Servidor en segundo plano al iniciar sesión (servicio de usuario de systemd).
if [[ $CON_SERVICIO == 1 ]] && command -v systemctl >/dev/null; then
  mkdir -p "$HOME/.config/systemd/user"
  cat > "$HOME/.config/systemd/user/libreta.service" <<EOF
[Unit]
Description=Libreta (servidor local de notas)

[Service]
ExecStart=$PY $APP/server.py --puerto $PUERTO
Restart=on-failure

[Install]
WantedBy=default.target
EOF
  systemctl --user daemon-reload
  # restart (no solo start) para que una actualización cargue el código nuevo
  { systemctl --user enable libreta.service && systemctl --user restart libreta.service; } >/dev/null 2>&1 \
    && say "Servidor activo y con arranque automático" \
    || say "No se pudo activar el servicio; el lanzador arrancará el servidor cuando abras Libreta"
fi

# 6. Antigravity (opcional): registrar el servidor MCP.
CFG="$HOME/.gemini/antigravity/mcp_config.json"
# Las preguntas se leen del teclado aunque el script llegue por una tubería (curl | bash).
TTY=/dev/stdin
[[ -t 0 ]] || { [[ -r /dev/tty ]] && TTY=/dev/tty; }
echo
r=""; read -r -p "¿Conectar Libreta con Antigravity ahora? [s/N] " r <"$TTY" || true
if [[ "${r,,}" == s* ]]; then
  w=""; read -r -p "¿Permitir que Antigravity también cree y edite notas? (si no, solo lectura) [s/N] " w <"$TTY" || true
  ESCRITURA=0; [[ "${w,,}" == s* ]] && ESCRITURA=1
  mkdir -p "$(dirname "$CFG")"
  [[ -f "$CFG" ]] && cp "$CFG" "$CFG.antes-de-libreta.$(date +%Y%m%d%H%M%S)"
  "$PY" - "$CFG" "$PY" "$APP/mcp_server.py" "$ESCRITURA" <<'EOF'
import json, os, sys
cfg_path, py, script, write = sys.argv[1:5]
try:
    with open(cfg_path, encoding="utf-8") as f:
        cfg = json.load(f)
except (OSError, ValueError):
    cfg = {}
entry = {"command": py, "args": [script]}
if write == "1":
    entry["env"] = {"LIBRETA_MCP_ESCRITURA": "1"}
cfg.setdefault("mcpServers", {})["libreta"] = entry
tmp = cfg_path + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)
os.replace(tmp, cfg_path)
EOF
  say "Registrado en $CFG (se guardó copia del archivo anterior). Reinicia Antigravity."
else
  say "Puedes conectarlo más tarde desde el botón «⚡ Antigravity» de la aplicación."
fi

echo
say "Listo. Abre Libreta desde el menú o ejecuta: libreta"
say "Tus notas y respaldos están en: $DATOS"
case ":$PATH:" in *":$BIN:"*) ;; *) say "Nota: cierra y abre la sesión para que el comando «libreta» esté en el PATH." ;; esac
