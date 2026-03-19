"""
AP2 + A2A HTTP Integration Test — Embedded Flow over real HTTP server.

Similar to test_a2a_integration.py but for the AP2 Embedded Flow:
  - CartMandate with x402 requirements in task.artifacts
  - PaymentMandate with x402 payload in message.parts
  - Flow detection: metadata has x402.payment.status but NO x402.payment.required key

Runs on port 19403 (different from standalone test on 19402).
"""

import json
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
    Artifact,
    DataPart,
    Part,
    TaskState,
    TextPart,
)

from ap2.types.mandate import CART_MANDATE_DATA_KEY

from ap2_flow.merchant import (
    create_cart_mandate,
    extract_payment_from_mandate,
)
from ap2_flow.types import X402_METHOD, extract_x402_from_payment_request
from merchant.facilitator import MockFacilitator
from shared.constants import NETWORK, USDC_ADDRESS, X402_VERSION

from x402_a2a.types import (
    PaymentPayload,
    PaymentRequirements,
)
from x402_a2a.core.wallet import process_payment

from eth_account import Account


# ── Constants ────────────────────────────────────────────────────

MERCHANT_WALLET = "0xAb5801a7D398351b8bE11C439e05C5B3259aeC9B"
PRODUCT_NAME = "sneakers"
PRODUCT_PRICE = "100000"
A2A_TEST_PORT = 19403


# ── Scripted AP2 Merchant Executor ───────────────────────────────


class ScriptedAP2MerchantExecutor(AgentExecutor):
    """Deterministic executor for AP2 Embedded Flow (no LLM).

    - On receiving any initial message (IntentMandate text):
        Returns input-required with:
        - metadata: x402.payment.status=payment-required (NO x402.payment.required)
        - artifact: CartMandate with x402 requirements embedded

    - On receiving PaymentMandate (DataPart in message.parts):
        Extracts x402 payload, verifies, settles, returns completed
    """

    def __init__(self):
        self._facilitator = MockFacilitator(is_valid=True, is_settled=True)

    async def execute(self, context: RequestContext, event_queue: EventQueue):
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)

        # Check if this is a payment submission (has DataPart with PaymentMandate)
        user_message = context.message
        payment_mandate_dict = None

        if user_message and user_message.parts:
            for part in user_message.parts:
                if hasattr(part, 'root'):
                    part = part.root
                if isinstance(part, DataPart) and isinstance(part.data, dict):
                    if "ap2.mandates.PaymentMandate" in part.data:
                        payment_mandate_dict = part.data["ap2.mandates.PaymentMandate"]
                        break

        # Also check metadata for payment-submitted status
        msg_meta = {}
        if user_message and user_message.metadata:
            msg_meta = user_message.metadata

        if payment_mandate_dict or msg_meta.get("x402.payment.status") == "payment-submitted":
            # ── Payment submission path ──
            if payment_mandate_dict:
                # Extract and verify x402 payload from PaymentMandate
                payload_dict = extract_payment_from_mandate(payment_mandate_dict)
                if payload_dict:
                    payload = PaymentPayload.model_validate(payload_dict)
                    accepted = payload_dict.get("accepted", {})
                    requirements = PaymentRequirements(
                        scheme=accepted.get("scheme", "exact"),
                        network=accepted.get("network", NETWORK),
                        asset=accepted.get("asset", USDC_ADDRESS),
                        pay_to=accepted.get("payTo", MERCHANT_WALLET),
                        amount=accepted.get("amount", accepted.get("maxAmountRequired", PRODUCT_PRICE)),
                        max_timeout_seconds=accepted.get("maxTimeoutSeconds", 1200),
                    )

                    verify_result = await self._facilitator.verify(payload, requirements)
                    settle_result = await self._facilitator.settle(payload, requirements)

                    if verify_result.is_valid and settle_result.success:
                        await updater.add_artifact(
                            [Part(root=TextPart(text=f"Order confirmed! Your {PRODUCT_NAME} is on the way."))]
                        )
                        await updater.update_status(
                            state=TaskState.completed,
                            message=updater.new_agent_message(
                                parts=[Part(root=TextPart(text="Payment completed"))],
                                metadata={
                                    "x402.payment.status": "payment-completed",
                                    "x402.payment.receipts": [
                                        {
                                            "network": NETWORK,
                                            "transaction": settle_result.transaction,
                                        }
                                    ],
                                },
                            ),
                        )
                        return

            # Fallback: payment failed or no mandate found
            await updater.update_status(
                state=TaskState.failed,
                message=updater.new_agent_message(
                    parts=[Part(root=TextPart(text="Payment processing failed"))],
                ),
            )
            return

        # ── Initial request path — return CartMandate as artifact ──
        cart = create_cart_mandate(
            product_name=PRODUCT_NAME,
            price=PRODUCT_PRICE,
            wallet_address=MERCHANT_WALLET,
            merchant_signature="0xtest_merchant_sig",
        )
        cart_dict = cart.model_dump(by_alias=True)

        # Add CartMandate as artifact (AP2 data in DataPart)
        cart_artifact_data = {CART_MANDATE_DATA_KEY: cart_dict}
        await updater.add_artifact(
            [Part(root=DataPart(data=cart_artifact_data))],
            name="AP2 CartMandate",
        )

        # Set status to input-required with ONLY x402.payment.status (Embedded Flow)
        # Note: NO x402.payment.required key — that's how clients detect Embedded Flow
        await updater.update_status(
            state=TaskState.input_required,
            message=updater.new_agent_message(
                parts=[Part(root=TextPart(text=f"Payment required for {PRODUCT_NAME}: {PRODUCT_PRICE} USDC units"))],
                metadata={
                    "x402.payment.status": "payment-required",
                    # NO x402.payment.required here — it's in the CartMandate artifact
                },
            ),
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue):
        pass


# ── Server fixture ───────────────────────────────────────────────


def _build_ap2_app(host: str, port: int) -> Starlette:
    """Build a real A2A Starlette app with the AP2 executor."""
    base_url = f"http://{host}:{port}"
    rpc_path = "/agents/ap2_merchant"
    url = f"{base_url}{rpc_path}"

    agent_card = AgentCard(
        name="AP2 Test Merchant",
        description="Scripted AP2 merchant for integration tests",
        url=url,
        version="1.0.0",
        defaultInputModes=["text"],
        defaultOutputModes=["text"],
        capabilities=AgentCapabilities(streaming=False),
        skills=[
            AgentSkill(
                id="buy_product_ap2",
                name="Buy Product (AP2)",
                description="Purchase products via AP2 Embedded Flow with x402 payment.",
                tags=["x402", "ap2"],
                examples=["buy sneakers"],
            )
        ],
    )

    executor = ScriptedAP2MerchantExecutor()
    handler = DefaultRequestHandler(
        agent_executor=executor,
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
def ap2_a2a_server():
    """Start a real A2A HTTP server for AP2 Embedded Flow tests."""
    host = "127.0.0.1"
    port = A2A_TEST_PORT
    app = _build_ap2_app(host, port)

    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    for _ in range(50):
        try:
            with socket.create_connection((host, port), timeout=0.1):
                break
        except OSError:
            time.sleep(0.1)
    else:
        raise RuntimeError("AP2 A2A server did not start in time")

    yield {"host": host, "port": port, "rpc_url": f"http://{host}:{port}/agents/ap2_merchant"}

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


class TestAP2A2AIntegration:
    """Real HTTP integration tests for AP2 Embedded Flow."""

    @pytest.mark.asyncio
    async def test_initial_message_returns_cart_mandate_artifact(self, ap2_a2a_server):
        """Step 1: message/send → Task input-required with CartMandate artifact (Embedded Flow)."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                ap2_a2a_server["rpc_url"],
                json=_jsonrpc(
                    "message/send",
                    {
                        "message": {
                            "role": "user",
                            "messageId": str(uuid.uuid4()),
                            "parts": [{"kind": "text", "text": "buy sneakers"}],
                        }
                    },
                ),
            )

        assert resp.status_code == 200
        body = resp.json()
        assert "result" in body, f"Expected result, got: {body}"

        task = body["result"]
        assert task["status"]["state"] == "input-required"

        # Embedded Flow detection: metadata has status but NO x402.payment.required
        msg_meta = task["status"]["message"]["metadata"]
        assert msg_meta["x402.payment.status"] == "payment-required"
        assert "x402.payment.required" not in msg_meta, (
            "Embedded Flow should NOT have x402.payment.required in metadata"
        )

        # CartMandate should be in artifacts
        artifacts = task.get("artifacts", [])
        assert len(artifacts) > 0, "Expected at least one artifact with CartMandate"

        # Find CartMandate in artifacts
        cart_mandate_data = None
        for artifact in artifacts:
            for part in artifact.get("parts", []):
                if part.get("kind") == "data" and CART_MANDATE_DATA_KEY in part.get("data", {}):
                    cart_mandate_data = part["data"][CART_MANDATE_DATA_KEY]
                    break

        assert cart_mandate_data is not None, "CartMandate not found in artifacts"

        # Verify x402 requirements are embedded in the CartMandate
        payment_request = cart_mandate_data["contents"]["payment_request"]
        x402_data = extract_x402_from_payment_request(payment_request)
        assert x402_data is not None
        assert "accepts" in x402_data
        assert x402_data["accepts"][0]["payTo"] == MERCHANT_WALLET

    @pytest.mark.asyncio
    async def test_embedded_flow_detection(self, ap2_a2a_server):
        """Verify flow detection logic: status present + no requirements = Embedded Flow."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                ap2_a2a_server["rpc_url"],
                json=_jsonrpc(
                    "message/send",
                    {
                        "message": {
                            "role": "user",
                            "messageId": str(uuid.uuid4()),
                            "parts": [{"kind": "text", "text": "I want sneakers"}],
                        }
                    },
                ),
            )

        task = resp.json()["result"]
        meta = task["status"]["message"]["metadata"]

        # Flow detection per spec section 4
        has_status = "x402.payment.status" in meta
        has_requirements = "x402.payment.required" in meta

        assert has_status is True
        assert has_requirements is False
        # This means: Embedded Flow → look in artifacts for CartMandate

    @pytest.mark.asyncio
    async def test_full_ap2_payment_flow_over_http(self, ap2_a2a_server, test_account):
        """Full AP2 Embedded Flow over real HTTP:
        1. Send intent → get CartMandate artifact
        2. Extract x402 requirements from CartMandate
        3. Sign x402 payment
        4. Send PaymentMandate in message.parts → get completed task
        """
        rpc_url = ap2_a2a_server["rpc_url"]
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
                            "parts": [{"kind": "text", "text": "buy sneakers"}],
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

            # ── Step 2: Extract x402 requirements from CartMandate artifact ──
            cart_mandate_data = None
            for artifact in task1.get("artifacts", []):
                for part in artifact.get("parts", []):
                    if part.get("kind") == "data" and CART_MANDATE_DATA_KEY in part.get("data", {}):
                        cart_mandate_data = part["data"][CART_MANDATE_DATA_KEY]
                        break

            assert cart_mandate_data is not None, "CartMandate not found"

            payment_request = cart_mandate_data["contents"]["payment_request"]
            x402_data = extract_x402_from_payment_request(payment_request)
            assert x402_data is not None

            req_dict = x402_data["accepts"][0]
            requirements = PaymentRequirements.model_validate(req_dict)

            # ── Step 3: Sign x402 payment ──
            signed_payload = process_payment(requirements, test_account)
            assert signed_payload.x402_version == 2
            payload_dict = signed_payload.model_dump(mode="json", by_alias=True)

            # ── Step 4: Build PaymentMandate and send as DataPart ──
            from ap2_flow.client import create_payment_mandate

            pm = create_payment_mandate(
                cart_mandate_dict=cart_mandate_data,
                signed_payload=payload_dict,
                merchant_agent="ap2_merchant",
            )
            pm_dict = pm.model_dump(by_alias=True)

            # Send PaymentMandate in message.parts as DataPart
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
                            "parts": [
                                {
                                    "kind": "data",
                                    "data": {
                                        "ap2.mandates.PaymentMandate": pm_dict,
                                    },
                                }
                            ],
                            "metadata": {
                                "x402.payment.status": "payment-submitted",
                            },
                        }
                    },
                    req_id=2,
                ),
            )
            assert resp2.status_code == 200
            body2 = resp2.json()
            assert "result" in body2, f"Step 4 failed: {body2}"

            task2 = body2["result"]
            assert task2["id"] == task_id
            assert task2["status"]["state"] == "completed"

            # Verify payment-completed metadata
            status_meta = task2["status"]["message"]["metadata"]
            assert status_meta["x402.payment.status"] == "payment-completed"
            assert "x402.payment.receipts" in status_meta
            receipts = status_meta["x402.payment.receipts"]
            assert len(receipts) > 0
            assert receipts[0]["network"] == NETWORK

    @pytest.mark.asyncio
    async def test_ap2_artifacts_present(self, ap2_a2a_server, test_account):
        """After successful AP2 payment, the task should contain order confirmation artifact."""
        rpc_url = ap2_a2a_server["rpc_url"]
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
                            "parts": [{"kind": "text", "text": "buy sneakers"}],
                        }
                    },
                ),
            )
            task1 = resp1.json()["result"]
            task_id = task1["id"]

            # Step 2: Extract and sign
            cart_mandate_data = None
            for artifact in task1.get("artifacts", []):
                for part in artifact.get("parts", []):
                    if part.get("kind") == "data" and CART_MANDATE_DATA_KEY in part.get("data", {}):
                        cart_mandate_data = part["data"][CART_MANDATE_DATA_KEY]
                        break

            payment_request = cart_mandate_data["contents"]["payment_request"]
            x402_data = extract_x402_from_payment_request(payment_request)
            req_dict = x402_data["accepts"][0]
            requirements = PaymentRequirements.model_validate(req_dict)
            signed_payload = process_payment(requirements, test_account)
            payload_dict = signed_payload.model_dump(mode="json", by_alias=True)

            from ap2_flow.client import create_payment_mandate
            pm = create_payment_mandate(cart_mandate_data, payload_dict)
            pm_dict = pm.model_dump(by_alias=True)

            # Step 3: Submit payment
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
                            "parts": [
                                {
                                    "kind": "data",
                                    "data": {"ap2.mandates.PaymentMandate": pm_dict},
                                }
                            ],
                            "metadata": {"x402.payment.status": "payment-submitted"},
                        }
                    },
                ),
            )
            task2 = resp2.json()["result"]
            assert task2["status"]["state"] == "completed"

            # Verify artifacts contain order confirmation
            artifacts = task2.get("artifacts", [])
            artifact_texts = []
            for artifact in artifacts:
                for part in artifact.get("parts", []):
                    if part.get("kind") == "text":
                        artifact_texts.append(part["text"])

            assert any("sneakers" in t.lower() for t in artifact_texts), (
                f"Expected 'sneakers' in artifact texts: {artifact_texts}"
            )

    @pytest.mark.asyncio
    async def test_agent_card_endpoint(self, ap2_a2a_server):
        """The agent card endpoint should return a valid card."""
        card_url = (
            f"http://{ap2_a2a_server['host']}:{ap2_a2a_server['port']}"
            "/agents/ap2_merchant/.well-known/agent-card.json"
        )
        async with httpx.AsyncClient() as client:
            resp = await client.get(card_url)

        assert resp.status_code == 200
        card = resp.json()
        assert card["name"] == "AP2 Test Merchant"
        assert "ap2" in card["skills"][0]["tags"]
