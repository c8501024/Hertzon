#!/usr/bin/env bash
# Hertzon for macOS on Apple Silicon: installs for the current user (no administrator rights) into
# ~/Library/Application Support/Hertzon, creates Hertzon.app in ~/Applications (Launchpad, Spotlight, Dock),
# the command "hertzon" in ~/.local/bin and opens .hzn projects with Hertzon. The app is made here on this
# Mac, so Gatekeeper does not block it although it carries no Apple developer signature.
# Run it again from a newer package to update; the Python environment is kept.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="${HERTZON_HOME:-$HOME/Library/Application Support/Hertzon}"
APPS="${HERTZON_APPS:-$HOME/Applications}"
APP="$APPS/Hertzon.app"
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

# ---- command ----------------------------------------------------------------------------------------
mkdir -p "$BIN"
cat > "$BIN/hertzon" <<EOS
#!/bin/sh
cd "$DEST" || exit 1
exec "$DEST/.venv/bin/python" -m hertzon "\$@"
EOS
chmod +x "$BIN/hertzon"

# ---- Hertzon.app ------------------------------------------------------------------------------------
mkdir -p "$APPS"
rm -rf "$APP"
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
# tell Launch Services about the app (Spotlight, Launchpad, .hzn files); it would also notice on its own
LSREG=/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister
if [ -x "$LSREG" ]; then "$LSREG" -f "$APP" >/dev/null 2>&1 || true; fi
touch "$APP"

echo
echo "Hertzon is installed: open it from Launchpad, Spotlight or $APP, or with the command: hertzon"
case ":$PATH:" in *":$BIN:"*) ;; *) echo "($BIN is not in PATH; start it with $BIN/hertzon)";; esac
echo "Uninstall: \"$DEST/uninstall.sh\""
