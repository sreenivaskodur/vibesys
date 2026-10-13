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
# Ubuntu 24 x86_64 build; see the release page for other distros/arches.
ASSET="HammerDB-${HAMMERDB_VERSION}-Prod-Lin-UBU24.tar.gz"
URL="https://github.com/TPC-Council/HammerDB/releases/download/v${HAMMERDB_VERSION}/${ASSET}"

if [ ! -x "$TOOLS/HammerDB/hammerdbcli" ]; then
  tmp=$(mktemp -d)
  trap 'rm -rf "$tmp"' EXIT
  curl -fsSL "$URL" -o "$tmp/hammerdb.tgz"
  tar -xzf "$tmp/hammerdb.tgz" -C "$tmp"
  cli=$(find "$tmp" -maxdepth 3 -name hammerdbcli -type f | head -1)
  [ -n "$cli" ] || { echo "error: hammerdbcli not found in $ASSET" >&2; exit 1; }
  rm -rf "$TOOLS/HammerDB"
  mv "$(dirname "$cli")" "$TOOLS/HammerDB"
  chmod +x "$TOOLS/HammerDB/hammerdbcli"
fi

[ -d "$TOOLS/HammerDB/lib" ] || { echo "error: bundled lib/ missing" >&2; exit 1; }
echo "HammerDB $HAMMERDB_VERSION ready at $TOOLS/HammerDB"
