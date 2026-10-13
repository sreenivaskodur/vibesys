#!/usr/bin/env bash
# Build the HammerDB toolchain on macOS (Apple Silicon) without touching system Tcl.
#
# HammerDB ships no macOS release, so it runs from source on Homebrew's Tcl 9:
#   - HammerDB v6.0 (TPC-Council/HammerDB), with bin/tclsh9.0 -> Homebrew tclsh
#   - Pgtcl (flightaware/Pgtcl) built against libpq, patched for Tcl 9 channel types,
#     plus a no-op pg_notice_handler (pgtclng API HammerDB calls to echo NOTICEs)
#   - ticklecharts (pure Tcl, bundled in HammerDB's Linux releases)
#   - samply, codesigned so it can attach to running processes
#
# Usage: setup_tools.sh <tools-dir>     (prints the env exports to use afterwards)
set -euo pipefail
TOOLS=$(mkdir -p "${1:?tools dir}" && cd "$1" && pwd)
HERE=$(cd "$(dirname "$0")" && pwd)
HAMMERDB_TAG=v6.0
PGTCL_COMMIT=6580a1cd446bdbe324c517cb3a037876333529fb
TICKLECHARTS_COMMIT=b49f014cb6c2412a7e661e805a37eaebcd625c59

brew install tcl-tk libpq autoconf samply >/dev/null
TCL=$(brew --prefix tcl-tk); PQ=$(brew --prefix libpq)

if [ ! -d "$TOOLS/HammerDB" ]; then
  git clone -q --depth 1 --branch $HAMMERDB_TAG https://github.com/TPC-Council/HammerDB.git "$TOOLS/HammerDB"
fi
mkdir -p "$TOOLS/HammerDB/bin"
ln -sf "$(ls "$TCL"/bin/tclsh9.* | head -1)" "$TOOLS/HammerDB/bin/tclsh9.0"

if [ ! -f "$TOOLS/lib/pgtcl/pkgIndex.tcl" ]; then
  rm -rf "$TOOLS/src/Pgtcl"; mkdir -p "$TOOLS/src"
  git clone -q https://github.com/flightaware/Pgtcl.git "$TOOLS/src/Pgtcl"
  (cd "$TOOLS/src/Pgtcl" && git checkout -q $PGTCL_COMMIT && git apply "$HERE/pgtcl-tcl9.patch" \
     && autoreconf -fi >/dev/null 2>&1 \
     && ./configure -q --with-tcl="$TCL/lib" --with-postgres-include="$PQ/include" --with-postgres-lib="$PQ/lib" >/dev/null \
     && make >/dev/null)   # serial: the generated Makefile races under -j
  mkdir -p "$TOOLS/lib/pgtcl"
  cp "$TOOLS"/src/Pgtcl/libpgtcl*.dylib "$TOOLS/lib/pgtcl/"
  lib=$(basename "$(ls "$TOOLS"/lib/pgtcl/libpgtcl*.dylib | head -1)")
  ver=$(echo "$lib" | sed -E 's/libpgtcl([0-9.]+)\.dylib/\1/')
  install_name_tool -id "$TOOLS/lib/pgtcl/$lib" "$TOOLS/lib/pgtcl/$lib"
  cat > "$TOOLS/lib/pgtcl/pkgIndex.tcl" <<EOF
# HammerDB targets the pgtclng API; pg_notice_handler only echoes server NOTICEs.
package ifneeded Pgtcl $ver "[list load [file join \$dir $lib]]; proc ::pg_notice_handler {args} {}"
EOF
fi

if [ ! -d "$TOOLS/lib/ticklecharts" ]; then
  git clone -q https://github.com/nico-robert/ticklecharts.git "$TOOLS/lib/ticklecharts"
  git -C "$TOOLS/lib/ticklecharts" checkout -q $TICKLECHARTS_COMMIT
fi

samply setup -y >/dev/null

echo "puts [package require Pgtcl]" | TCLLIBPATH="$TOOLS/lib" "$TOOLS/HammerDB/bin/tclsh9.0" >/dev/null
echo "export HAMMERDB_DIR=$TOOLS/HammerDB"
echo "export TCLLIBPATH=\"$TOOLS/lib $TOOLS/lib/ticklecharts\""
