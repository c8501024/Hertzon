#!/usr/bin/env bash
# Hertzon for Linux: installs for the current user (no root needed) into ~/.local/share/hertzon, adds the
# command "hertzon", an entry in the application menu with the Hertzon icon, and opens .hzn projects with
# Hertzon. Run it again from a newer package to update; the Python environment is kept.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
DEST="$DATA/hertzon"
BIN="$HOME/.local/bin"

echo "Hertzon wird installiert nach $DEST"

# ---- Python 3.10 or newer with venv ---------------------------------------------------------------
PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$c" >/dev/null 2>&1 &&
       "$c" -c 'import sys, venv, ensurepip; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
        PY="$(command -v "$c")"; break
    fi
done
if [ -z "$PY" ]; then
    echo "Python 3.10 oder neuer mit venv fehlt. Zum Beispiel unter Ubuntu/Debian:" >&2
    echo "  sudo apt install python3 python3-venv" >&2
    exit 1
fi
echo "Python: $PY ($("$PY" --version 2>&1))"

# ---- system libraries for gmsh (meshing) and Qt (window) --------------------------------------------
missing=()
for lib in libGLU.so.1 libXcursor.so.1 libXinerama.so.1 libXft.so.2 libxkbcommon-x11.so.0 libxcb-cursor.so.0 libEGL.so.1; do
    ldconfig -p 2>/dev/null | grep -q "$lib" || missing+=("$lib")
done
if [ ${#missing[@]} -gt 0 ]; then
    echo "Hinweis: diese Systembibliotheken fehlen vermutlich: ${missing[*]}"
    echo "Unter Ubuntu/Debian installierst du sie mit:"
    echo "  sudo apt install libglu1-mesa libxcursor1 libxinerama1 libxft2 libxkbcommon-x11-0 libxcb-cursor0 libegl1"
fi

# ---- program files (an update replaces them; .venv stays) -------------------------------------------
mkdir -p "$DEST"
rm -rf "$DEST/hertzon" "$DEST/examples" "$DEST/docs"
cp -r "$SRC/hertzon" "$SRC/examples" "$SRC/docs" "$SRC/pyproject.toml" "$SRC/README.md" "$SRC/LICENSE" "$SRC/uninstall.sh" "$DEST/"

# ---- Python environment -----------------------------------------------------------------------------
if [ ! -x "$DEST/.venv/bin/python" ]; then
    "$PY" -m venv "$DEST/.venv"
fi
echo "Pakete werden installiert (EMerge, Qt, VTK; beim ersten Mal ca. 1 GB) ..."
"$DEST/.venv/bin/python" -m pip install --upgrade pip -q
"$DEST/.venv/bin/python" -m pip install -e "$DEST" -q
echo "Solver wird vorbereitet (einmalig, einige Minuten) ..."
(cd "$DEST" && "$DEST/.venv/bin/python" -m hertzon.solver.warmup) ||
    echo "Die Solver-Kernel werden stattdessen bei der ersten Simulation kompiliert."

# ---- command, menu entry, icon, .hzn files ----------------------------------------------------------
mkdir -p "$BIN"
cat > "$BIN/hertzon" <<EOS
#!/bin/sh
cd "$DEST" && exec "$DEST/.venv/bin/python" -m hertzon "\$@"
EOS
chmod +x "$BIN/hertzon"

mkdir -p "$DATA/icons/hicolor/256x256/apps" "$DATA/applications" "$DATA/mime/packages"
cp "$DEST/hertzon/gui/hertzon.png" "$DATA/icons/hicolor/256x256/apps/hertzon.png"
cat > "$DATA/applications/hertzon.desktop" <<EOS
[Desktop Entry]
Type=Application
Name=Hertzon
GenericName=3D Full-Wave FEM Solver
Comment=Electromagnetic Design Suite: 3D full-wave FEM, circuits, PCBs
Exec=$BIN/hertzon %f
Icon=hertzon
Terminal=false
Categories=Science;Engineering;Physics;
MimeType=application/x-hertzon-project;
StartupWMClass=Hertzon
EOS
cat > "$DATA/mime/packages/hertzon.xml" <<'EOS'
<?xml version="1.0" encoding="UTF-8"?>
<mime-info xmlns="http://www.freedesktop.org/standards/shared-mime-info">
  <mime-type type="application/x-hertzon-project">
    <comment>Hertzon project</comment>
    <glob pattern="*.hzn"/>
    <icon name="hertzon"/>
  </mime-type>
</mime-info>
EOS
command -v update-mime-database >/dev/null && update-mime-database "$DATA/mime" >/dev/null 2>&1 || true
command -v update-desktop-database >/dev/null && update-desktop-database "$DATA/applications" >/dev/null 2>&1 || true
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q -t "$DATA/icons/hicolor" >/dev/null 2>&1 || true
command -v xdg-mime >/dev/null && xdg-mime default hertzon.desktop application/x-hertzon-project >/dev/null 2>&1 || true

echo
echo "Hertzon ist installiert. Du findest es im Anwendungsmenü oder startest es mit: hertzon"
case ":$PATH:" in *":$BIN:"*) ;; *) echo "($BIN ist nicht im PATH; dann mit $BIN/hertzon starten.)";; esac
echo "Deinstallieren: $DEST/uninstall.sh"
