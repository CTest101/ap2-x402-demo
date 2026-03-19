"""
Merchant Agent — ADK LlmAgent 定义。
商品查询工具在需要支付时抛出 x402PaymentRequiredException，
由 x402_executor 层捕获并处理 A2A 支付流程。
"""

import hashlib

from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.genai import types
from x402_a2a.types import PaymentRequirements, x402PaymentRequiredException
from x402_a2a import get_extension_declaration
from a2a.types import AgentCard, AgentCapabilities, AgentSkill

from shared.constants import (
    NETWORK,
    USDC_ADDRESS,
    X402_VERSION,
    PAYMENT_TIMEOUT_SECONDS,
    LLM_MODEL,
)


class MerchantAgent:
    """
    商户 Agent：接收购买请求，返回商品价格并触发 x402 支付流程。
    """

    def __init__(self, wallet_address: str = "0xAb5801a7D398351b8bE11C439e05C5B3259aeC9B"):
        self._wallet_address = wallet_address

    def _get_product_price(self, product_name: str) -> str:
        """根据商品名称生成确定性价格 (单位: 最小精度, USDC 6位小数)。"""
        price = (
            int(hashlib.sha256(product_name.lower().encode()).hexdigest(), 16)
            % 99900001
            + 5000
        )
        return str(price)

    def get_product_and_request_payment(self, product_name: str) -> dict:
        """
        Agent 工具函数：查询商品并请求支付。
        抛出 x402PaymentRequiredException 让 x402 executor 处理支付流程。
        """
        if not product_name:
            return {"error": "Product name cannot be empty."}

        price = self._get_product_price(product_name)

        # 构建 PaymentRequirements — 注意: x402_a2a 库内部使用 v1 字段名,
        # 我们在 x402_executor 层做 v1→v2 转换
        requirements = PaymentRequirements(
            scheme="exact",
            network=NETWORK,  # CAIP-2 格式: "eip155:84532"
            asset=USDC_ADDRESS,
            pay_to=self._wallet_address,
            max_amount_required=price,  # v1 字段名, 在 executor 层映射为 v2 的 "amount"
            description=f"Payment for: {product_name}",
            resource=f"https://merchant.example/product/{product_name}",
            mime_type="application/json",
            max_timeout_seconds=PAYMENT_TIMEOUT_SECONDS,
            extra={
                "name": "USDC",
                "version": "2",
                "product": {
                    "sku": f"{product_name}_sku",
                    "name": product_name,
                    "version": "1",
                },
            },
        )

        # 触发支付流程 — 由 x402ServerExecutor 捕获
        raise x402PaymentRequiredException(product_name, requirements)

    def before_agent_callback(self, callback_context: CallbackContext):
        """
        支付验证后注入虚拟工具响应，让 LLM 知道支付已完成。
        """
        payment_data = callback_context.state.get("payment_verified_data")
        if payment_data:
            del callback_context.state["payment_verified_data"]

            tool_response = types.Part(
                function_response=types.FunctionResponse(
                    name="check_payment_status",
                    response=payment_data,
                )
            )
            callback_context.new_user_message = types.Content(parts=[tool_response])

    def create_agent(self) -> LlmAgent:
        """创建 ADK LlmAgent 实例。"""
        return LlmAgent(
            model=LLM_MODEL,
            name="merchant_agent",
            description="A merchant agent that sells products using x402 payment protocol.",
            instruction="""You are a helpful merchant agent for an online store.
- When a user asks to buy an item, use the `get_product_and_request_payment` tool.
- If you receive a successful result from the `check_payment_status` tool, confirm the purchase and tell the user their order is being prepared. Do not ask for payment again.
- If the system tells you the payment failed, relay the error clearly and politely.
""",
            tools=[self.get_product_and_request_payment],
            before_agent_callback=self.before_agent_callback,
        )

    def create_agent_card(self, url: str) -> AgentCard:
        """创建 A2A AgentCard。"""
        return AgentCard(
            name="x402 Merchant Agent",
            description="Sells products using x402 v2 payment protocol over A2A.",
            url=url,
            version="1.0.0",
            defaultInputModes=["text", "text/plain"],
            defaultOutputModes=["text", "text/plain"],
            capabilities=AgentCapabilities(
                streaming=False,
                extensions=[
                    get_extension_declaration(
                        description="Supports payments using x402 protocol v2.",
                        required=True,
                    )
                ],
            ),
            skills=[
                AgentSkill(
                    id="buy_product",
                    name="Buy Product",
                    description="Purchase any product with x402 crypto payment.",
                    tags=["x402", "payment", "merchant", "purchase"],
                    examples=[
                        "I want to buy a laptop",
                        "How much for a red stapler?",
                        "Can I purchase a copy of Moby Dick?",
                    ],
                )
            ],
        )
