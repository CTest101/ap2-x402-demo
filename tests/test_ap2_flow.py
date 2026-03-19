"""
AP2 Embedded Flow unit tests — pure Python, no HTTP server, no LLM.

Tests the complete AP2 Mandate chain with x402 embedded:
  IntentMandate → CartMandate (with x402 requirements) → PaymentMandate (with x402 payload)
"""

import json
import threading
import time

import httpx
import pytest
from flask import Flask

from eth_account import Account

from ap2.types.mandate import (
    CartMandate,
    IntentMandate,
    PaymentMandate,
    PaymentMandateContents,
    CART_MANDATE_DATA_KEY,
)
from ap2.types.payment_request import PaymentRequest, PaymentResponse

from ap2_flow.types import (
    X402_METHOD,
    AP2_EXTENSION_URI,
    create_x402_payment_required,
    create_payment_request_with_x402,
    extract_x402_from_payment_request,
)
from ap2_flow.merchant import (
    create_cart_mandate,
    extract_payment_from_mandate,
    verify_and_settle_mandate,
)
from ap2_flow.client import (
    create_intent_mandate,
    create_payment_mandate,
    sign_mandate,
)

from merchant.facilitator import MockFacilitator
from shared.constants import NETWORK, USDC_ADDRESS, X402_VERSION
from x402_a2a.core.wallet import process_payment
from x402_a2a.types import PaymentRequirements


MERCHANT_WALLET = "0xAb5801a7D398351b8bE11C439e05C5B3259aeC9B"
TEST_PRODUCT = "sneakers"
TEST_PRICE = "100000"


@pytest.fixture
def test_account():
    return Account.from_key(
        "0x0000000000000000000000000000000000000000000000000000000000000001"
    )


# ── IntentMandate ──────────────────────────────────────────────


class TestIntentMandate:
    def test_create_intent_mandate(self):
        """Create an IntentMandate and verify its structure."""
        mandate = create_intent_mandate(
            description="I want to buy sneakers",
            merchants=["merchant_agent"],
            skus=["sneakers_sku"],
        )

        assert mandate.natural_language_description == "I want to buy sneakers"
        assert mandate.merchants == ["merchant_agent"]
        assert mandate.skus == ["sneakers_sku"]
        assert mandate.requires_refundability is False
        assert mandate.intent_expiry is not None

        # Should be serializable
        data = mandate.model_dump(by_alias=True)
        assert "natural_language_description" in data or "naturalLanguageDescription" in data


# ── CartMandate with x402 ─────────────────────────────────────


class TestCartMandate:
    def test_create_cart_mandate_with_x402(self):
        """CartMandate should contain x402 PaymentRequirements in method_data."""
        cart = create_cart_mandate(
            product_name=TEST_PRODUCT,
            price=TEST_PRICE,
            wallet_address=MERCHANT_WALLET,
            merchant_signature="0xmerchant_sig",
        )

        assert isinstance(cart, CartMandate)
        assert cart.contents is not None
        assert cart.merchant_authorization == "0xmerchant_sig"

        # Verify payment_request contains x402 method_data
        cart_dict = cart.model_dump(by_alias=True)
        contents = cart_dict["contents"]
        payment_request = contents["payment_request"]
        method_data = payment_request["method_data"]

        assert len(method_data) >= 1
        x402_method = method_data[0]
        assert x402_method["supported_methods"] == X402_METHOD

        # Verify x402 data structure
        x402_data = x402_method["data"]
        assert "accepts" in x402_data
        assert x402_data["x402Version"] == X402_VERSION

        accepts = x402_data["accepts"]
        assert len(accepts) >= 1
        req = accepts[0]
        assert req["scheme"] == "exact"
        assert req["network"] == NETWORK
        assert req["asset"] == USDC_ADDRESS
        assert req["payTo"] == MERCHANT_WALLET
        assert req["amount"] == TEST_PRICE

    def test_extract_x402_from_cart(self):
        """Should be able to extract x402 requirements from CartMandate."""
        cart = create_cart_mandate(
            product_name=TEST_PRODUCT,
            price=TEST_PRICE,
            wallet_address=MERCHANT_WALLET,
        )
        cart_dict = cart.model_dump(by_alias=True)
        payment_request = cart_dict["contents"]["payment_request"]

        x402_data = extract_x402_from_payment_request(payment_request)
        assert x402_data is not None
        assert "accepts" in x402_data
        assert x402_data["accepts"][0]["payTo"] == MERCHANT_WALLET


# ── PaymentMandate with x402 ──────────────────────────────────


class TestPaymentMandate:
    def test_create_payment_mandate_with_x402_payload(self, test_account):
        """PaymentMandate should contain x402 PaymentPayload in payment_response.details."""
        # Step 1: Create cart
        cart = create_cart_mandate(
            product_name=TEST_PRODUCT,
            price=TEST_PRICE,
            wallet_address=MERCHANT_WALLET,
        )
        cart_dict = cart.model_dump(by_alias=True)

        # Step 2: Extract requirements and sign
        payment_request = cart_dict["contents"]["payment_request"]
        x402_data = extract_x402_from_payment_request(payment_request)
        req_dict = x402_data["accepts"][0]
        requirements = PaymentRequirements.model_validate(req_dict)
        signed_payload = process_payment(requirements, test_account)
        payload_dict = signed_payload.model_dump(mode="json", by_alias=True)

        # Step 3: Create PaymentMandate
        pm = create_payment_mandate(
            cart_mandate_dict=cart_dict,
            signed_payload=payload_dict,
            merchant_agent="merchant_agent",
        )

        assert isinstance(pm, PaymentMandate)

        # Verify x402 payload is embedded in payment_response.details
        pm_dict = pm.model_dump(by_alias=True)
        contents = pm_dict["payment_mandate_contents"]
        payment_response = contents["payment_response"]

        assert payment_response["method_name"] == X402_METHOD
        details = payment_response["details"]
        assert details is not None
        assert "payload" in details
        assert "signature" in details["payload"]
        assert details["payload"]["signature"].startswith("0x")

    def test_extract_payment_from_mandate(self, test_account):
        """extract_payment_from_mandate should retrieve x402 payload."""
        cart = create_cart_mandate(
            product_name=TEST_PRODUCT,
            price=TEST_PRICE,
            wallet_address=MERCHANT_WALLET,
        )
        cart_dict = cart.model_dump(by_alias=True)

        # Sign payment
        x402_data = extract_x402_from_payment_request(
            cart_dict["contents"]["payment_request"]
        )
        req_dict = x402_data["accepts"][0]
        requirements = PaymentRequirements.model_validate(req_dict)
        signed_payload = process_payment(requirements, test_account)
        payload_dict = signed_payload.model_dump(mode="json", by_alias=True)

        pm = create_payment_mandate(cart_dict, payload_dict)
        pm_dict = pm.model_dump(by_alias=True)

        extracted = extract_payment_from_mandate(pm_dict)
        assert extracted is not None
        assert "payload" in extracted
        assert extracted["payload"]["signature"].startswith("0x")


# ── Full AP2 Flow ──────────────────────────────────────────────


class TestFullAP2Flow:
    @pytest.mark.asyncio
    async def test_full_ap2_flow(self, test_account):
        """Full chain: IntentMandate → CartMandate → sign x402 → PaymentMandate → verify + settle."""
        # 1. User creates IntentMandate
        intent = create_intent_mandate(
            description="I want to buy sneakers",
            merchants=["merchant_agent"],
        )
        assert intent.natural_language_description == "I want to buy sneakers"

        # 2. Merchant creates CartMandate with x402 requirements
        cart = create_cart_mandate(
            product_name=TEST_PRODUCT,
            price=TEST_PRICE,
            wallet_address=MERCHANT_WALLET,
            merchant_signature="0xmerchant_sig_123",
        )
        cart_dict = cart.model_dump(by_alias=True)

        # 3. Client extracts x402 requirements from cart
        payment_request = cart_dict["contents"]["payment_request"]
        x402_data = extract_x402_from_payment_request(payment_request)
        assert x402_data is not None
        req_dict = x402_data["accepts"][0]

        # 4. Client signs x402 payment
        requirements = PaymentRequirements.model_validate(req_dict)
        signed_payload = process_payment(requirements, test_account)
        assert signed_payload.x402_version == 2
        payload_dict = signed_payload.model_dump(mode="json", by_alias=True)

        # 5. Client creates PaymentMandate with embedded x402 payload
        pm = create_payment_mandate(
            cart_mandate_dict=cart_dict,
            signed_payload=payload_dict,
            merchant_agent="merchant_agent",
        )
        pm_dict = pm.model_dump(by_alias=True)

        # 6. Merchant extracts and verifies x402 payment
        extracted = extract_payment_from_mandate(pm_dict)
        assert extracted is not None
        assert extracted["payload"]["signature"].startswith("0x")

        # 7. Verify and settle via facilitator
        facilitator = MockFacilitator(is_valid=True, is_settled=True)
        result = await verify_and_settle_mandate(pm_dict, facilitator)
        assert result["success"] is True
        assert result["transaction"] is not None


# ── Mandate Signing ────────────────────────────────────────────


class TestMandateSigning:
    @pytest.fixture(scope="class")
    def wallet_server(self):
        """Start wallet Flask app in a background thread for mandate signing tests."""
        from wallet.server import app

        # Use a different port to avoid conflicts
        port = 15001
        server_thread = threading.Thread(
            target=lambda: app.run(host="127.0.0.1", port=port, use_reloader=False),
            daemon=True,
        )
        server_thread.start()
        time.sleep(0.5)
        yield f"http://127.0.0.1:{port}"

    @pytest.mark.asyncio
    async def test_mandate_signing(self, wallet_server):
        """Sign mandates via the wallet service /sign-mandate endpoint."""
        intent = create_intent_mandate(description="buy shoes")
        mandate_dict = intent.model_dump(by_alias=True)

        result = await sign_mandate(mandate_dict, wallet_server)

        assert "signature" in result
        assert result["signature"].startswith("0x")
        assert "address" in result
        assert result["address"].startswith("0x")

    @pytest.mark.asyncio
    async def test_sign_cart_mandate(self, wallet_server):
        """Sign a CartMandate via the wallet service."""
        cart = create_cart_mandate(
            product_name="shoes",
            price="50000",
            wallet_address=MERCHANT_WALLET,
        )
        cart_dict = cart.model_dump(by_alias=True)

        result = await sign_mandate(cart_dict, wallet_server)

        assert "signature" in result
        assert result["signature"].startswith("0x")
