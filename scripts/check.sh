#!/usr/bin/env bash
# Local preflight — mirrors CI exactly. Run before every push.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "=== Lint ==="
uv sync --locked

uv run --no-sync ruff check .
uv run --no-sync ruff format --check .

echo "=== Test ==="
uv run --no-sync pytest tests/ -q --cov=custom_components.foxess.sun_state --cov-report=term-missing --cov-fail-under=90

echo ""
echo "✅ All checks passed — safe to push."
