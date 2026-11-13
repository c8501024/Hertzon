#!/bin/sh
# Build hzmesh into ../_bin: libhzmesh.so (Linux) and, when mingw-w64 is installed, hzmesh.dll (Windows).
# On macOS it builds libhzmesh.dylib instead (a universal binary for arm64 and x86_64, Apple clang).
# -ffp-contract=off keeps the compiler from fusing multiply-adds, which would break the exact predicates.
set -e
cd "$(dirname "$0")"
MAP="-ffile-prefix-map=$(pwd)=."
mkdir -p build
if [ "$(uname -s)" = "Darwin" ]; then
  ARCH="-arch arm64 -arch x86_64 -mmacosx-version-min=11.0"
  clang -O2 $MAP $ARCH -fPIC -ffp-contract=off -c predicates.c -o build/predicates.o
  clang++ -O2 $MAP $ARCH -std=c++17 -fPIC -Wall -Wextra -ffp-contract=off -c hzmesh.cpp -o build/hzmesh.o
  clang++ $ARCH -dynamiclib -install_name @rpath/libhzmesh.dylib -o build/libhzmesh.dylib \
    build/hzmesh.o build/predicates.o
  cp build/libhzmesh.dylib ../_bin/libhzmesh.dylib
  exit 0
fi
gcc -O2 $MAP -fPIC -ffp-contract=off -c predicates.c -o build/predicates.o
g++ -O2 $MAP -g -std=c++17 -fPIC -Wall -Wextra -ffp-contract=off -c hzmesh.cpp -o build/hzmesh.o
g++ -shared -o build/libhzmesh.so build/hzmesh.o build/predicates.o
cp build/libhzmesh.so ../_bin/libhzmesh.so
if command -v x86_64-w64-mingw32-g++ >/dev/null 2>&1; then
  x86_64-w64-mingw32-gcc -O2 $MAP -ffp-contract=off -c predicates.c -o build/predicates.win.o
  x86_64-w64-mingw32-g++ -O2 $MAP -std=c++17 -ffp-contract=off -c hzmesh.cpp -o build/hzmesh.win.o
  x86_64-w64-mingw32-g++ -shared -static -static-libgcc -static-libstdc++ -o build/hzmesh.dll \
    build/hzmesh.win.o build/predicates.win.o
  cp build/hzmesh.dll ../_bin/hzmesh.dll
fi
