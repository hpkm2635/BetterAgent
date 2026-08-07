#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$ROOT_DIR"

echo "=========================================="
echo "   Starting BetterAgent Microservices (Linux)"
echo "=========================================="

# Determine Python Binary (.venv preferred)
PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
if [ ! -f "$PYTHON_BIN" ]; then
    PYTHON_BIN="python3"
fi

if [ -f "$ROOT_DIR/runner.py" ]; then
    exec "$PYTHON_BIN" "$ROOT_DIR/runner.py" "$@"
else
    echo "[!] runner.py not found in project root."
    exit 1
fi
