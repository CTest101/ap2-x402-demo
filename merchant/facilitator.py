"""
Facilitator 实现:
  - MockFacilitator: 用于本地测试，不调用链上合约
  - LocalFacilitator: 可扩展的本地验证 (预留)
"""

import logging
from typing import override

from x402_a2a import FacilitatorClient
from x402_a2a.types import (
    ExactPaymentPayload,
    PaymentPayload,
    PaymentRequirements,
    SettleResponse,
    VerifyResponse,
)

logger = logging.getLogger(__name__)


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

        payer = None
        if isinstance(payload.payload, ExactPaymentPayload):
            payer = payload.payload.authorization.from_
        else:
            raise TypeError(f"Unsupported payload type: {type(payload.payload)}")

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
            return SettleResponse(success=True, network="mock-network")
        return SettleResponse(success=False, error_reason="mock_settlement_failed")


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
        payer = None
        if isinstance(payload.payload, ExactPaymentPayload):
            payer = payload.payload.authorization.from_
        return VerifyResponse(is_valid=True, payer=payer)

    @override
    async def settle(
        self, payload: PaymentPayload, requirements: PaymentRequirements
    ) -> SettleResponse:
        # TODO: 接入 web3 链上结算
        logger.info("--- LOCAL FACILITATOR: SETTLE (stub) ---")
        return SettleResponse(success=True, network="eip155:84532")
