"""
ClientAgent — 编排器 Agent。
发现远程 merchant agent，处理 x402 支付流程:
  1. 发送用户请求给 merchant
  2. 收到 payment_required → 调用 wallet 签名
  3. 发送签名后的 payload 给 merchant
  4. 返回最终结果给用户

x402 v2 适配: 所有 metadata 中的支付数据使用 v2 格式。
"""

import json
import logging
import uuid

import httpx
from a2a.client import A2ACardResolver
from a2a.types import (
    AgentCard,
    JSONRPCError,
    Message,
    MessageSendParams,
    Part,
    Task,
    TaskState,
    TextPart,
)
from google.adk import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.tools.tool_context import ToolContext

from x402_a2a.core.utils import x402Utils
from x402_a2a.types import PaymentStatus

from .remote_connection import RemoteAgentConnections, TaskUpdateCallback
from .wallet_client import Wallet
from shared.constants import LLM_MODEL

logger = logging.getLogger(__name__)


class ClientAgent:
    """
    编排器 Agent: 发现远程 agent，委派任务，管理 x402 支付流程。
    """

    def __init__(
        self,
        remote_agent_addresses: list[str],
        http_client: httpx.AsyncClient,
        wallet: Wallet,
        task_callback: TaskUpdateCallback | None = None,
    ):
        self.task_callback = task_callback
        self.httpx_client = http_client
        self.wallet = wallet
        self.remote_agent_connections: dict[str, RemoteAgentConnections] = {}
        self.cards: dict[str, AgentCard] = {}
        self.remote_agent_addresses = remote_agent_addresses
        self.agents_info_str = ""
        self._initialized = False
        self.x402 = x402Utils()

    def create_agent(self) -> Agent:
        """创建 ADK Agent 实例。"""
        return Agent(
            model=LLM_MODEL,
            name="client_agent",
            instruction=self.root_instruction,
            before_agent_callback=self.before_agent_callback,
            description="An orchestrator that delegates tasks to merchant agents and handles x402 payments.",
            tools=[self.list_remote_agents, self.send_message],
        )

    def root_instruction(self, context: ReadonlyContext) -> str:
        """编排器系统指令。"""
        return f"""
You are a master orchestrator agent. Your job is to complete user requests by delegating tasks to specialized agents.

**Standard Operating Procedure (SOP):**

1.  **Discover**: Always start by using `list_remote_agents` to see which agents are available.
2.  **Delegate**: Send the user's request to the most appropriate agent using `send_message`.
3.  **Confirm Payment**: If the merchant requires a payment, the system will return a confirmation message. You MUST present this message to the user.
4.  **Sign and Send**: If the user confirms they want to pay (e.g., by saying "yes"), you MUST call `send_message` again, targeting the *same agent*, with the exact message: "sign_and_send_payment". The system will handle the signing and sending of the payload.
5.  **Report Outcome**: Clearly report the final success or failure message to the user.

**System Context:**

* **Available Agents**:
    {self.agents_info_str}
"""

    async def before_agent_callback(self, callback_context: CallbackContext):
        """首次运行时初始化远程 agent 连接。"""
        if self._initialized:
            return

        for address in self.remote_agent_addresses:
            card = await A2ACardResolver(self.httpx_client, address).get_agent_card()
            self.remote_agent_connections[card.name] = RemoteAgentConnections(
                self.httpx_client, card
            )
            self.cards[card.name] = card

        agent_list = [
            {"name": c.name, "description": c.description} for c in self.cards.values()
        ]
        self.agents_info_str = json.dumps(agent_list, indent=2)
        self._initialized = True

    # ── Tools ──────────────────────────────────────────────────────

    def list_remote_agents(self):
        """列出可用的远程 agent。"""
        return [
            {"name": card.name, "description": card.description}
            for card in self.cards.values()
        ]

    async def send_message(
        self, agent_name: str, message: str, tool_context: ToolContext
    ):
        """发送消息给远程 agent，处理支付流程。"""
        if agent_name not in self.remote_agent_connections:
            raise ValueError(f"Agent '{agent_name}' not found.")

        state = tool_context.state
        client = self.remote_agent_connections[agent_name]
        task_id = None
        message_metadata = {}

        # 用户确认支付 → 签名并发送
        if message == "sign_and_send_payment":
            purchase_task_data = state.get("purchase_task")
            if not purchase_task_data:
                raise ValueError("State inconsistency: 'purchase_task' not found.")

            original_task = Task.model_validate(purchase_task_data)
            task_id = original_task.id

            # 提取 payment requirements
            requirements = self.x402.get_payment_requirements(original_task)
            if not requirements:
                raise ValueError("Could not find payment requirements in the task.")

            # 通过 wallet 签名
            signed_payload = self.wallet.sign_payment(requirements)
            message_metadata[self.x402.PAYLOAD_KEY] = signed_payload.model_dump(
                by_alias=True
            )
            message_metadata[self.x402.STATUS_KEY] = PaymentStatus.PAYMENT_SUBMITTED.value

            message = "send_signed_payment_payload"

        # 构建 A2A 消息
        request = MessageSendParams(
            message=Message(
                messageId=str(uuid.uuid4()),
                role="user",
                parts=[Part(root=TextPart(text=message))],
                contextId=state.get("context_id"),
                taskId=task_id,
                metadata=message_metadata if message_metadata else None,
            )
        )

        # 发送并等待结果
        response_task = await client.send_message(
            request.message.message_id, request, self.task_callback
        )

        if isinstance(response_task, JSONRPCError):
            logger.error(f"Error from {agent_name}: {response_task.message}")
            return f"Agent '{agent_name}' returned an error: {response_task.message}"

        # 更新 state
        state["context_id"] = response_task.context_id
        state["last_contacted_agent"] = agent_name

        # 处理响应
        if response_task.status.state == TaskState.input_required:
            # 商户要求支付 → 保存 task, 向用户确认
            state["purchase_task"] = response_task.model_dump(by_alias=True)
            requirements = self.x402.get_payment_requirements(response_task)
            if not requirements or not requirements.accepts:
                raise ValueError("Server requested payment but sent no valid options.")

            option = requirements.accepts[0]
            # v1 字段 max_amount_required, v2 用 amount — 兼容两者
            currency_amount = getattr(option, "max_amount_required", None) or getattr(option, "amount", "?")
            currency_name = option.extra.get("name", "TOKEN") if option.extra else "TOKEN"
            product_name = (
                option.extra.get("product", {}).get("name", "the item")
                if option.extra
                else "the item"
            )

            return (
                f"The merchant is requesting payment for '{product_name}' "
                f"for {currency_amount} {currency_name}. "
                f"Do you want to approve this payment?"
            )

        elif response_task.status.state in (TaskState.completed, TaskState.failed):
            final_text = []
            if response_task.artifacts:
                for artifact in response_task.artifacts:
                    for part in artifact.parts:
                        part_root = part.root
                        if isinstance(part_root, TextPart):
                            final_text.append(part_root.text)

            if final_text:
                return " ".join(final_text)

            if self.x402.get_payment_status(response_task) == PaymentStatus.PAYMENT_COMPLETED:
                return "Payment successful! Your purchase is complete."

            return f"Task with {agent_name} is {response_task.status.state.value}."

        else:
            return f"Task with {agent_name} is in state: {response_task.status.state.value}"
