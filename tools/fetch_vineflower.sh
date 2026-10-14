#!/usr/bin/env bash
# Fetch the decompiler used by tools/decompile_class.sh.
#
# Vineflower is a third-party binary, so it is not committed here: we download the
# exact release the notes were produced with and verify its SHA-256 before use.
#
# Usage: fetch_vineflower.sh [--force]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="$ROOT/tools/vineflower.jar"
VERSION="1.12.0"
URL="https://repo1.maven.org/maven2/org/vineflower/vineflower/${VERSION}/vineflower-${VERSION}.jar"
SHA256="1dfcfe974395734fa467ce620661c7623d05ba83670de0529b1fbd63ff548b9d"

if [ -f "$DEST" ] && [ "${1:-}" != "--force" ]; then
  have="$(sha256sum "$DEST" | cut -d' ' -f1)"
  if [ "$have" = "$SHA256" ]; then
    echo "vineflower $VERSION already present and verified ($DEST)"
    exit 0
  fi
  echo "existing $DEST does not match the pinned checksum, re-downloading"
fi

tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
echo "downloading $URL"
curl -fsSL -o "$tmp" "$URL"

have="$(sha256sum "$tmp" | cut -d' ' -f1)"
if [ "$have" != "$SHA256" ]; then
  echo "checksum mismatch for vineflower $VERSION" >&2
  echo "  expected $SHA256" >&2
  echo "  got      $have" >&2
  exit 1
fi

mv "$tmp" "$DEST"
echo "wrote $DEST (vineflower $VERSION, sha256 verified)"
