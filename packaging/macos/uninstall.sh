#!/usr/bin/env bash
# Removes Hertzon installed by install.sh. Projects saved elsewhere are not touched, nor are the settings
# (~/Library/Preferences/com.hertzon.Hertzon.plist) and the Hertzon folders in your home.
set -eu
DEST="${HERTZON_HOME:-$HOME/Library/Application Support/Hertzon}"
LSREG=/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister
# Hertzon.app is in /Applications or, for older installations or without administrator rights, in ~/Applications
for APP in ${HERTZON_APPS:+"$HERTZON_APPS/Hertzon.app"} "${HERTZON_SYSTEM_APPS:-/Applications}/Hertzon.app" \
           "$HOME/Applications/Hertzon.app"; do
    [ -e "$APP" ] || continue
    if [ "$(defaults read "$APP/Contents/Info" CFBundleIdentifier 2>/dev/null)" != "org.hertzon.Hertzon" ]; then
        continue
    fi
    if [ -x "$LSREG" ]; then "$LSREG" -u "$APP" >/dev/null 2>&1 || true; fi
    if ! rm -rf "$APP" 2>/dev/null; then
        if [ -t 0 ]; then
            echo "Removing $APP needs administrator rights."
            sudo rm -rf "$APP" || echo "$APP could not be removed; delete it in Finder."
        else
            echo "$APP could not be removed (administrator rights needed); delete it in Finder."
        fi
    fi
done
rm -rf "$DEST"
rm -f "$HOME/.local/bin/hertzon"
echo "Hertzon was removed."
