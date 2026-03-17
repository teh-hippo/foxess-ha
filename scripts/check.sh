#!/usr/bin/env bash
# Local preflight — mirrors CI exactly. Run before every push.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "=== Lint ==="
uv run ruff check .
uv run ruff format --check .

echo ""
echo "✅ All checks passed — safe to push."
