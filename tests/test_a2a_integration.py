"""
A2A Integration Test — Real HTTP server with scripted merchant executor.

Runs a real A2A Starlette server on a random port with uvicorn in a background
thread.  The merchant executor is scripted (no LLM) so the full x402 middleware
chain is exercised over real HTTP JSON-RPC requests.

Flow:
  1. Client sends message/send "buy a banana" → Task input-required + payment metadata
  2. Client signs payment requirements via wallet
  3. Client sends message/send with payment-submitted metadata → Task completed + receipts
"""

import asyncio
import socket
import threading
import time
import uuid

import httpx
import pytest
import uvicorn
from starlette.applications import Starlette

from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.server.agent_execution import AgentExecutor
from a2a.server.agent_execution.context import RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import (
    AgentCard,
    AgentCapabilities,
    AgentSkill,
    Part,
    TaskState,
    TextPart,
)

from x402_a2a.types import (
    PaymentRequirements,
    x402PaymentRequiredException,
    x402ExtensionConfig,
)
from x402_a2a.executors import x402ServerExecutor
from x402_a2a.core.wallet import process_payment

from merchant.facilitator import MockFacilitator
from shared.constants import NETWORK, USDC_ADDRESS, PAYMENT_TIMEOUT_SECONDS

from eth_account import Account


# ── Scripted merchant executor (no LLM) ─────────────────────────


MERCHANT_WALLET = "0xAb5801a7D398351b8bE11C439e05C5B3259aeC9B"
PRODUCT_NAME = "banana"
PRODUCT_PRICE = "50000"


class ScriptedMerchantExecutor(AgentExecutor):
    """Deterministic executor that mimics a merchant agent without any LLM.

    - First call: raises x402PaymentRequiredException (like a real agent tool would)
    - Payment-verified call: completes the task with an artifact
    """

    async def execute(self, context: RequestContext, event_queue: EventQueue):
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)

        # Payment-verified continuation
        if (
            context.current_task
            and context.current_task.metadata
            and context.current_task.metadata.get("x402_payment_verified")
        ):
            await updater.add_artifact(
                [Part(root=TextPart(text=f"Order confirmed! Your {PRODUCT_NAME} is on the way."))]
            )
            await updater.complete()
            return

        # First call — request payment
        requirements = PaymentRequirements(
            scheme="exact",
            network=NETWORK,
            asset=USDC_ADDRESS,
            pay_to=MERCHANT_WALLET,
            amount=PRODUCT_PRICE,
            max_timeout_seconds=PAYMENT_TIMEOUT_SECONDS,
            extra={
                "name": "USDC",
                "version": "2",
                "product": {"sku": "banana_sku", "name": PRODUCT_NAME, "version": "1"},
            },
        )
        raise x402PaymentRequiredException(PRODUCT_NAME, requirements)

    async def cancel(self, context: RequestContext, event_queue: EventQueue):
        pass


# ── x402 executor with injectable facilitator ───────────────────


class _x402TestExecutor(x402ServerExecutor):
    """Test-only x402 executor that accepts a facilitator directly."""

    def __init__(self, delegate: AgentExecutor, facilitator: MockFacilitator):
        super().__init__(delegate, x402ExtensionConfig())
        self._facilitator = facilitator

    async def verify_payment(self, payload, requirements):
        return await self._facilitator.verify(payload, requirements)

    async def settle_payment(self, payload, requirements):
        return await self._facilitator.settle(payload, requirements)


# ── Server fixture ───────────────────────────────────────────────


# 固定端口用于集成测试，避免每次随机
A2A_TEST_PORT = 19402


def _build_app(host: str, port: int) -> Starlette:
    """Build a real A2A Starlette app with the scripted executor chain."""
    base_url = f"http://{host}:{port}"
    rpc_path = "/agents/merchant_agent"
    url = f"{base_url}{rpc_path}"

    agent_card = AgentCard(
        name="Test Merchant",
        description="Scripted merchant for integration tests",
        url=url,
        version="1.0.0",
        defaultInputModes=["text"],
        defaultOutputModes=["text"],
        capabilities=AgentCapabilities(streaming=False),
        skills=[
            AgentSkill(
                id="buy_product",
                name="Buy Product",
                description="Purchase products with x402 payment.",
                tags=["x402"],
                examples=["buy a banana"],
            )
        ],
    )

    facilitator = MockFacilitator(is_valid=True, is_settled=True)
    delegate = ScriptedMerchantExecutor()
    x402_exec = _x402TestExecutor(delegate, facilitator)

    handler = DefaultRequestHandler(
        agent_executor=x402_exec,
        task_store=InMemoryTaskStore(),
    )
    a2a_app = A2AStarletteApplication(
        agent_card=agent_card,
        http_handler=handler,
    )

    card_url = f"{rpc_path}/.well-known/agent-card.json"
    routes = a2a_app.routes(agent_card_url=card_url, rpc_url=rpc_path)
    return Starlette(routes=routes)


@pytest.fixture(scope="module")
def a2a_server():
    """Start a real A2A HTTP server in a background thread."""
    host = "127.0.0.1"
    port = A2A_TEST_PORT
    app = _build_app(host, port)

    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Wait for server to be ready
    for _ in range(50):
        try:
            with socket.create_connection((host, port), timeout=0.1):
                break
        except OSError:
            time.sleep(0.1)
    else:
        raise RuntimeError("A2A server did not start in time")

    yield {"host": host, "port": port, "rpc_url": f"http://{host}:{port}/agents/merchant_agent"}

    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture
def test_account():
    return Account.from_key(
        "0x0000000000000000000000000000000000000000000000000000000000000001"
    )


# ── Helpers ──────────────────────────────────────────────────────


def _jsonrpc(method: str, params: dict, req_id: int = 1) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}


# ── Integration tests ───────────────────────────────────────────


class TestA2AIntegration:
    """Real HTTP integration tests for the full x402 A2A payment flow."""

    @pytest.mark.asyncio
    async def test_initial_message_returns_payment_required(self, a2a_server):
        """Step 1: message/send → Task with input-required + payment metadata."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                a2a_server["rpc_url"],
                json=_jsonrpc(
                    "message/send",
                    {
                        "message": {
                            "role": "user",
                            "messageId": str(uuid.uuid4()),
                            "parts": [{"kind": "text", "text": "buy a banana"}],
                        }
                    },
                ),
            )

        assert resp.status_code == 200
        body = resp.json()
        assert "result" in body, f"Expected result, got: {body}"

        task = body["result"]
        assert task["status"]["state"] == "input-required"

        # Payment metadata should be present on the status message
        msg_meta = task["status"]["message"]["metadata"]
        assert msg_meta["x402.payment.status"] == "payment-required"
        assert "x402.payment.required" in msg_meta

        # Verify payment requirements structure
        required = msg_meta["x402.payment.required"]
        assert "accepts" in required
        accepts = required["accepts"]
        assert len(accepts) >= 1
        assert accepts[0]["scheme"] == "exact"

    @pytest.mark.asyncio
    async def test_full_payment_flow_over_http(self, a2a_server, test_account):
        """Full 3-step flow: request → sign → submit payment, all over real HTTP."""
        rpc_url = a2a_server["rpc_url"]
        context_id = str(uuid.uuid4())

        async with httpx.AsyncClient() as client:
            # ── Step 1: Send initial purchase request ──
            resp1 = await client.post(
                rpc_url,
                json=_jsonrpc(
                    "message/send",
                    {
                        "message": {
                            "role": "user",
                            "messageId": str(uuid.uuid4()),
                            "contextId": context_id,
                            "parts": [{"kind": "text", "text": "buy a banana"}],
                        }
                    },
                    req_id=1,
                ),
            )
            assert resp1.status_code == 200
            body1 = resp1.json()
            assert "result" in body1, f"Step 1 failed: {body1}"

            task1 = body1["result"]
            assert task1["status"]["state"] == "input-required"
            task_id = task1["id"]

            # Extract payment requirements from metadata
            msg_meta = task1["status"]["message"]["metadata"]
            required_data = msg_meta["x402.payment.required"]
            accepts = required_data["accepts"]
            assert len(accepts) >= 1

            # ── Step 2: Sign payment requirements ──
            req_dict = accepts[0]
            requirements = PaymentRequirements.model_validate(req_dict)
            payload = process_payment(requirements, test_account)

            assert payload.x402_version == 2
            assert payload.payload["signature"].startswith("0x")

            # ── Step 3: Submit payment via message/send ──
            payload_dict = payload.model_dump(mode="json", by_alias=True)

            resp2 = await client.post(
                rpc_url,
                json=_jsonrpc(
                    "message/send",
                    {
                        "message": {
                            "role": "user",
                            "messageId": str(uuid.uuid4()),
                            "contextId": context_id,
                            "taskId": task_id,
                            "parts": [{"kind": "text", "text": "payment submitted"}],
                            "metadata": {
                                "x402.payment.status": "payment-submitted",
                                "x402.payment.payload": payload_dict,
                            },
                        }
                    },
                    req_id=2,
                ),
            )
            assert resp2.status_code == 200
            body2 = resp2.json()
            assert "result" in body2, f"Step 3 failed: {body2}"

            task2 = body2["result"]
            assert task2["id"] == task_id

            # Task should be completed
            assert task2["status"]["state"] == "completed"

            # The x402 middleware set x402_payment_verified during verify→execute→settle
            task_meta = task2.get("metadata", {})
            assert task_meta.get("x402_payment_verified") is True, (
                f"Expected x402_payment_verified in metadata: {task_meta}"
            )

    @pytest.mark.asyncio
    async def test_agent_card_endpoint(self, a2a_server):
        """The .well-known/agent-card.json endpoint should return a valid card."""
        card_url = (
            f"http://{a2a_server['host']}:{a2a_server['port']}"
            "/agents/merchant_agent/.well-known/agent-card.json"
        )
        async with httpx.AsyncClient() as client:
            resp = await client.get(card_url)

        assert resp.status_code == 200
        card = resp.json()
        assert card["name"] == "Test Merchant"
        assert card["version"] == "1.0.0"

    @pytest.mark.asyncio
    async def test_payment_artifacts_present(self, a2a_server, test_account):
        """After successful payment, the task should contain merchant artifacts."""
        rpc_url = a2a_server["rpc_url"]
        context_id = str(uuid.uuid4())

        async with httpx.AsyncClient() as client:
            # Step 1: Initial request
            resp1 = await client.post(
                rpc_url,
                json=_jsonrpc(
                    "message/send",
                    {
                        "message": {
                            "role": "user",
                            "messageId": str(uuid.uuid4()),
                            "contextId": context_id,
                            "parts": [{"kind": "text", "text": "buy a banana"}],
                        }
                    },
                ),
            )
            task1 = resp1.json()["result"]
            task_id = task1["id"]

            # Step 2: Sign and submit
            accepts = task1["status"]["message"]["metadata"]["x402.payment.required"]["accepts"]
            requirements = PaymentRequirements.model_validate(accepts[0])
            payload = process_payment(requirements, test_account)
            payload_dict = payload.model_dump(mode="json", by_alias=True)

            resp2 = await client.post(
                rpc_url,
                json=_jsonrpc(
                    "message/send",
                    {
                        "message": {
                            "role": "user",
                            "messageId": str(uuid.uuid4()),
                            "contextId": context_id,
                            "taskId": task_id,
                            "parts": [{"kind": "text", "text": "payment submitted"}],
                            "metadata": {
                                "x402.payment.status": "payment-submitted",
                                "x402.payment.payload": payload_dict,
                            },
                        }
                    },
                ),
            )
            task2 = resp2.json()["result"]
            assert task2["status"]["state"] == "completed"

            # Verify artifacts contain the order confirmation
            artifacts = task2.get("artifacts", [])
            assert len(artifacts) > 0, "Expected at least one artifact"

            # Find text content in artifacts
            artifact_texts = []
            for artifact in artifacts:
                for part in artifact.get("parts", []):
                    if part.get("kind") == "text":
                        artifact_texts.append(part["text"])

            assert any("banana" in t.lower() for t in artifact_texts), (
                f"Expected 'banana' in artifact texts: {artifact_texts}"
            )
