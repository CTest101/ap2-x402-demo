"""AP2 Merchant logic — creates CartMandates with embedded x402 and processes payments."""

import datetime
import json
import logging
import uuid

from ap2.types.mandate import (
    CartContents,
    CartMandate,
    PaymentMandate,
    PaymentMandateContents,
    CART_MANDATE_DATA_KEY,
)
from ap2.types.payment_request import PaymentRequest, PaymentResponse

from x402_a2a.types import (
    PaymentPayload,
    PaymentRequirements,
    VerifyResponse,
    SettleResponse,
)
from x402_a2a import FacilitatorClient

from shared.constants import NETWORK, USDC_ADDRESS, X402_VERSION, PAYMENT_TIMEOUT_SECONDS
from .types import (
    X402_METHOD,
    create_x402_payment_required,
    create_payment_request_with_x402,
    extract_x402_from_payment_request,
)

logger = logging.getLogger(__name__)


def create_cart_mandate(
    product_name: str,
    price: str,
    wallet_address: str,
    merchant_signature: str | None = None,
) -> CartMandate:
    """Create a CartMandate with x402 PaymentRequirements embedded in method_data.

    Args:
        product_name: Name of the product being sold.
        price: Price in smallest unit (e.g. "50000" for 0.05 USDC).
        wallet_address: Merchant's wallet address to receive payment.
        merchant_signature: Optional merchant signature for the cart.

    Returns:
        CartMandate with x402 requirements embedded.
    """
    # Build x402 PaymentRequirements (using aliased field names for serialization)
    requirements = {
        "scheme": "exact",
        "network": NETWORK,
        "asset": USDC_ADDRESS,
        "payTo": wallet_address,
        "amount": price,
        "maxTimeoutSeconds": PAYMENT_TIMEOUT_SECONDS,
        "extra": {
            "name": "USDC",
            "version": "2",
            "product": {
                "sku": f"{product_name}_sku",
                "name": product_name,
                "version": "1",
            },
        },
    }

    # Wrap in x402PaymentRequiredResponse
    x402_payment_required = {
        "x402Version": X402_VERSION,
        "accepts": [requirements],
    }

    # Create AP2 PaymentRequest with x402 method_data
    payment_request = create_payment_request_with_x402(x402_payment_required)

    # Build CartContents
    cart_contents = CartContents(
        id=str(uuid.uuid4()),
        user_cart_confirmation_required=True,
        payment_request=payment_request,
        cart_expiry=(
            datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(hours=1)
        ).isoformat(),
        merchant_name="AP2 Merchant",
    )

    return CartMandate(
        contents=cart_contents,
        merchant_authorization=merchant_signature or "unsigned",
    )


def extract_payment_from_mandate(payment_mandate: dict) -> dict | None:
    """Extract x402 PaymentPayload from a PaymentMandate dict.

    The payload lives at:
    payment_mandate.payment_mandate_contents.payment_response.details
    """
    contents = payment_mandate.get("payment_mandate_contents", {})
    payment_response = contents.get("payment_response", {})
    details = payment_response.get("details")
    return details


async def verify_and_settle_mandate(
    payment_mandate: dict,
    facilitator: FacilitatorClient,
) -> dict:
    """Verify and settle the x402 payment embedded in a PaymentMandate.

    Returns a dict with verify/settle results.
    """
    # Extract x402 payload from mandate
    payload_dict = extract_payment_from_mandate(payment_mandate)
    if not payload_dict:
        return {"success": False, "error": "No x402 payload found in PaymentMandate"}

    # Build PaymentPayload from dict
    payload = PaymentPayload.model_validate(payload_dict)

    # Build requirements from the accepted field if present, or use defaults
    accepted = payload_dict.get("accepted", {})
    requirements = PaymentRequirements(
        scheme=accepted.get("scheme", "exact"),
        network=accepted.get("network", NETWORK),
        asset=accepted.get("asset", USDC_ADDRESS),
        pay_to=accepted.get("payTo", ""),
        amount=accepted.get("amount", accepted.get("maxAmountRequired", "0")),
        max_timeout_seconds=accepted.get("maxTimeoutSeconds", PAYMENT_TIMEOUT_SECONDS),
    )

    # Verify
    verify_result = await facilitator.verify(payload, requirements)
    if not verify_result.is_valid:
        return {
            "success": False,
            "error": f"Verification failed: {verify_result.invalid_reason}",
        }

    # Settle
    settle_result = await facilitator.settle(payload, requirements)
    return {
        "success": settle_result.success,
        "transaction": settle_result.transaction if settle_result.success else None,
        "error": settle_result.error_reason if not settle_result.success else None,
    }
