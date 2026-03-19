"""
E2E test — Full x402 payment flow.
Tests the complete cycle: merchant requires payment → client signs → merchant verifies+settles.
Uses MockFacilitator, no real blockchain or LLM needed.
"""

import asyncio
import json
import subprocess
import sys
import time
import uuid

import httpx
import pytest

from wallet.server import app as wallet_app, _sign_transfer_authorization
from merchant.agent import MerchantAgent
from merchant.facilitator import MockFacilitator
from merchant.x402_executor import x402MerchantExecutor

from x402_a2a.types import (
    PaymentRequirements,
    PaymentPayload,
    x402PaymentRequiredResponse,
    PaymentStatus,
    x402PaymentRequiredException,
)
from x402_a2a.core.utils import x402Utils
from x402_a2a.core.wallet import process_payment

from shared.constants import NETWORK, USDC_ADDRESS, X402_VERSION

from eth_account import Account


# ── Test fixtures ──────────────────────────────────────────────


@pytest.fixture
def wallet_client():
    """Flask test client for wallet service."""
    wallet_app.config["TESTING"] = True
    client = wallet_app.test_client()
    yield client


@pytest.fixture
def merchant():
    """MerchantAgent instance."""
    return MerchantAgent(wallet_address="0xAb5801a7D398351b8bE11C439e05C5B3259aeC9B")


@pytest.fixture
def mock_facilitator():
    """MockFacilitator for testing."""
    return MockFacilitator(is_valid=True, is_settled=True)


@pytest.fixture
def x402_utils():
    return x402Utils()


@pytest.fixture
def test_account():
    """Test account for signing."""
    return Account.from_key(
        "0x0000000000000000000000000000000000000000000000000000000000000001"
    )


# ── Unit tests for payment flow components ──────────────────────


class TestPaymentRequirementsCreation:
    """Test that merchant creates valid PaymentRequirements."""

    def test_merchant_raises_payment_exception(self, merchant):
        """Merchant tool raises x402PaymentRequiredException with valid requirements."""
        with pytest.raises(x402PaymentRequiredException) as exc_info:
            merchant.get_product_and_request_payment("laptop")

        exc = exc_info.value
        accepts = exc.get_accepts_array()
        assert len(accepts) == 1

        req = accepts[0]
        assert req.scheme == "exact"
        assert req.network == NETWORK
        assert req.asset == USDC_ADDRESS
        assert req.pay_to == merchant._wallet_address
        assert int(req.amount) > 0
        assert req.extra["name"] == "USDC"
        assert req.extra["product"]["name"] == "laptop"

    def test_deterministic_price(self, merchant):
        """Same product always gets same price."""
        with pytest.raises(x402PaymentRequiredException) as exc1:
            merchant.get_product_and_request_payment("laptop")
        with pytest.raises(x402PaymentRequiredException) as exc2:
            merchant.get_product_and_request_payment("laptop")

        price1 = exc1.value.get_accepts_array()[0].amount
        price2 = exc2.value.get_accepts_array()[0].amount
        assert price1 == price2

    def test_empty_product_returns_error(self, merchant):
        """Empty product name returns error dict instead of raising."""
        result = merchant.get_product_and_request_payment("")
        assert "error" in result


class TestWalletSigning:
    """Test wallet signing produces valid v2 PaymentPayload."""

    def test_wallet_service_signs_requirements(self, wallet_client):
        """Wallet service returns valid v2 payload for payment requirements."""
        requirements = {
            "scheme": "exact",
            "network": NETWORK,
            "asset": USDC_ADDRESS,
            "payTo": "0xAb5801a7D398351b8bE11C439e05C5B3259aeC9B",
            "amount": "50000",
            "maxTimeoutSeconds": 1200,
            "extra": {"name": "USDC", "version": "2"},
        }

        response = wallet_client.post(
            "/sign",
            data=json.dumps(requirements),
            content_type="application/json",
        )
        assert response.status_code == 200

        data = response.get_json()
        assert data["x402Version"] == X402_VERSION
        assert data["scheme"] == "exact"
        assert data["network"] == NETWORK
        assert data["payload"]["signature"].startswith("0x")
        assert data["payload"]["authorization"]["to"] == requirements["payTo"]
        assert data["payload"]["authorization"]["value"] == requirements["amount"]

    def test_local_signing_produces_valid_payload(self, test_account):
        """Local signing via process_payment produces valid PaymentPayload."""
        requirements = PaymentRequirements(
            scheme="exact",
            network=NETWORK,
            asset=USDC_ADDRESS,
            pay_to="0xAb5801a7D398351b8bE11C439e05C5B3259aeC9B",
            amount="100000",
            max_timeout_seconds=1200,
            extra={"name": "USDC", "version": "2"},
        )

        payload = process_payment(requirements, test_account)

        assert payload.x402_version == 2
        assert isinstance(payload.payload, dict)
        assert payload.payload["signature"].startswith("0x")
        assert payload.accepted.scheme == "exact"
        assert payload.accepted.network == NETWORK


class TestFacilitator:
    """Test MockFacilitator verify and settle."""

    @pytest.mark.asyncio
    async def test_mock_verify_valid(self, mock_facilitator):
        """MockFacilitator verifies payments as valid."""
        requirements = PaymentRequirements(
            scheme="exact",
            network=NETWORK,
            asset=USDC_ADDRESS,
            pay_to="0xAb5801a7D398351b8bE11C439e05C5B3259aeC9B",
            amount="50000",
            max_timeout_seconds=600,
        )
        payload = PaymentPayload(
            x402_version=2,
            payload={
                "signature": "0xdeadbeef",
                "authorization": {
                    "from": "0x7E5F4552091A69125d5DfCb7b8C2659029395Bdf",
                    "to": "0xAb5801a7D398351b8bE11C439e05C5B3259aeC9B",
                    "value": "50000",
                    "validAfter": "0",
                    "validBefore": "9999999999",
                    "nonce": "0x" + "00" * 32,
                },
            },
            accepted=requirements,
        )

        result = await mock_facilitator.verify(payload, requirements)
        assert result.is_valid is True
        assert result.payer == "0x7E5F4552091A69125d5DfCb7b8C2659029395Bdf"

    @pytest.mark.asyncio
    async def test_mock_settle_success(self, mock_facilitator):
        """MockFacilitator settles payments successfully."""
        requirements = PaymentRequirements(
            scheme="exact",
            network=NETWORK,
            asset=USDC_ADDRESS,
            pay_to="0xAb5801a7D398351b8bE11C439e05C5B3259aeC9B",
            amount="50000",
            max_timeout_seconds=600,
        )
        payload = PaymentPayload(
            x402_version=2,
            payload={"signature": "0xdeadbeef", "authorization": {}},
            accepted=requirements,
        )

        result = await mock_facilitator.settle(payload, requirements)
        assert result.success is True
        assert result.network == NETWORK
        assert result.transaction == "0xmock_tx_hash"

    @pytest.mark.asyncio
    async def test_mock_verify_invalid(self):
        """MockFacilitator can reject payments."""
        facilitator = MockFacilitator(is_valid=False)
        requirements = PaymentRequirements(
            scheme="exact",
            network=NETWORK,
            asset=USDC_ADDRESS,
            pay_to="0x0000000000000000000000000000000000000001",
            amount="1000",
            max_timeout_seconds=600,
        )
        payload = PaymentPayload(
            x402_version=2,
            payload={"signature": "0x00", "authorization": {}},
            accepted=requirements,
        )

        result = await facilitator.verify(payload, requirements)
        assert result.is_valid is False


class TestX402MetadataFlow:
    """Test x402 metadata creation and extraction."""

    def test_create_payment_required_task(self, x402_utils):
        """x402Utils creates valid payment-required task with metadata."""
        from a2a.types import Task, TaskStatus, TaskState

        task = Task(
            id="test-task-1",
            context_id="ctx-1",
            status=TaskStatus(state=TaskState.working),
        )

        requirements = PaymentRequirements(
            scheme="exact",
            network=NETWORK,
            asset=USDC_ADDRESS,
            pay_to="0xAb5801a7D398351b8bE11C439e05C5B3259aeC9B",
            amount="50000",
            max_timeout_seconds=600,
            extra={"name": "USDC"},
        )
        payment_required = x402PaymentRequiredResponse(
            x402_version=2,
            accepts=[requirements],
        )

        task = x402_utils.create_payment_required_task(task, payment_required)

        assert task.status.state == TaskState.input_required
        assert task.status.message is not None
        assert task.status.message.metadata is not None

        # Extract requirements back from task
        extracted = x402_utils.get_payment_requirements(task)
        assert extracted is not None
        assert len(extracted.accepts) == 1
        assert extracted.accepts[0].amount == "50000"

    def test_record_payment_success(self, x402_utils):
        """x402Utils records payment success with settlement receipt."""
        from a2a.types import Task, TaskStatus, TaskState
        from x402_a2a.types import SettleResponse

        task = Task(
            id="test-task-2",
            context_id="ctx-2",
            status=TaskStatus(state=TaskState.working),
        )

        settle_response = SettleResponse(
            success=True,
            network=NETWORK,
            transaction="0xmock_tx",
        )

        task = x402_utils.record_payment_success(task, settle_response)

        status = x402_utils.get_payment_status(task)
        assert status == PaymentStatus.PAYMENT_COMPLETED

        receipts = x402_utils.get_payment_receipts(task)
        assert len(receipts) == 1
        assert receipts[0].success is True


class TestE2EPaymentFlow:
    """End-to-end payment flow without LLM or real servers."""

    @pytest.mark.asyncio
    async def test_full_payment_flow(self, merchant, mock_facilitator, test_account):
        """
        Full flow:
        1. Merchant raises x402PaymentRequiredException
        2. Client signs payment
        3. Facilitator verifies
        4. Facilitator settles
        """
        # Step 1: Merchant requests payment
        with pytest.raises(x402PaymentRequiredException) as exc_info:
            merchant.get_product_and_request_payment("keyboard")

        exc = exc_info.value
        accepts = exc.get_accepts_array()
        assert len(accepts) == 1
        requirements = accepts[0]

        # Step 2: Client signs payment locally
        payload = process_payment(requirements, test_account)

        assert payload.x402_version == 2
        assert isinstance(payload.payload, dict)
        assert payload.payload["signature"].startswith("0x")
        assert payload.accepted.amount == requirements.amount

        # Step 3: Facilitator verifies payment
        verify_result = await mock_facilitator.verify(payload, requirements)
        assert verify_result.is_valid is True

        # Step 4: Facilitator settles payment
        settle_result = await mock_facilitator.settle(payload, requirements)
        assert settle_result.success is True
        assert settle_result.transaction == "0xmock_tx_hash"

    @pytest.mark.asyncio
    async def test_full_flow_with_wallet_service(self, merchant, mock_facilitator, wallet_client):
        """
        Full flow using the wallet service for signing instead of local signing.
        """
        # Step 1: Merchant requests payment
        with pytest.raises(x402PaymentRequiredException) as exc_info:
            merchant.get_product_and_request_payment("mouse")

        exc = exc_info.value
        accepts = exc.get_accepts_array()
        requirements = accepts[0]

        # Step 2: Sign via wallet service
        req_data = requirements.model_dump(by_alias=True)
        sign_response = wallet_client.post(
            "/sign",
            data=json.dumps(req_data),
            content_type="application/json",
        )
        assert sign_response.status_code == 200

        sign_data = sign_response.get_json()
        assert sign_data["x402Version"] == 2

        # Reconstruct PaymentPayload from wallet service response
        payload = PaymentPayload(
            x402_version=sign_data["x402Version"],
            payload=sign_data["payload"],
            accepted=PaymentRequirements.model_validate(sign_data["accepted"]),
        )

        # Step 3: Facilitator verifies
        verify_result = await mock_facilitator.verify(payload, requirements)
        assert verify_result.is_valid is True

        # Step 4: Facilitator settles
        settle_result = await mock_facilitator.settle(payload, requirements)
        assert settle_result.success is True

    @pytest.mark.asyncio
    async def test_metadata_roundtrip(self, merchant, test_account, x402_utils):
        """
        Test that payment metadata survives the full create→extract→verify cycle.
        """
        from a2a.types import Task, TaskStatus, TaskState

        # Step 1: Merchant raises exception
        with pytest.raises(x402PaymentRequiredException) as exc_info:
            merchant.get_product_and_request_payment("monitor")

        exc = exc_info.value
        accepts = exc.get_accepts_array()
        requirements = accepts[0]

        # Step 2: Create payment-required task (simulating x402ServerExecutor)
        task = Task(
            id=str(uuid.uuid4()),
            context_id=str(uuid.uuid4()),
            status=TaskStatus(state=TaskState.working),
        )

        payment_required = x402PaymentRequiredResponse(
            x402_version=2,
            accepts=accepts,
            error=str(exc),
        )
        task = x402_utils.create_payment_required_task(task, payment_required)

        assert task.status.state == TaskState.input_required

        # Step 3: Extract requirements from task metadata (simulating client)
        extracted = x402_utils.get_payment_requirements(task)
        assert extracted is not None
        assert len(extracted.accepts) == 1

        extracted_req = extracted.accepts[0]
        assert extracted_req.amount == requirements.amount
        assert extracted_req.pay_to == requirements.pay_to
        assert extracted_req.network == NETWORK

        # Step 4: Sign payment
        payload = process_payment(extracted_req, test_account)
        assert payload.accepted.amount == requirements.amount

        # Step 5: Record payment success
        from x402_a2a.types import SettleResponse

        settle_resp = SettleResponse(
            success=True,
            network=NETWORK,
            transaction="0xtest_tx",
        )
        task = x402_utils.record_payment_success(task, settle_resp)

        status = x402_utils.get_payment_status(task)
        assert status == PaymentStatus.PAYMENT_COMPLETED


class TestWalletServiceStandalone:
    """Verify wallet service works as a standalone Flask app."""

    def test_sign_and_verify_roundtrip(self, wallet_client):
        """Sign via wallet service, parse the result, verify structure."""
        requirements = {
            "scheme": "exact",
            "network": NETWORK,
            "asset": USDC_ADDRESS,
            "payTo": "0xAb5801a7D398351b8bE11C439e05C5B3259aeC9B",
            "amount": "1000000",
            "maxTimeoutSeconds": 1200,
            "extra": {"name": "USDC", "version": "2"},
        }

        # Sign
        sign_resp = wallet_client.post(
            "/sign",
            data=json.dumps(requirements),
            content_type="application/json",
        )
        assert sign_resp.status_code == 200
        payload = sign_resp.get_json()

        # Verify v2 structure
        assert payload["x402Version"] == 2
        assert payload["scheme"] == "exact"
        assert payload["network"] == NETWORK
        assert "payload" in payload
        assert "accepted" in payload

        # Verify payload contents
        inner = payload["payload"]
        assert "signature" in inner
        assert "authorization" in inner
        assert inner["authorization"]["to"] == requirements["payTo"]
        assert inner["authorization"]["value"] == requirements["amount"]

        # Verify can be parsed as PaymentPayload
        parsed = PaymentPayload(
            x402_version=payload["x402Version"],
            payload=inner,
            accepted=PaymentRequirements.model_validate(payload["accepted"]),
        )
        assert parsed.get_scheme() == "exact"
        assert parsed.get_network() == NETWORK

    def test_get_wallet_address(self, wallet_client):
        """Wallet address endpoint works."""
        resp = wallet_client.get("/address")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["address"].startswith("0x")
        assert len(data["address"]) == 42
