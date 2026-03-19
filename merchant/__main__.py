"""
Merchant Agent Server — Starlette :8002
启动 A2A 服务端，注册商户 agent 路由。
"""

import os
import logging

import click
import uvicorn
from dotenv import load_dotenv
from starlette.applications import Starlette

from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from google.adk.runners import Runner
from google.adk.artifacts import InMemoryArtifactService
from google.adk.memory.in_memory_memory_service import InMemoryMemoryService
from google.adk.sessions import InMemorySessionService

from .agent import MerchantAgent
from .executor import ADKAgentExecutor
from .x402_executor import x402MerchantExecutor

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def create_app(host: str, port: int) -> Starlette:
    """构建 Starlette 应用，注册商户 agent 路由。"""
    base_url = f"http://{host}:{port}"
    base_path = "/agents"
    agent_path = "merchant_agent"
    full_path = f"{base_path}/{agent_path}"
    url = f"{base_url}{full_path}"

    # 读取商户钱包地址
    wallet_address = os.getenv(
        "MERCHANT_WALLET_ADDRESS",
        "0xAb5801a7D398351b8bE11C439e05C5B3259aeC9B",
    )
    merchant = MerchantAgent(wallet_address=wallet_address)
    agent_card = merchant.create_agent_card(url)
    agent = merchant.create_agent()

    # 构建 ADK Runner
    runner = Runner(
        app_name=agent_card.name,
        agent=agent,
        artifact_service=InMemoryArtifactService(),
        session_service=InMemorySessionService(),
        memory_service=InMemoryMemoryService(),
    )

    # 执行链: x402MerchantExecutor → ADKAgentExecutor → ADK LlmAgent
    delegate_executor = ADKAgentExecutor(runner, agent_card)
    x402_executor = x402MerchantExecutor(delegate_executor)

    # A2A 路由
    request_handler = DefaultRequestHandler(
        agent_executor=x402_executor,
        task_store=InMemoryTaskStore(),
    )
    a2a_app = A2AStarletteApplication(
        agent_card=agent_card,
        http_handler=request_handler,
    )

    agent_card_url = f"{full_path}/.well-known/agent-card.json"
    routes = a2a_app.routes(agent_card_url=agent_card_url, rpc_url=full_path)
    logger.info(f"Agent card: {agent_card_url}")

    return Starlette(routes=routes)


@click.command()
@click.option("--host", default="localhost")
@click.option("--port", default=8002, type=int)
def main(host: str, port: int):
    if not os.getenv("GOOGLE_API_KEY"):
        raise ValueError("GOOGLE_API_KEY environment variable not set.")

    app = create_app(host, port)
    logger.info(f"Merchant Agent starting on {host}:{port}")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
