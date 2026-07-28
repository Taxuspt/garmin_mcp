#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
OUTPUT="$REPO_ROOT/garmin-mcp.dxt"

rm -f "$OUTPUT"
cd "$REPO_ROOT/dxt"
zip "$OUTPUT" manifest.json
echo "Built: $OUTPUT ($(du -h "$OUTPUT" | cut -f1))"
