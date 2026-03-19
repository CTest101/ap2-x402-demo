"""AP2 helper types and constants for x402 Embedded Flow."""

import datetime
import uuid

from ap2.types.mandate import (
    CartContents,
    CartMandate,
    IntentMandate,
    PaymentMandate,
    PaymentMandateContents,
    CART_MANDATE_DATA_KEY,
)
from ap2.types.payment_request import (
    PaymentCurrencyAmount,
    PaymentDetailsInit,
    PaymentItem,
    PaymentRequest,
    PaymentResponse,
)

# x402 method identifier used in AP2 PaymentRequest.method_data
X402_METHOD = "https://www.x402.org/"

# AP2 extension URI (same as x402 method for now)
AP2_EXTENSION_URI = "https://www.x402.org/"


def create_x402_payment_required(requirements_dict: dict) -> dict:
    """Wrap x402 PaymentRequirements into the structure expected inside CartMandate.

    Returns the x402PaymentRequiredResponse dict that goes into
    PaymentRequest.method_data[].data.
    """
    accepts = requirements_dict.get("accepts")
    if accepts is None:
        # Single requirement — wrap in accepts array
        accepts = [requirements_dict]

    return {
        "x402Version": requirements_dict.get("x402Version", 1),
        "accepts": accepts,
    }


def create_payment_request_with_x402(
    x402_payment_required: dict,
    details: dict | None = None,
) -> PaymentRequest:
    """Create an AP2 PaymentRequest with x402 as the payment method."""
    if details is None:
        # Build details from accepts
        accepts = x402_payment_required.get("accepts", [])
        total_str = accepts[0].get("maxAmountRequired", "0") if accepts else "0"
        total_amount = PaymentCurrencyAmount(currency="USDC", value=float(total_str))
        total_item = PaymentItem(label="Total", amount=total_amount)
        details = PaymentDetailsInit(
            id=str(uuid.uuid4()),
            display_items=[total_item],
            total=total_item,
        )

    return PaymentRequest(
        method_data=[
            {
                "supported_methods": X402_METHOD,
                "data": x402_payment_required,
            }
        ],
        details=details,
    )


def extract_x402_from_payment_request(payment_request: dict) -> dict | None:
    """Extract x402 PaymentRequirements from an AP2 PaymentRequest dict."""
    method_data = payment_request.get("method_data", [])
    for method in method_data:
        if method.get("supported_methods") == X402_METHOD:
            return method.get("data")
    return None
