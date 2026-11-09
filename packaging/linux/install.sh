#!/usr/bin/env bash
# Hertzon for Linux: installs for the current user (no root needed) into ~/.local/share/hertzon, adds the
# command "hertzon", an entry in the application menu with the Hertzon icon, and opens .hzn projects with
# Hertzon. Run it again from a newer package to update; the Python environment is kept.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
DEST="$DATA/hertzon"
BIN="$HOME/.local/bin"

echo "Installing Hertzon to $DEST"

# ---- Python 3.10 or newer with venv ---------------------------------------------------------------
PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$c" >/dev/null 2>&1 &&
       "$c" -c 'import sys, venv, ensurepip; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
        PY="$(command -v "$c")"; break
    fi
done
if [ -z "$PY" ]; then
    echo "Python 3.10 or newer with venv is missing. For example on Ubuntu/Debian:" >&2
    echo "  sudo apt install python3 python3-venv" >&2
    exit 1
fi
echo "Python: $PY ($("$PY" --version 2>&1))"

# ---- program files (an update replaces them; .venv stays) -------------------------------------------
mkdir -p "$DEST"
rm -rf "$DEST/hertzon" "$DEST/examples" "$DEST/docs"
cp -r "$SRC/hertzon" "$SRC/examples" "$SRC/docs" "$SRC/pyproject.toml" "$SRC/README.md" "$SRC/LICENSE" "$SRC/uninstall.sh" "$SRC/syscheck.sh" "$DEST/"

# ---- Python environment -----------------------------------------------------------------------------
if [ ! -x "$DEST/.venv/bin/python" ]; then
    "$PY" -m venv "$DEST/.venv"
fi
echo "Installing packages (EMerge, Qt, VTK; about 1 GB the first time) ..."
"$DEST/.venv/bin/python" -m pip install --upgrade pip -q
"$DEST/.venv/bin/python" -m pip install -e "$DEST" -q
echo "Preparing the solver (once, a few minutes) ..."
(cd "$DEST" && "$DEST/.venv/bin/python" -m hertzon.solver.warmup) ||
    echo "The solver kernels will be compiled at the first simulation instead."

# ---- system libraries for the window (Qt) and the mesher (gmsh) -------------------------------------
# without them Qt stops with "no Qt platform plugin could be initialized" and nothing opens
chmod +x "$DEST/syscheck.sh"
if ! "$DEST/syscheck.sh"; then
    cmd="$("$DEST/syscheck.sh" --command)"
    if [ -n "$cmd" ] && [ -t 0 ]; then
        printf "Install them now (asks for the sudo password)? [Y/n] "
        read -r a || a=n
        case "$a" in [nN]*) ;; *) $cmd || true;; esac
        "$DEST/syscheck.sh" >/dev/null || echo "System libraries are still missing; Hertzon starts only once they are installed: $DEST/syscheck.sh"
    fi
fi

# ---- command, menu entry, icon, .hzn files ----------------------------------------------------------
mkdir -p "$BIN"
cat > "$BIN/hertzon" <<EOS
#!/bin/sh
# Started from the application menu there is no terminal: missing system libraries are shown in a message
# window instead of Qt stopping silently, and the output goes to hertzon.log.
cd "$DEST" || exit 1
if [ ! -t 2 ]; then
    cmd="\$("$DEST/syscheck.sh" --command 2>/dev/null)" || {
        msg="Hertzon cannot start: system libraries for its window (Qt) are missing.
Please install them in a terminal:

\$cmd

Then start Hertzon again."
        if command -v zenity >/dev/null 2>&1; then zenity --error --no-wrap --title=Hertzon --text="\$msg"
        elif command -v kdialog >/dev/null 2>&1; then kdialog --title Hertzon --error "\$msg"
        elif command -v notify-send >/dev/null 2>&1; then notify-send -u critical Hertzon "\$msg"
        elif command -v xmessage >/dev/null 2>&1; then xmessage -center "\$msg"
        fi
        exit 1
    }
    exec "$DEST/.venv/bin/python" -m hertzon "\$@" >"$DEST/hertzon.log" 2>&1
fi
exec "$DEST/.venv/bin/python" -m hertzon "\$@"
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
echo "Hertzon is installed. Find it in the application menu or start it with: hertzon"
case ":$PATH:" in *":$BIN:"*) ;; *) echo "($BIN is not in PATH; start it with $BIN/hertzon)";; esac
echo "Uninstall: $DEST/uninstall.sh"
