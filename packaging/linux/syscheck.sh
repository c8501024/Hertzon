#!/usr/bin/env bash
# Checks the system libraries the Hertzon window (Qt, platform plugin "xcb") and the mesher (gmsh) need, and
# prints the command that installs the missing ones for this distribution.
#   syscheck.sh            report; exit 0 = all there, 1 = something is missing
#   syscheck.sh --command  print only the install command (empty when nothing is missing)
# install.sh runs it after the Python packages, the hertzon launcher before each start from the menu: without
# these libraries Qt stops with "no Qt platform plugin could be initialized" before any window appears.
set -u
DEST="$(cd "$(dirname "$0")" && pwd)"
PY="$DEST/.venv/bin/python"

# ---- which libraries are missing -------------------------------------------------------------------
missing=()
add() { case " ${missing[*]-} " in *" $1 "*) ;; *) missing+=("$1");; esac; }

# libraries the Qt platform plugin and gmsh are linked against, as the system loader resolves them
qt="$("$PY" -c 'import os, PySide6; print(os.path.join(os.path.dirname(PySide6.__file__), "Qt"))' 2>/dev/null)"
files=()
[ -n "$qt" ] && files+=("$qt/plugins/platforms/libqxcb.so" "$qt"/lib/libQt6XcbQpa.so.* "$qt"/lib/libQt6Gui.so.*)
site="$("$PY" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])' 2>/dev/null)"
[ -n "$site" ] && for f in "$site"/gmsh*/lib/libgmsh.so* "$site"/../../libgmsh.so* "$DEST/.venv/lib/libgmsh.so"*; do
    [ -e "$f" ] && files+=("$f")
done
for f in "${files[@]}"; do
    [ -e "$f" ] || continue
    for l in $(ldd "$f" 2>/dev/null | awk '/not found/ {print $1}'); do add "$l"; done
done

# loaded at run time (not visible to ldd): Qt 6.5+ needs libxcb-cursor for its xcb plugin; EGL and GLU for
# the 3D view and gmsh
have() {
    { ldconfig -p 2>/dev/null || /sbin/ldconfig -p 2>/dev/null || /usr/sbin/ldconfig -p 2>/dev/null; } |
        grep -q " $1 " && return 0
    for d in /usr/lib64 /usr/lib /lib64 /lib /usr/lib/x86_64-linux-gnu /lib/x86_64-linux-gnu \
             /usr/lib/aarch64-linux-gnu /usr/local/lib; do
        [ -e "$d/$1" ] && return 0
    done
    return 1
}
for l in libxcb-cursor.so.0 libxkbcommon-x11.so.0 libEGL.so.1 libGL.so.1 libGLU.so.1 libXcursor.so.1 \
         libXinerama.so.1 libXft.so.2 libfontconfig.so.1; do
    have "$l" || add "$l"
done

# ---- package names --------------------------------------------------------------------------------
pm=""
for c in apt-get dnf zypper pacman; do command -v "$c" >/dev/null 2>&1 && { pm=$c; break; }; done

pkg() {   # pkg SONAME -> package for $pm
    local s=$1 apt dnf pac zyp
    case "$s" in
        libxcb-cursor.so.0)      apt=libxcb-cursor0;       dnf=xcb-util-cursor;     pac=xcb-util-cursor;     zyp=libxcb-cursor0;;
        libxcb-icccm.so.4)       apt=libxcb-icccm4;        dnf=xcb-util-wm;         pac=xcb-util-wm;         zyp=libxcb-icccm4;;
        libxcb-keysyms.so.1)     apt=libxcb-keysyms1;      dnf=xcb-util-keysyms;    pac=xcb-util-keysyms;    zyp=libxcb-keysyms1;;
        libxcb-render-util.so.0) apt=libxcb-render-util0;  dnf=xcb-util-renderutil; pac=xcb-util-renderutil; zyp=libxcb-render-util0;;
        libxcb-image.so.0)       apt=libxcb-image0;        dnf=xcb-util-image;      pac=xcb-util-image;      zyp=libxcb-image0;;
        libxcb-util.so.1)        apt=libxcb-util1;         dnf=xcb-util;            pac=xcb-util;            zyp=libxcb-util1;;
        libxcb-*.so.*)           apt="$(echo "$s" | sed 's/\.so\.//')"; dnf=libxcb; pac=libxcb; zyp=$apt;;
        libxcb.so.1)             apt=libxcb1;              dnf=libxcb;              pac=libxcb;              zyp=libxcb1;;
        libX11-xcb.so.1)         apt=libx11-xcb1;          dnf=libX11-xcb;          pac=libx11;              zyp=libX11-xcb1;;
        libxkbcommon-x11.so.0)   apt=libxkbcommon-x11-0;   dnf=libxkbcommon-x11;    pac=libxkbcommon-x11;    zyp=libxkbcommon-x11-0;;
        libxkbcommon.so.0)       apt=libxkbcommon0;        dnf=libxkbcommon;        pac=libxkbcommon;        zyp=libxkbcommon0;;
        libEGL.so.1)             apt=libegl1;              dnf=libglvnd-egl;        pac=libglvnd;            zyp=libEGL1;;
        libGL.so.1)              apt=libgl1;               dnf=libglvnd-glx;        pac=libglvnd;            zyp=libGL1;;
        libOpenGL.so.0)          apt=libopengl0;           dnf=libglvnd-opengl;     pac=libglvnd;            zyp=libOpenGL0;;
        libGLX.so.0)             apt=libglx0;              dnf=libglvnd-glx;        pac=libglvnd;            zyp=libGLX0;;
        libGLU.so.1)             apt=libglu1-mesa;         dnf=mesa-libGLU;         pac=glu;                 zyp=libGLU1;;
        libXcursor.so.1)         apt=libxcursor1;          dnf=libXcursor;          pac=libxcursor;          zyp=libXcursor1;;
        libXinerama.so.1)        apt=libxinerama1;         dnf=libXinerama;         pac=libxinerama;         zyp=libXinerama1;;
        libXft.so.2)             apt=libxft2;              dnf=libXft;              pac=libxft;              zyp=libXft2;;
        libXrender.so.1)         apt=libxrender1;          dnf=libXrender;          pac=libxrender;          zyp=libXrender1;;
        libXext.so.6)            apt=libxext6;             dnf=libXext;             pac=libxext;             zyp=libXext6;;
        libXi.so.6)              apt=libxi6;               dnf=libXi;               pac=libxi;               zyp=libXi6;;
        libXfixes.so.3)          apt=libxfixes3;           dnf=libXfixes;           pac=libxfixes;           zyp=libXfixes3;;
        libX11.so.6)             apt=libx11-6;             dnf=libX11;              pac=libx11;              zyp=libX11-6;;
        libSM.so.6)              apt=libsm6;               dnf=libSM;               pac=libsm;               zyp=libSM6;;
        libICE.so.6)             apt=libice6;              dnf=libICE;              pac=libice;              zyp=libICE6;;
        libfontconfig.so.1)      apt=libfontconfig1;       dnf=fontconfig;          pac=fontconfig;          zyp=libfontconfig1;;
        libfreetype.so.6)        apt=libfreetype6;         dnf=freetype;            pac=freetype2;           zyp=libfreetype6;;
        libdbus-1.so.3)          apt=libdbus-1-3;          dnf=dbus-libs;           pac=dbus;                zyp=libdbus-1-3;;
        *) return 1;;
    esac
    case "$pm" in apt-get) echo "$apt";; dnf) echo "$dnf";; pacman) echo "$pac";; zypper) echo "$zyp";; *) echo "$apt";; esac
}

pkgs=(); unknown=()
for l in "${missing[@]-}"; do
    [ -n "$l" ] || continue
    if p="$(pkg "$l")"; then
        case " ${pkgs[*]-} " in *" $p "*) ;; *) pkgs+=("$p");; esac
    else
        unknown+=("$l")
    fi
done

cmd=""
if [ ${#pkgs[@]} -gt 0 ]; then
    case "$pm" in
        dnf)    cmd="sudo dnf install ${pkgs[*]}";;
        pacman) cmd="sudo pacman -S --needed ${pkgs[*]}";;
        zypper) cmd="sudo zypper install ${pkgs[*]}";;
        *)      cmd="sudo apt install ${pkgs[*]}";;
    esac
fi

if [ "${1-}" = "--command" ]; then
    echo "$cmd"
    [ ${#missing[@]} -eq 0 ]; exit
fi
if [ ${#missing[@]} -eq 0 ]; then
    echo "System libraries: all present."
    exit 0
fi
echo "Missing system libraries, without which the Hertzon window cannot open: ${missing[*]}"
[ -n "$cmd" ] && echo "Install them with:" && echo "  $cmd"
[ ${#unknown[@]} -gt 0 ] && echo "Also install the package that provides: ${unknown[*]}"
exit 1
