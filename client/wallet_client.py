"""
Wallet Client — 钱包接口和实现。
支持两种模式:
  1. RemoteWallet: 调用 Wallet Service (Flask :5001) 远程签名
  2. LocalWallet: 本地私钥签名 (使用 x402_a2a 库)
"""

import os
import logging
from abc import ABC, abstractmethod

import httpx
import eth_account
from x402_a2a.types import PaymentPayload, x402PaymentRequiredResponse
from x402_a2a.core.wallet import process_payment_required

from shared.constants import X402_VERSION

logger = logging.getLogger(__name__)


class Wallet(ABC):
    """钱包抽象接口 — 签名 PaymentRequirements 返回 PaymentPayload。"""

    @abstractmethod
    def sign_payment(self, requirements: x402PaymentRequiredResponse) -> PaymentPayload:
        raise NotImplementedError


class RemoteWallet(Wallet):
    """
    远程钱包 — 调用 Wallet Service HTTP API 完成签名。
    返回的 PaymentPayload 已经是 v2 格式。
    """

    def __init__(self, wallet_service_url: str = "http://localhost:5001"):
        self._url = wallet_service_url

    def sign_payment(self, requirements: x402PaymentRequiredResponse) -> PaymentPayload:
        """调用 wallet service /sign 接口签名。"""
        # 将 requirements 序列化后发送给 wallet service
        req_data = requirements.model_dump(by_alias=True)
        response = httpx.post(f"{self._url}/sign", json=req_data, timeout=30)
        response.raise_for_status()

        payload_data = response.json()
        # wallet service 返回 v2 格式, 但 x402_a2a PaymentPayload 是 v1 model
        # 需要提取核心字段构建 v1 compatible PaymentPayload
        return self._adapt_v2_to_payload(payload_data, requirements)

    def _adapt_v2_to_payload(
        self, v2_data: dict, requirements: x402PaymentRequiredResponse
    ) -> PaymentPayload:
        """将 wallet service 返回的 v2 payload 适配为 x402_a2a 的 PaymentPayload model。"""
        accepted = v2_data.get("accepted", {})
        inner_payload = v2_data.get("payload", {})

        # 构建 x402_a2a 兼容的 PaymentPayload
        payload_dict = {
            "scheme": v2_data.get("scheme", accepted.get("scheme", "exact")),
            "network": v2_data.get("network", accepted.get("network", "")),
            "payload": {
                "signature": inner_payload.get("signature", ""),
                "authorization": inner_payload.get("authorization", {}),
            },
        }
        return PaymentPayload.model_validate(payload_dict)


class LocalWallet(Wallet):
    """
    本地钱包 — 使用私钥直接签名 (用于测试)。
    通过 x402_a2a 库的 process_payment_required 完成签名。
    """

    def __init__(self, private_key: str | None = None):
        self._private_key = private_key or os.getenv(
            "WALLET_PRIVATE_KEY",
            "0x0000000000000000000000000000000000000000000000000000000000000001",
        )

    def sign_payment(self, requirements: x402PaymentRequiredResponse) -> PaymentPayload:
        """使用本地私钥签名 EIP-3009 transferWithAuthorization。"""
        account = eth_account.Account.from_key(self._private_key)
        return process_payment_required(requirements, account)
