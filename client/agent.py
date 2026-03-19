"""
Root agent entry point — ADK web UI 入口。
创建 ClientAgent 实例，连接到 Merchant Agent。
"""

import os

import httpx
from dotenv import load_dotenv

from client.client_agent import ClientAgent
from client.wallet_client import RemoteWallet, LocalWallet
from client.task_store import TaskStore

load_dotenv()

# 根据环境变量选择 wallet 实现
wallet_service_url = os.getenv("WALLET_SERVICE_URL", "http://localhost:5001")
use_remote_wallet = os.getenv("USE_REMOTE_WALLET", "true").lower() == "true"

if use_remote_wallet:
    wallet = RemoteWallet(wallet_service_url)
else:
    wallet = LocalWallet()

# Merchant agent 地址
merchant_port = os.getenv("MERCHANT_SERVICE_PORT", "8002")
merchant_url = f"http://localhost:{merchant_port}/agents/merchant_agent"

root_agent = ClientAgent(
    remote_agent_addresses=[merchant_url],
    http_client=httpx.AsyncClient(timeout=30),
    wallet=wallet,
    task_callback=TaskStore().update_task,
).create_agent()
