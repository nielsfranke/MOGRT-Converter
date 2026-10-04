#!/bin/sh
# Installs MOGRT Converter for the current user (~/.local).
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/.local/opt/mogrt-converter"
mkdir -p "$DEST" "$HOME/.local/bin" "$HOME/.local/share/applications" "$HOME/.local/share/icons/hicolor/256x256/apps"
cp -R "$HERE"/. "$DEST"/
ln -sf "$DEST/mogrt-converter" "$HOME/.local/bin/mogrt-converter"
cp "$HERE/mogrt-converter.png" "$HOME/.local/share/icons/hicolor/256x256/apps/mogrt-converter.png"
sed "s|^Exec=.*|Exec=$DEST/mogrt-converter %f|" "$HERE/mogrt-converter.desktop" > "$HOME/.local/share/applications/mogrt-converter.desktop"
command -v update-desktop-database >/dev/null && update-desktop-database "$HOME/.local/share/applications" || true
echo "Installiert. Start über das Anwendungsmenü oder: mogrt-converter"
