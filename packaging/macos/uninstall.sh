#!/usr/bin/env bash
# Removes Hertzon installed by install.sh. Projects saved elsewhere are not touched, nor are the settings
# (~/Library/Preferences/com.hertzon.Hertzon.plist) and the Hertzon folders in your home.
set -eu
DEST="${HERTZON_HOME:-$HOME/Library/Application Support/Hertzon}"
APP="${HERTZON_APPS:-$HOME/Applications}/Hertzon.app"
LSREG=/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister
if [ -x "$LSREG" ] && [ -d "$APP" ]; then "$LSREG" -u "$APP" >/dev/null 2>&1 || true; fi
rm -rf "$APP" "$DEST"
rm -f "$HOME/.local/bin/hertzon"
echo "Hertzon was removed."
