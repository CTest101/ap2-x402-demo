"""AP2 Client logic — creates IntentMandates, PaymentMandates, and signs mandates."""

import datetime
import json
import logging
import uuid

import httpx

from ap2.types.mandate import (
    IntentMandate,
    PaymentMandate,
    PaymentMandateContents,
    CART_MANDATE_DATA_KEY,
)
from ap2.types.payment_request import PaymentCurrencyAmount, PaymentItem, PaymentResponse

from .types import X402_METHOD, extract_x402_from_payment_request

logger = logging.getLogger(__name__)


def create_intent_mandate(
    description: str,
    merchants: list[str] | None = None,
    skus: list[str] | None = None,
    requires_refundability: bool = False,
) -> IntentMandate:
    """Create an AP2 IntentMandate expressing the user's purchase intent.

    Args:
        description: Natural language description of what the user wants to buy.
        merchants: Optional list of preferred merchant identifiers.
        skus: Optional list of desired SKU identifiers.
        requires_refundability: Whether the user requires a refund option.

    Returns:
        IntentMandate ready to be signed and sent to a merchant.
    """
    return IntentMandate(
        natural_language_description=description,
        merchants=merchants,
        skus=skus,
        requires_refundability=requires_refundability,
        intent_expiry=(
            datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(hours=1)
        ).isoformat(),
    )


def create_payment_mandate(
    cart_mandate_dict: dict,
    signed_payload: dict,
    merchant_agent: str = "merchant_agent",
) -> PaymentMandate:
    """Create a PaymentMandate with x402 PaymentPayload embedded in payment_response.details.

    Args:
        cart_mandate_dict: The CartMandate dict received from merchant.
        signed_payload: The signed x402 PaymentPayload dict.
        merchant_agent: Identifier of the merchant agent.

    Returns:
        PaymentMandate with x402 payload embedded.
    """
    # Extract cart info for the payment mandate
    contents = cart_mandate_dict.get("contents", {})
    cart_id = contents.get("id", str(uuid.uuid4()))

    # Extract total from x402 requirements in the cart
    payment_request = contents.get("payment_request", {})
    method_data = payment_request.get("method_data", [])
    total_value = 0.0
    for method in method_data:
        if method.get("supported_methods") == X402_METHOD:
            x402_data = method.get("data", {})
            accepts = x402_data.get("accepts", [])
            if accepts:
                total_value = float(accepts[0].get("amount", accepts[0].get("maxAmountRequired", "0")))
            break

    total = PaymentItem(
        label="Total",
        amount=PaymentCurrencyAmount(currency="USDC", value=total_value),
    )

    # Build PaymentResponse with x402 payload embedded in details
    payment_response = PaymentResponse(
        request_id=cart_id,
        method_name=X402_METHOD,
        details=signed_payload,
    )

    payment_mandate_contents = PaymentMandateContents(
        payment_mandate_id=str(uuid.uuid4()),
        payment_details_id=cart_id,
        payment_details_total=total,
        payment_response=payment_response,
        merchant_agent=merchant_agent,
    )

    # PaymentMandate 创建时 user_authorization 为 None，需要后续调用 sign_payment_mandate 签名
    return PaymentMandate(
        payment_mandate_contents=payment_mandate_contents,
    )


async def sign_payment_mandate(
    payment_mandate: PaymentMandate,
    wallet_service_url: str,
) -> PaymentMandate:
    """Sign a PaymentMandate via wallet service, filling user_authorization.

    Args:
        payment_mandate: The unsigned PaymentMandate.
        wallet_service_url: Base URL of the wallet service.

    Returns:
        PaymentMandate with user_authorization field set.
    """
    mandate_dict = payment_mandate.model_dump(by_alias=True)
    sig_data = await sign_mandate(mandate_dict, wallet_service_url)
    payment_mandate.user_authorization = sig_data["signature"]
    return payment_mandate


async def sign_cart_mandate_as_merchant(
    cart_mandate_dict: dict,
    wallet_service_url: str,
) -> dict:
    """Sign a CartMandate as merchant, filling merchant_authorization.

    Args:
        cart_mandate_dict: The CartMandate dict to sign.
        wallet_service_url: Base URL of the wallet service.

    Returns:
        CartMandate dict with merchant_authorization filled.
    """
    sig_data = await sign_mandate(cart_mandate_dict, wallet_service_url)
    cart_mandate_dict["merchant_authorization"] = sig_data["signature"]
    return cart_mandate_dict


async def sign_mandate(mandate_dict: dict, wallet_service_url: str) -> dict:
    """Sign any mandate dict via the wallet service's /sign-mandate endpoint.

    Args:
        mandate_dict: The mandate to sign (any type).
        wallet_service_url: Base URL of the wallet service (e.g. http://localhost:5001).

    Returns:
        Dict with 'signature' and 'address' keys.
    """
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{wallet_service_url}/sign-mandate",
            json=mandate_dict,
        )
        resp.raise_for_status()
        return resp.json()
