"""
Facilitator 实现:
  - MockFacilitator: 用于本地测试，不调用链上合约
  - LocalFacilitator: 可扩展的本地验证 (预留)
"""

import logging
from typing import override

from x402_a2a import FacilitatorClient
from x402_a2a.types import (
    PaymentPayload,
    PaymentRequirements,
    SettleResponse,
    VerifyResponse,
)

logger = logging.getLogger(__name__)


def _extract_payer_from_payload(payload: PaymentPayload) -> str | None:
    """Extract payer address from PaymentPayload.payload dict."""
    if isinstance(payload.payload, dict):
        auth = payload.payload.get("authorization", {})
        return auth.get("from")
    return None


class MockFacilitator(FacilitatorClient):
    """
    测试用 Mock Facilitator — 跳过链上验证，返回可配置的结果。
    """

    def __init__(self, is_valid: bool = True, is_settled: bool = True):
        self._is_valid = is_valid
        self._is_settled = is_settled

    @override
    async def verify(
        self, payload: PaymentPayload, requirements: PaymentRequirements
    ) -> VerifyResponse:
        """模拟支付验证。"""
        logger.info("--- MOCK FACILITATOR: VERIFY ---")
        logger.info(f"Payload:\n{payload.model_dump_json(indent=2)}")

        payer = _extract_payer_from_payload(payload)

        if self._is_valid:
            return VerifyResponse(is_valid=True, payer=payer)
        return VerifyResponse(is_valid=False, invalid_reason="mock_invalid_payload")

    @override
    async def settle(
        self, payload: PaymentPayload, requirements: PaymentRequirements
    ) -> SettleResponse:
        """模拟支付结算。"""
        logger.info("--- MOCK FACILITATOR: SETTLE ---")
        if self._is_settled:
            return SettleResponse(
                success=True,
                network=requirements.network,
                transaction="0xmock_tx_hash",
            )
        return SettleResponse(
            success=False,
            error_reason="mock_settlement_failed",
            network=requirements.network,
            transaction="",
        )


class LocalFacilitator(FacilitatorClient):
    """
    本地 Facilitator — 预留扩展位，未来接入链上验证。
    目前行为和 MockFacilitator 相同。
    """

    @override
    async def verify(
        self, payload: PaymentPayload, requirements: PaymentRequirements
    ) -> VerifyResponse:
        # TODO: 接入 web3 链上验证
        logger.info("--- LOCAL FACILITATOR: VERIFY (stub) ---")
        payer = _extract_payer_from_payload(payload)
        return VerifyResponse(is_valid=True, payer=payer)

    @override
    async def settle(
        self, payload: PaymentPayload, requirements: PaymentRequirements
    ) -> SettleResponse:
        # TODO: 接入 web3 链上结算
        logger.info("--- LOCAL FACILITATOR: SETTLE (stub) ---")
        return SettleResponse(
            success=True,
            network=requirements.network,
            transaction="0xlocal_tx_hash",
        )
