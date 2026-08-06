#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$ROOT_DIR"

echo "=========================================="
echo "   Stopping BetterAgent Microservices (Linux)"
echo "=========================================="

RUN_PID_FILE="$ROOT_DIR/logs/run.pid"

if [ -f "$RUN_PID_FILE" ]; then
    while IFS= read -r pid; do
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            echo "Sending SIGTERM to PID: $pid ..."
            kill -15 "$pid" || kill -9 "$pid"
        fi
    done < "$RUN_PID_FILE"
    rm -f "$RUN_PID_FILE"
    echo "PIDs terminated and logs/run.pid removed."
else
    echo "No logs/run.pid file found."
fi

if command -v docker >/dev/null 2>&1; then
    echo "Stopping Docker containers..."
    docker compose -f deploy/docker-compose.yml stop
fi

echo "=========================================="
echo " All BetterAgent services stopped cleanly."
echo "=========================================="
