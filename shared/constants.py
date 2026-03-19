"""Network constants — x402 v2 uses CAIP-2 format."""

# CAIP-2 chain identifier for Base Sepolia
# v1 used "base-sepolia", v2 uses CAIP-2 format
NETWORK = "eip155:84532"
CAIP2_CHAIN_ID = 84532

# USDC token contract on Base Sepolia
USDC_ADDRESS = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"

# x402 protocol version
X402_VERSION = 2

# Default timeout for payment (seconds)
PAYMENT_TIMEOUT_SECONDS = 1200

# LLM model for both agents
LLM_MODEL = "gemini-2.5-flash"
