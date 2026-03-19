"""
Wallet Service — Flask :5001
负责私钥管理和 EIP-712 / EIP-3009 transferWithAuthorization 签名。
客户端和商户代理通过 HTTP 调用此服务来完成支付签名。

Endpoints:
  POST /sign    — 接收 PaymentRequirements，签名返回 PaymentPayload (v2 format)
  POST /address — 返回钱包地址
"""

import os
import time
import logging

from eth_account import Account
from eth_account.messages import encode_typed_data
from flask import Flask, jsonify, request
from dotenv import load_dotenv

from shared.constants import NETWORK, USDC_ADDRESS, X402_VERSION

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# 加载私钥
PRIVATE_KEY = os.getenv(
    "WALLET_PRIVATE_KEY",
    "0x0000000000000000000000000000000000000000000000000000000000000001",
)
account = Account.from_key(PRIVATE_KEY)


def _build_eip712_typed_data(authorization: dict) -> dict:
    """构建 EIP-712 typed data for transferWithAuthorization (EIP-3009)。"""
    return {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "TransferWithAuthorization": [
                {"name": "from", "type": "address"},
                {"name": "to", "type": "address"},
                {"name": "value", "type": "uint256"},
                {"name": "validAfter", "type": "uint256"},
                {"name": "validBefore", "type": "uint256"},
                {"name": "nonce", "type": "bytes32"},
            ],
        },
        "primaryType": "TransferWithAuthorization",
        "domain": {
            "name": authorization.get("token_name", "USDC"),
            "version": authorization.get("token_version", "2"),
            "chainId": int(authorization.get("chain_id", 84532)),
            "verifyingContract": authorization.get("verifying_contract", USDC_ADDRESS),
        },
        "message": {
            "from": authorization["from"],
            "to": authorization["to"],
            "value": int(authorization["value"]),
            "validAfter": int(authorization.get("valid_after", 0)),
            "validBefore": int(authorization.get("valid_before", int(time.time()) + 3600)),
            "nonce": bytes.fromhex(authorization["nonce"].replace("0x", "")),
        },
    }


def _sign_transfer_authorization(requirements: dict) -> dict:
    """
    根据 PaymentRequirements 生成 EIP-3009 签名。
    返回 x402 v2 格式的 PaymentPayload。
    """
    pay_to = requirements.get("payTo", requirements.get("pay_to", ""))
    # v2 uses "amount", v1 uses "maxAmountRequired"
    amount = requirements.get("amount", requirements.get("maxAmountRequired", "0"))
    asset = requirements.get("asset", USDC_ADDRESS)
    network = requirements.get("network", NETWORK)

    # 提取链 ID (from CAIP-2 format "eip155:84532")
    chain_id = 84532
    if ":" in network:
        chain_id = int(network.split(":")[1])

    nonce = os.urandom(32)
    nonce_hex = "0x" + nonce.hex()

    valid_after = 0
    valid_before = int(time.time()) + 3600  # 1小时有效

    authorization = {
        "from": account.address,
        "to": pay_to,
        "value": str(amount),
        "valid_after": str(valid_after),
        "valid_before": str(valid_before),
        "nonce": nonce_hex,
        "chain_id": chain_id,
        "verifying_contract": asset,
        "token_name": requirements.get("extra", {}).get("name", "USDC"),
        "token_version": requirements.get("extra", {}).get("version", "2"),
    }

    typed_data = _build_eip712_typed_data(authorization)
    signed = account.sign_typed_data(
        typed_data["domain"],
        {"TransferWithAuthorization": typed_data["types"]["TransferWithAuthorization"]},
        typed_data["message"],
    )
    signature = signed.signature.hex()
    if not signature.startswith("0x"):
        signature = "0x" + signature

    # 构建 v2 PaymentPayload
    resource_url = requirements.get("resource", "")
    resource_desc = requirements.get("description", "")
    resource_mime = requirements.get("mimeType", requirements.get("mime_type", "application/json"))

    payload = {
        "x402Version": X402_VERSION,
        "scheme": requirements.get("scheme", "exact"),
        "network": network,
        "resource": {
            "url": resource_url if isinstance(resource_url, str) else str(resource_url),
            "description": resource_desc,
            "mimeType": resource_mime,
        },
        "accepted": requirements,  # 选中的 PaymentRequirements
        "payload": {
            "signature": signature,
            "authorization": {
                "from": account.address,
                "to": pay_to,
                "value": str(amount),
                "validAfter": str(valid_after),
                "validBefore": str(valid_before),
                "nonce": nonce_hex,
            },
        },
    }

    return payload


@app.route("/sign", methods=["POST"])
def sign():
    """
    接收 PaymentRequirements (单个或 accepts 数组的第一个)，
    签名后返回 v2 格式 PaymentPayload。
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "Missing request body"}), 400

    # 支持直接传入 requirements 或 accepts 数组
    requirements = data
    if "accepts" in data:
        accepts = data["accepts"]
        if not accepts:
            return jsonify({"error": "Empty accepts array"}), 400
        requirements = accepts[0]

    try:
        payload = _sign_transfer_authorization(requirements)
        logger.info(f"Signed payment for {payload['payload']['authorization']['to']}")
        return jsonify(payload)
    except Exception as e:
        logger.error(f"Signing failed: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/address", methods=["POST", "GET"])
def address():
    """返回当前钱包地址。"""
    return jsonify({"address": account.address})


def main():
    port = int(os.getenv("WALLET_SERVICE_PORT", "5001"))
    logger.info(f"Wallet Service starting on :{port}")
    logger.info(f"Wallet address: {account.address}")
    app.run(host="0.0.0.0", port=port, debug=False)


if __name__ == "__main__":
    main()
