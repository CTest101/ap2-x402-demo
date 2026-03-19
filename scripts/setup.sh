#!/usr/bin/env bash
# AP2 x402 Demo — 环境初始化脚本
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "=== AP2 x402 Demo Setup ==="
cd "$PROJECT_DIR"

# Install dependencies via uv
echo "Installing dependencies..."
uv sync

# Copy .env if not exists
if [ ! -f .env ]; then
    cp .env.example .env
    echo "Created .env from .env.example — please edit with your API keys."
else
    echo ".env already exists, skipping."
fi

echo "=== Setup complete ==="
echo "Run: bash scripts/run_all.sh"
