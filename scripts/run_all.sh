#!/usr/bin/env bash
# AP2 x402 Demo — 启动所有三个服务
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

# Load .env
if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

WALLET_PORT="${WALLET_SERVICE_PORT:-5001}"
MERCHANT_PORT="${MERCHANT_SERVICE_PORT:-8002}"
CLIENT_PORT="${CLIENT_SERVICE_PORT:-8000}"

echo "=== Starting AP2 x402 Demo ==="
echo ""

# Trap to kill all background processes on exit
cleanup() {
    echo ""
    echo "Shutting down all services..."
    kill $(jobs -p) 2>/dev/null || true
    wait 2>/dev/null || true
    echo "Done."
}
trap cleanup EXIT INT TERM

# 1. Wallet Service (Flask :5001)
echo "[1/3] Starting Wallet Service on :${WALLET_PORT}..."
uv run python -m wallet.server &
sleep 1

# Health check: verify wallet service is responding
echo "      Checking Wallet Service health..."
if curl -sf "http://localhost:${WALLET_PORT}/address" > /dev/null 2>&1; then
    WALLET_ADDR=$(curl -sf "http://localhost:${WALLET_PORT}/address" | python3 -c "import sys,json; print(json.load(sys.stdin)['address'])" 2>/dev/null || echo "unknown")
    echo "      Wallet Service is up ✓ (address: ${WALLET_ADDR})"
else
    echo "      WARNING: Wallet Service health check failed, continuing anyway..."
fi

# 2. Merchant Agent (Starlette :8002)
echo "[2/3] Starting Merchant Agent on :${MERCHANT_PORT}..."
uv run python -m merchant --port "${MERCHANT_PORT}" &
sleep 2

# 3. Client Agent (ADK Web UI :8000)
echo "[3/3] Starting Client Agent (ADK Web UI) on :${CLIENT_PORT}..."
uv run adk web --port "${CLIENT_PORT}" client/ &

echo ""
echo "=== All services running ==="
echo "  Wallet Service:  http://localhost:${WALLET_PORT}"
echo "  Merchant Agent:  http://localhost:${MERCHANT_PORT}"
echo "  Client Web UI:   http://localhost:${CLIENT_PORT}"
echo ""
echo "Press Ctrl+C to stop all services."

# Wait for all background processes
wait
