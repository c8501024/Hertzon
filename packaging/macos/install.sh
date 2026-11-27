#!/usr/bin/env bash
# Hertzon for macOS on Apple Silicon: installs for the current user (no administrator rights) into
# ~/Library/Application Support/Hertzon, creates Hertzon.app in /Applications (Finder's Applications folder,
# Launchpad, Spotlight, Dock; for an administrator no password is needed, otherwise it asks for one or falls
# back to ~/Applications),
# the command "hertzon" in ~/.local/bin and opens .hzn projects with Hertzon. The app is made here on this
# Mac, so Gatekeeper does not block it although it carries no Apple developer signature.
# Run it again from a newer package to update; the Python environment is kept.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="${HERTZON_HOME:-$HOME/Library/Application Support/Hertzon}"
# Hertzon.app goes to /Applications; ~/Applications (where 0.4.1 put it) only if that is not possible.
# HERTZON_APPS forces a folder; HERTZON_SYSTEM_APPS replaces /Applications (used by the CI test).
SYSAPPS="${HERTZON_SYSTEM_APPS:-/Applications}"
USERAPPS="$HOME/Applications"
BIN="$HOME/.local/bin"
VER="$(sed -n 's/^version = "\(.*\)"/\1/p' "$SRC/pyproject.toml")"

if [ "$(uname -s)" != "Darwin" ]; then
    echo "This package is for macOS. For Linux use Hertzon-<version>-linux.tar.gz." >&2
    exit 1
fi
if [ "$(uname -m)" != "arm64" ] && [ "$(sysctl -n hw.optional.arm64 2>/dev/null || echo 0)" != "1" ]; then
    echo "This package is for Macs with Apple Silicon (M1 or newer). On an Intel Mac, install Hertzon" >&2
    echo "manually as described in the README (pip builds the solver from source, which needs Rust)." >&2
    exit 1
fi

echo "Installing Hertzon $VER to $DEST"

# ---- Python 3.10 to 3.13 (EMerge 3 supports these), as an arm64 build ----------------------------------
PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3 \
         /Library/Frameworks/Python.framework/Versions/3.1[0-3]/bin/python3 \
         /opt/homebrew/bin/python3.1[0-3] /opt/homebrew/opt/python@3.1[0-3]/bin/python3.1[0-3]; do
    p="$(command -v "$c" 2>/dev/null)" || continue
    if "$p" -c 'import sys, venv, ensurepip, platform
sys.exit(not ((3, 10) <= sys.version_info[:2] <= (3, 13) and platform.machine() == "arm64"))' 2>/dev/null; then
        PY="$p"; break
    fi
done
if [ -z "$PY" ]; then
    echo "Python 3.10 to 3.13 for Apple Silicon is missing (newer versions are not supported by the solver yet)." >&2
    echo "Install Python 3.13, for example:" >&2
    echo "  from https://www.python.org/downloads/macos/ (macOS 64-bit universal2 installer), or" >&2
    echo "  with Homebrew: brew install python@3.13" >&2
    echo "Then run this script again." >&2
    exit 1
fi
echo "Python: $PY ($("$PY" --version 2>&1))"

# ---- program files (an update replaces them; .venv stays) -------------------------------------------
mkdir -p "$DEST"
rm -rf "$DEST/hertzon" "$DEST/examples" "$DEST/docs" "$DEST/claude-plugin" "$DEST/.claude-plugin"
cp -R "$SRC/hertzon" "$SRC/examples" "$SRC/docs" "$SRC/pyproject.toml" "$SRC/README.md" "$SRC/LICENSE" \
      "$SRC/uninstall.sh" "$DEST/"
for d in claude-plugin .claude-plugin; do
    if [ -d "$SRC/$d" ]; then cp -R "$SRC/$d" "$DEST/"; fi
done
# a package downloaded with a browser carries the quarantine flag, and macOS refuses to load the mesher
# library (libhzmesh.dylib) from a quarantined file; the files are Hertzon's own, so the flag goes
xattr -dr com.apple.quarantine "$DEST" 2>/dev/null || true

# ---- Python environment -----------------------------------------------------------------------------
if [ -x "$DEST/.venv/bin/python" ] && ! "$DEST/.venv/bin/python" -c 'import sys' 2>/dev/null; then
    echo "The Python environment no longer starts (its Python was removed or updated); making a new one."
    rm -rf "$DEST/.venv"
fi
if [ ! -x "$DEST/.venv/bin/python" ]; then
    "$PY" -m venv "$DEST/.venv"
fi
echo "Installing packages (EMerge, Qt, VTK; about 1 GB the first time) ..."
"$DEST/.venv/bin/python" -m pip install --upgrade pip -q
"$DEST/.venv/bin/python" -m pip install -e "$DEST" -q
echo "Preparing the solver (once, a few minutes) ..."
(cd "$DEST" && "$DEST/.venv/bin/python" -m hertzon.solver.warmup) ||
    echo "The solver kernels will be compiled at the first simulation instead."

# ---- SPARTA (optional) ------------------------------------------------------------------------------
# SPARTA, the GPU/CPU sparse direct solver of EMerge's author, is AGPL-3.0 and so not part of Hertzon
# (GPL-2.0-or-later): it is installed only when the user says so. Asked once per Python environment (the
# answer is kept in .venv/hertzon_sparta_asked); later updates update it when it is installed and leave it
# out otherwise (Tools > Options > Install SPARTA adds it any time). HERTZON_SPARTA=1 or 0 answers without
# asking (scripts, CI); without a terminal it is left out and asked at the next run in a terminal.
# On a Mac it is the fastest matrix solver Hertzon has (measured on Apple silicon: two to three times faster
# than Apple Accelerate, on the CPU in double precision), and "Auto" uses it when installed, so "yes" is
# preselected here.
SPARTA="${HERTZON_SPARTA:-}"
if [ -z "$SPARTA" ]; then
    if "$DEST/.venv/bin/python" -m hertzon.solver.sparta_setup version >/dev/null 2>&1; then
        SPARTA=update
    elif [ ! -e "$DEST/.venv/hertzon_sparta_asked" ] && [ -t 0 ]; then
        echo
        echo "SPARTA is an optional sparse direct solver that factorizes on the GPU (Metal) or the CPU."
        echo "On a Mac it solves about two to three times faster than Apple Accelerate; recommended."
        echo "It is a separate program under the AGPL-3.0 licence: https://github.com/FennisRobert/SPARTA"
        printf "Install SPARTA? [Y/n] "
        read -r answer || answer=n
        case "$answer" in ""|[Yy]*) SPARTA=1;; *) SPARTA=0;; esac
        echo "$SPARTA" > "$DEST/.venv/hertzon_sparta_asked"
    else
        SPARTA=0
    fi
fi
case "$SPARTA" in
    1|[Yy]*) SPARTA_CMD=install ;;
    update) SPARTA_CMD=update ;;
    *) SPARTA_CMD="" ;;
esac
if [ -n "$SPARTA_CMD" ]; then
    "$DEST/.venv/bin/python" -m hertzon.solver.sparta_setup "$SPARTA_CMD" ||
        echo "SPARTA could not be installed; install it later in Hertzon: Tools > Options > Install SPARTA."
fi

# ---- command ----------------------------------------------------------------------------------------
mkdir -p "$BIN"
cat > "$BIN/hertzon" <<EOS
#!/bin/sh
cd "$DEST" || exit 1
exec "$DEST/.venv/bin/python" -m hertzon "\$@"
EOS
chmod +x "$BIN/hertzon"

# ---- Hertzon.app ------------------------------------------------------------------------------------
# made in a temporary folder first and then copied to its place (with sudo if that folder needs it)
STAGE="$(mktemp -d)"
APP="$STAGE/Hertzon.app"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cat > "$APP/Contents/MacOS/Hertzon" <<EOS
#!/bin/sh
# Started from Finder, Launchpad or the Dock there is no terminal: the output goes to hertzon.log.
# A .hzn opened in Finder reaches Hertzon as an open-file event, not as an argument.
cd "$DEST" || exit 1
if [ -t 2 ]; then exec "$DEST/.venv/bin/python" -m hertzon "\$@"; fi
exec "$DEST/.venv/bin/python" -m hertzon "\$@" >"$DEST/hertzon.log" 2>&1
EOS
chmod +x "$APP/Contents/MacOS/Hertzon"

ICON=""
ICONSET="$(mktemp -d)/hertzon.iconset"
mkdir -p "$ICONSET"
if (for s in 16 32 64 128 256; do
        sips -z $s $s "$DEST/hertzon/gui/hertzon.png" --out "$ICONSET/icon_${s}x${s}.png" >/dev/null
    done
    cp "$ICONSET/icon_32x32.png" "$ICONSET/icon_16x16@2x.png"
    cp "$ICONSET/icon_64x64.png" "$ICONSET/icon_32x32@2x.png"
    cp "$ICONSET/icon_256x256.png" "$ICONSET/icon_128x128@2x.png"
    rm "$ICONSET/icon_64x64.png"
    iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/hertzon.icns") 2>/dev/null; then
    ICON="hertzon"
else
    echo "The app icon could not be made (sips/iconutil); Hertzon.app gets the default icon."
fi
rm -rf "$(dirname "$ICONSET")"

cat > "$APP/Contents/Info.plist" <<EOS
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Hertzon</string>
  <key>CFBundleDisplayName</key><string>Hertzon</string>
  <key>CFBundleIdentifier</key><string>org.hertzon.Hertzon</string>
  <key>CFBundleExecutable</key><string>Hertzon</string>
  <key>CFBundleIconFile</key><string>$ICON</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$VER</string>
  <key>CFBundleVersion</key><string>$VER</string>
  <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>LSApplicationCategoryType</key><string>public.app-category.developer-tools</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>CFBundleDocumentTypes</key>
  <array>
    <dict>
      <key>CFBundleTypeName</key><string>Hertzon project</string>
      <key>CFBundleTypeRole</key><string>Editor</string>
      <key>CFBundleTypeIconFile</key><string>$ICON</string>
      <key>LSHandlerRank</key><string>Owner</string>
      <key>LSItemContentTypes</key><array><string>org.hertzon.project</string></array>
    </dict>
  </array>
  <key>UTExportedTypeDeclarations</key>
  <array>
    <dict>
      <key>UTTypeIdentifier</key><string>org.hertzon.project</string>
      <key>UTTypeDescription</key><string>Hertzon project</string>
      <key>UTTypeIconFile</key><string>$ICON</string>
      <key>UTTypeConformsTo</key><array><string>public.data</string></array>
      <key>UTTypeTagSpecification</key>
      <dict><key>public.filename-extension</key><array><string>hzn</string></array></dict>
    </dict>
  </array>
</dict>
</plist>
EOS

LSREG=/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister
SUDO=""
KEEP_APP=""
if [ -n "${HERTZON_APPS:-}" ]; then
    APPS="$HERTZON_APPS"
    mkdir -p "$APPS"
elif [ -d "$SYSAPPS" ] && [ -w "$SYSAPPS" ] && { [ ! -e "$SYSAPPS/Hertzon.app" ] || [ -w "$SYSAPPS/Hertzon.app" ]; }; then
    APPS="$SYSAPPS"                 # an administrator account can write here without a password
elif [ ! -t 0 ] && [ -d "$SYSAPPS/Hertzon.app" ] &&
     [ "$(defaults read "$SYSAPPS/Hertzon.app/Contents/Info" CFBundleIdentifier 2>/dev/null)" = "org.hertzon.Hertzon" ]; then
    # an update from Hertzon (no terminal, no password prompt): the app in /Applications only starts the
    # program in $DEST, which was updated above, so it is kept instead of making a second one in ~/Applications
    APPS="$SYSAPPS"
    KEEP_APP=1
else
    APPS="$USERAPPS"
    if [ -d "$SYSAPPS" ] && [ -t 0 ]; then
        echo
        echo "Your account cannot write to $SYSAPPS without an administrator password."
        printf "Put Hertzon.app into %s with administrator rights (sudo)? [Y/n] " "$SYSAPPS"
        read -r answer || answer=n
        case "$answer" in
            ""|[Yy]*) if sudo -v; then APPS="$SYSAPPS"; SUDO="sudo"; fi ;;
        esac
    fi
    if [ "$APPS" = "$USERAPPS" ]; then
        echo "Hertzon.app goes to $USERAPPS instead (Launchpad and Spotlight find it there, Finder lists it"
        echo "under your home folder > Applications). Run install.sh again as an administrator to move it."
        mkdir -p "$USERAPPS"
    fi
fi
if [ -n "${KEEP_APP:-}" ]; then
    echo "Hertzon.app in $APPS is kept (it cannot be replaced without an administrator password; it starts"
    echo "the updated program all the same)."
else
    $SUDO rm -rf "$APPS/Hertzon.app"
    $SUDO ditto "$APP" "$APPS/Hertzon.app"
fi
rm -rf "$STAGE"
APP="$APPS/Hertzon.app"
# tell Launch Services about the app (Spotlight, Launchpad, .hzn files); it would also notice on its own
if [ -x "$LSREG" ]; then "$LSREG" -f "$APP" >/dev/null 2>&1 || true; fi
if [ -z "${KEEP_APP:-}" ]; then $SUDO touch "$APP"; fi

# an app of an earlier installation in the other folder would show up twice: remove it
for old in "$USERAPPS/Hertzon.app" "$SYSAPPS/Hertzon.app"; do
    if [ "$old" = "$APP" ] || [ ! -e "$old" ]; then continue; fi
    # only Hertzon's own app (made by this script) is removed
    if [ "$(defaults read "$old/Contents/Info" CFBundleIdentifier 2>/dev/null)" != "org.hertzon.Hertzon" ]; then continue; fi
    if [ -x "$LSREG" ]; then "$LSREG" -u "$old" >/dev/null 2>&1 || true; fi
    if rm -rf "$old" 2>/dev/null; then
        echo "Removed the app of the earlier installation: $old"
    else
        echo "The app of an earlier installation could not be removed: $old (delete it in Finder)."
    fi
done

echo
echo "Hertzon is installed: open it from the Applications folder, Launchpad or Spotlight ($APP),"
echo "or with the command: hertzon"
case ":$PATH:" in *":$BIN:"*) ;; *) echo "($BIN is not in PATH; start it with $BIN/hertzon)";; esac
echo "Uninstall: \"$DEST/uninstall.sh\""
