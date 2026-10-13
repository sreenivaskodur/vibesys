#!/usr/bin/env bash
# Linux counterpart of setup_tools.sh. HammerDB publishes Linux release
# tarballs with bundled Tcl, pgtcl, and ticklecharts, so setup is download +
# extract — no source builds. Neon's own build dependencies (rustup, flex,
# bison, libpq-dev, protobuf-compiler, libssl-dev, libicu-dev, pkg-config,
# libreadline-dev, zlib1g-dev, m4) are expected from the system package
# manager.
#
# Usage: setup_tools_linux.sh <tools-dir>
set -euo pipefail
TOOLS=$(mkdir -p "${1:?tools dir}" && cd "$1" && pwd)
HAMMERDB_VERSION=6.0
URL="https://github.com/TPC-Council/HammerDB/releases/download/v${HAMMERDB_VERSION}/HammerDB-${HAMMERDB_VERSION}-Linux.tar.gz"

if [ ! -x "$TOOLS/HammerDB/hammerdbcli" ]; then
  tmp=$(mktemp -d)
  trap 'rm -rf "$tmp"' EXIT
  curl -fsSL "$URL" -o "$tmp/hammerdb.tgz"
  tar -xzf "$tmp/hammerdb.tgz" -C "$tmp"
  rm -rf "$TOOLS/HammerDB"
  mv "$tmp/HammerDB-${HAMMERDB_VERSION}" "$TOOLS/HammerDB"
fi

# Smoke-check the bundled Tcl runtime and the Postgres driver it ships.
(cd "$TOOLS/HammerDB" && echo 'puts [package require Pgtcl]; exit' \
   | TCLLIBPATH="$TOOLS/HammerDB/lib" ./bin/tclsh8.6 2>/dev/null) \
  | grep -q . || { echo "error: bundled Pgtcl failed to load" >&2; exit 1; }

echo "HammerDB $HAMMERDB_VERSION ready at $TOOLS/HammerDB"
