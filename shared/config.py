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
            "0x39e7972056220aba339638c79a0785da96a98c3ec41aeea5ec5e5643fdc9df6b",
        ),
        "merchant_wallet_address": os.getenv(
            "MERCHANT_WALLET_ADDRESS",
            "0x92F6E9deBbEb778a245916Cf52DD7F54429Fff24",
        ),
        "rpc_url": os.getenv("RPC_URL", "https://sepolia.base.org"),
        "wallet_service_port": int(os.getenv("WALLET_SERVICE_PORT", "5001")),
        "merchant_service_port": int(os.getenv("MERCHANT_SERVICE_PORT", "8002")),
        "client_service_port": int(os.getenv("CLIENT_SERVICE_PORT", "8000")),
        "use_mock_facilitator": os.getenv("USE_MOCK_FACILITATOR", "true").lower() == "true",
    }
