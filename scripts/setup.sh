#!/usr/bin/env bash
# AP2 x402 Demo — 环境初始化脚本
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "=== AP2 x402 Demo Setup ==="
cd "$PROJECT_DIR"

# Check Python version (requires 3.13+)
if command -v python3 &>/dev/null; then
    PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
    PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
    if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 13 ]; }; then
        echo "ERROR: Python 3.13+ is required, but found Python $PY_VERSION"
        exit 1
    fi
    echo "Python $PY_VERSION detected ✓"
else
    echo "ERROR: python3 not found. Please install Python 3.13+."
    exit 1
fi

# Check uv availability
if ! command -v uv &>/dev/null; then
    echo "ERROR: uv not found. Install it: https://docs.astral.sh/uv/getting-started/installation/"
    exit 1
fi
echo "uv $(uv --version | awk '{print $2}') detected ✓"

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
