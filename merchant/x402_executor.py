"""
x402MerchantExecutor — 包装 ADKAgentExecutor，处理 x402 支付流程。
继承 x402ServerExecutor，实现 verify_payment / settle_payment。

关键职责:
  1. 捕获 x402PaymentRequiredException → 返回 input_required + v2 PaymentRequired
  2. 接收 payment-submitted → verify → 执行 delegate → settle
  3. v1→v2 格式转换 (x402_a2a 库内部使用 v1 types)
"""

import os
import logging
from typing import override

from a2a.server.agent_execution import AgentExecutor
from x402_a2a.executors import x402ServerExecutor
from x402_a2a.types import (
    PaymentPayload,
    PaymentRequirements,
    SettleResponse,
    VerifyResponse,
    x402ExtensionConfig,
    x402PaymentRequiredResponse,
)
from x402_a2a import FacilitatorClient, FacilitatorConfig

from .facilitator import MockFacilitator, LocalFacilitator
from shared.constants import X402_VERSION

logger = logging.getLogger(__name__)


class x402MerchantExecutor(x402ServerExecutor):
    """
    商户 x402 Executor — 连接 facilitator 完成支付验证和结算。
    默认使用 MockFacilitator (USE_MOCK_FACILITATOR=true)。

    对 x402_a2a 库的 v1 类型做 v2 适配:
      - PaymentRequired 响应中 x402Version=2
      - 网络使用 CAIP-2 格式
    """

    def __init__(
        self,
        delegate: AgentExecutor,
        facilitator_config: FacilitatorConfig | None = None,
    ):
        super().__init__(delegate, x402ExtensionConfig())

        use_mock = os.getenv("USE_MOCK_FACILITATOR", "true").lower() == "true"
        if use_mock:
            logger.info("Using Mock Facilitator")
            self._facilitator = MockFacilitator()
        else:
            logger.info("Using Local Facilitator")
            self._facilitator = LocalFacilitator()

    @override
    async def verify_payment(
        self, payload: PaymentPayload, requirements: PaymentRequirements
    ) -> VerifyResponse:
        """通过 facilitator 验证支付。"""
        response = await self._facilitator.verify(payload, requirements)
        if response.is_valid:
            logger.info("Payment verified successfully")
        else:
            logger.warning(f"Payment verification failed: {response.invalid_reason}")
        return response

    @override
    async def settle_payment(
        self, payload: PaymentPayload, requirements: PaymentRequirements
    ) -> SettleResponse:
        """通过 facilitator 结算支付。"""
        response = await self._facilitator.settle(payload, requirements)
        if response.success:
            logger.info("Payment settled successfully")
        else:
            logger.warning(f"Payment settlement failed: {response.error_reason}")
        return response
