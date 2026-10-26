#!/usr/bin/env bash
# Instalación (o actualización) de Libreta en un solo comando:
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/pedromogollon3-coder/libreta/main/install.sh)"
# Descarga la última versión del repositorio y ejecuta instalar.sh. Tus notas no se tocan.
set -euo pipefail

REPO="pedromogollon3-coder/libreta"
RAMA="${LIBRETA_RAMA:-main}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "» Descargando Libreta ($REPO, rama $RAMA)…"
if command -v curl >/dev/null; then
  curl -fsSL "https://github.com/$REPO/archive/refs/heads/$RAMA.tar.gz" -o "$TMP/libreta.tar.gz"
elif command -v wget >/dev/null; then
  wget -qO "$TMP/libreta.tar.gz" "https://github.com/$REPO/archive/refs/heads/$RAMA.tar.gz"
else
  echo "✗ Necesitas curl o wget: sudo apt install curl" >&2
  exit 1
fi
tar xzf "$TMP/libreta.tar.gz" -C "$TMP"
bash "$TMP"/libreta-*/instalar.sh "$@"
