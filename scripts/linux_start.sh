#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$ROOT_DIR"

echo "=========================================="
echo "   Starting BetterAgent Microservices (Linux)"
echo "=========================================="

mkdir -p logs temp

# 1. Start Docker containers if Docker Compose is installed
if command -v docker >/dev/null 2>&1; then
    echo "[1/4] Starting Docker Compose containers..."
    docker compose -f deploy/docker-compose.yml up -d
else
    echo "[1/4] Docker not found. Skipping container startup..."
fi

RUN_PID_FILE="$ROOT_DIR/logs/run.pid"
> "$RUN_PID_FILE"

# Determine Python Binary
PYTHON_BIN="$ROOT_DIR/venv/bin/python"
if [ ! -f "$PYTHON_BIN" ]; then
    PYTHON_BIN="python3"
fi

# 2. Start Go Core
echo "[2/4] Starting Go Core (betteragent-core)..."
cd "$ROOT_DIR/core"
if [ -f "./betteragent-core" ]; then
    nohup ./betteragent-core > "../logs/betteragent_core.log" 2>&1 &
else
    nohup go run ./cmd/main.go > "../logs/betteragent_core.log" 2>&1 &
fi
GO_PID=$!
echo "$GO_PID" >> "$RUN_PID_FILE"
echo "     -> Go Core PID: $GO_PID"

# 3. Start Python Memory Service
cd "$ROOT_DIR"
echo "[3/4] Starting Python Memory Service..."
nohup "$PYTHON_BIN" -m services.memory.main > "logs/memory_service.log" 2>&1 &
MEM_PID=$!
echo "$MEM_PID" >> "$RUN_PID_FILE"
echo "     -> Memory Service PID: $MEM_PID"

# 4. Start Python Cognitive Service
echo "[4/4] Starting Python Cognitive Service..."
nohup "$PYTHON_BIN" -m services.cognitive.main > "logs/cognitive_service.log" 2>&1 &
COG_PID=$!
echo "$COG_PID" >> "$RUN_PID_FILE"
echo "     -> Cognitive Service PID: $COG_PID"

echo "=========================================="
echo " All BetterAgent microservices launched!"
echo " PIDs recorded in logs/run.pid"
echo " Logs stored in logs/ directory"
echo "=========================================="
