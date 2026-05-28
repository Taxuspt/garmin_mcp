#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
OUTPUT="$REPO_ROOT/garmin-mcp.dxt"
BUILD_DIR="$REPO_ROOT/.dxt-build/garmin-mcp"

rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"

cp "$REPO_ROOT/dxt/manifest.json" "$BUILD_DIR/manifest.json"
cp "$REPO_ROOT/pyproject.toml" "$BUILD_DIR/pyproject.toml"
cp "$REPO_ROOT/uv.lock" "$BUILD_DIR/uv.lock"
cp "$REPO_ROOT/README.md" "$BUILD_DIR/README.md"
cp -R "$REPO_ROOT/src" "$BUILD_DIR/src"
find "$BUILD_DIR" -type d -name "__pycache__" -prune -exec rm -rf {} +
find "$BUILD_DIR" -type f \( -name "*.pyc" -o -name "*.pyo" \) -delete

cd "$REPO_ROOT"
npx --yes @anthropic-ai/dxt validate "$BUILD_DIR/manifest.json"
npx --yes @anthropic-ai/dxt pack "$BUILD_DIR" "$OUTPUT"
