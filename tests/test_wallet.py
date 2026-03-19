"""
Wallet Service 测试 — 验证签名逻辑和 API 端点。
"""

import json
import pytest
from eth_account import Account

from wallet.server import app, _sign_transfer_authorization
from shared.constants import NETWORK, USDC_ADDRESS, X402_VERSION


@pytest.fixture
def client():
    """Flask test client."""
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


class TestSignEndpoint:
    """POST /sign 端点测试。"""

    def test_sign_returns_v2_payload(self, client):
        """签名返回 x402 v2 格式的 PaymentPayload。"""
        requirements = {
            "scheme": "exact",
            "network": NETWORK,
            "asset": USDC_ADDRESS,
            "payTo": "0xAb5801a7D398351b8bE11C439e05C5B3259aeC9B",
            "amount": "100000",
            "description": "Test payment",
            "resource": "https://example.com/test",
            "mimeType": "application/json",
            "maxTimeoutSeconds": 1200,
            "extra": {"name": "USDC", "version": "2"},
        }

        response = client.post(
            "/sign",
            data=json.dumps(requirements),
            content_type="application/json",
        )
        assert response.status_code == 200

        data = response.get_json()
        # v2 格式检查
        assert data["x402Version"] == X402_VERSION
        assert data["scheme"] == "exact"
        assert data["network"] == NETWORK
        assert "resource" in data
        assert "url" in data["resource"]
        assert "accepted" in data
        assert "payload" in data
        assert "signature" in data["payload"]
        assert "authorization" in data["payload"]

        auth = data["payload"]["authorization"]
        assert "from" in auth
        assert "to" in auth
        assert auth["to"] == requirements["payTo"]

    def test_sign_with_accepts_array(self, client):
        """支持 accepts 数组格式的请求。"""
        data = {
            "accepts": [
                {
                    "scheme": "exact",
                    "network": NETWORK,
                    "asset": USDC_ADDRESS,
                    "payTo": "0x1234567890abcdef1234567890abcdef12345678",
                    "amount": "50000",
                    "description": "Test",
                    "resource": "https://example.com",
                    "extra": {"name": "USDC", "version": "2"},
                }
            ]
        }

        response = client.post(
            "/sign",
            data=json.dumps(data),
            content_type="application/json",
        )
        assert response.status_code == 200
        result = response.get_json()
        assert result["x402Version"] == X402_VERSION

    def test_sign_empty_body_returns_400(self, client):
        """空请求体返回 400。"""
        response = client.post("/sign", content_type="application/json")
        assert response.status_code == 400

    def test_sign_empty_accepts_returns_400(self, client):
        """空 accepts 数组返回 400。"""
        response = client.post(
            "/sign",
            data=json.dumps({"accepts": []}),
            content_type="application/json",
        )
        assert response.status_code == 400


class TestAddressEndpoint:
    """POST /address 端点测试。"""

    def test_address_returns_valid_address(self, client):
        """返回有效的以太坊地址。"""
        response = client.post("/address")
        assert response.status_code == 200
        data = response.get_json()
        assert "address" in data
        assert data["address"].startswith("0x")
        assert len(data["address"]) == 42

    def test_address_get_also_works(self, client):
        """GET 请求也能获取地址。"""
        response = client.get("/address")
        assert response.status_code == 200
        data = response.get_json()
        assert "address" in data


class TestSignLogic:
    """签名逻辑单元测试。"""

    def test_sign_transfer_authorization_structure(self):
        """验证 _sign_transfer_authorization 返回正确结构。"""
        requirements = {
            "scheme": "exact",
            "network": NETWORK,
            "asset": USDC_ADDRESS,
            "payTo": "0xAb5801a7D398351b8bE11C439e05C5B3259aeC9B",
            "amount": "100000",
            "description": "Test",
            "resource": "https://example.com/test",
            "extra": {"name": "USDC", "version": "2"},
        }

        result = _sign_transfer_authorization(requirements)

        assert result["x402Version"] == 2
        assert result["network"] == NETWORK
        assert result["payload"]["signature"].startswith("0x")
        assert len(result["payload"]["signature"]) > 10

    def test_caip2_chain_id_extraction(self):
        """验证 CAIP-2 链 ID 提取。"""
        requirements = {
            "scheme": "exact",
            "network": "eip155:84532",
            "asset": USDC_ADDRESS,
            "payTo": "0x0000000000000000000000000000000000000001",
            "amount": "1000",
            "description": "Test",
            "resource": "https://example.com",
            "extra": {"name": "USDC", "version": "2"},
        }

        result = _sign_transfer_authorization(requirements)
        # 签名应该成功 (不抛异常即可)
        assert "payload" in result
        assert "authorization" in result["payload"]
