"""Environment variable loading via dotenv."""

import os
from dotenv import load_dotenv


def load_config() -> dict:
    """加载 .env 配置，返回关键配置项字典。"""
    load_dotenv()
    return {
        "google_api_key": os.getenv("GOOGLE_API_KEY", ""),
        "wallet_private_key": os.getenv(
            "WALLET_PRIVATE_KEY",
            "0x0000000000000000000000000000000000000000000000000000000000000001",
        ),
        "merchant_wallet_address": os.getenv(
            "MERCHANT_WALLET_ADDRESS",
            "0xAb5801a7D398351b8bE11C439e05C5B3259aeC9B",
        ),
        "rpc_url": os.getenv("RPC_URL", "https://sepolia.base.org"),
        "wallet_service_port": int(os.getenv("WALLET_SERVICE_PORT", "5001")),
        "merchant_service_port": int(os.getenv("MERCHANT_SERVICE_PORT", "8002")),
        "client_service_port": int(os.getenv("CLIENT_SERVICE_PORT", "8000")),
        "use_mock_facilitator": os.getenv("USE_MOCK_FACILITATOR", "true").lower() == "true",
    }
