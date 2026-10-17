#!/usr/bin/env bash
# Vendor cytoscape.min.js (MIT) into the dashboard so it works offline (spec 5.3, rule D3).
set -euo pipefail
VERSION="${CYTOSCAPE_VERSION:-3.30.2}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$ROOT/chainwatch/serving/static/vendor"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
echo "npm pack cytoscape@$VERSION"
(cd "$TMP" && npm pack "cytoscape@$VERSION" --silent >/dev/null)
tar -xzf "$TMP"/cytoscape-*.tgz -C "$TMP"
mkdir -p "$DEST"
cp "$TMP/package/dist/cytoscape.min.js" "$DEST/cytoscape.min.js"
cp "$TMP/package/LICENSE" "$DEST/cytoscape.LICENSE"
echo "cytoscape $VERSION" > "$DEST/VERSIONS.txt"
ls -l "$DEST"
