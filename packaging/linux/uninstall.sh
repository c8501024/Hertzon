#!/usr/bin/env bash
# Removes Hertzon installed by install.sh. Projects saved elsewhere are not touched.
set -eu
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
rm -rf "$DATA/hertzon"
rm -f "$HOME/.local/bin/hertzon" "$DATA/applications/hertzon.desktop" \
      "$DATA/icons/hicolor/256x256/apps/hertzon.png" "$DATA/mime/packages/hertzon.xml"
command -v update-mime-database >/dev/null && update-mime-database "$DATA/mime" >/dev/null 2>&1 || true
command -v update-desktop-database >/dev/null && update-desktop-database "$DATA/applications" >/dev/null 2>&1 || true
echo "Hertzon was removed."
