# AP2 x402 Demo

A2A + x402 v2 payment protocol demo with three processes:

| Service | Port | Description |
|---------|------|-------------|
| Wallet Service | :5001 | Flask — manages private keys, EIP-712 signing |
| Merchant Agent | :8002 | Starlette — A2A Server, x402 payment flow |
| Client Agent | :8000 | ADK Web UI — user interaction, auto payment |

## Quick Start

```bash
# Install dependencies
uv sync

# Copy and edit env
cp .env.example .env

# Run all services
bash scripts/run_all.sh
```

## x402 v2 Protocol

This demo uses x402 **v2** format:
- CAIP-2 network identifiers (`eip155:84532` for Base Sepolia)
- `amount` field (replaces v1 `maxAmountRequired`)
- Structured `resource` object in PaymentRequired/PaymentPayload
- `extensions` field support

## Architecture

```
Client Agent (ADK) → A2A → Merchant Agent (ADK + x402)
                              ↕
                        Wallet Service (Flask)
                              ↕
                        Facilitator (Mock/Real)
```
