#!/usr/bin/env bash
# Builds the Windows installer (dist/Hertzon-Setup-<version>.exe, needs NSIS: apt install nsis)
# the Linux package (dist/Hertzon-<version>-linux.tar.gz) and the macOS package for Apple Silicon
# (dist/Hertzon-<version>-macos-arm64.tar.gz).
set -euo pipefail
cd "$(dirname "$0")/.."
VER=$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml)
STAGE=build/installer
rm -rf "$STAGE" && mkdir -p "$STAGE/win" "$STAGE/linux/Hertzon-$VER" "$STAGE/macos/Hertzon-$VER" dist

# program files: tracked files only (no caches, results or the local .venv)
stage() {   # stage DEST FILES...
    local dest=$1; shift
    git ls-files -z -- "$@" | xargs -0 cp --parents -t "$dest"
}
stage "$STAGE/win" hertzon claude-plugin .claude-plugin examples docs/FEATURE_MATRIX.md docs/THIRD_PARTY.md Hertzon.exe setup.ps1 run.ps1 pyproject.toml README.md LICENSE
rm -f "$STAGE/win/hertzon/mesher/_bin/libhzmesh.so"
makensis -V2 -DVERSION="$VER" -DSTAGE="$PWD/$STAGE/win" -DOUT="$PWD/dist/Hertzon-Setup-$VER.exe" packaging/installer.nsi

L="$STAGE/linux/Hertzon-$VER"
stage "$L" hertzon claude-plugin .claude-plugin examples docs/FEATURE_MATRIX.md docs/THIRD_PARTY.md pyproject.toml README.md LICENSE
rm -f "$L/hertzon/mesher/_bin/hzmesh.dll"
cp packaging/linux/install.sh packaging/linux/uninstall.sh packaging/linux/syscheck.sh "$L/"
chmod +x "$L/install.sh" "$L/uninstall.sh" "$L/syscheck.sh"
tar -C "$STAGE/linux" -czf "dist/Hertzon-$VER-linux.tar.gz" "Hertzon-$VER"

M="$STAGE/macos/Hertzon-$VER"
stage "$M" hertzon claude-plugin .claude-plugin examples docs/FEATURE_MATRIX.md docs/THIRD_PARTY.md pyproject.toml README.md LICENSE
rm -f "$M/hertzon/mesher/_bin/hzmesh.dll" "$M/hertzon/mesher/_bin/libhzmesh.so"
cp packaging/macos/install.sh packaging/macos/uninstall.sh "$M/"
chmod +x "$M/install.sh" "$M/uninstall.sh"
tar -C "$STAGE/macos" -czf "dist/Hertzon-$VER-macos-arm64.tar.gz" "Hertzon-$VER"
ls -la dist
