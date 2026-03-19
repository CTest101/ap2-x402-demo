"""
ADKAgentExecutor — 将 ADK Runner 桥接到 A2A AgentExecutor 接口。
负责执行 ADK agent 的多轮对话循环，处理工具调用。
x402PaymentRequiredException 会向上传播给 x402_executor 层处理。
"""

import json
import logging
from collections.abc import AsyncGenerator

from a2a.server.agent_execution import AgentExecutor
from a2a.server.agent_execution.context import RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import (
    AgentCard,
    DataPart,
    FilePart,
    FileWithBytes,
    FileWithUri,
    Part,
    TaskState,
    TextPart,
    UnsupportedOperationError,
)
from a2a.utils.errors import ServerError
from google.adk import Runner
from google.adk.events import Event
from google.genai import types

from x402_a2a.core.utils import x402Utils
from x402_a2a.types import x402PaymentRequiredException

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


class ADKAgentExecutor(AgentExecutor):
    """A2A AgentExecutor 实现：运行 ADK Agent 并处理多轮工具调用。"""

    def __init__(self, runner: Runner, card: AgentCard):
        self.runner = runner
        self._card = card
        self.x402 = x402Utils()

    def _run_agent(
        self, session_id: str, new_message: types.Content
    ) -> AsyncGenerator[Event, None]:
        return self.runner.run_async(
            session_id=session_id, user_id="self", new_message=new_message
        )

    async def _process_request(
        self,
        new_message: types.Content,
        session_id: str,
        task_updater: TaskUpdater,
    ) -> None:
        """多轮执行循环: agent → tool call → tool response → agent → final."""
        session = await self._upsert_session(session_id)
        session_id = session.id
        current_message = new_message

        while True:
            event_stream = self._run_agent(session_id, current_message)
            function_calls_to_execute = []

            async for event in event_stream:
                if event.is_final_response():
                    parts = []
                    if event.content and event.content.parts:
                        parts = convert_genai_parts_to_a2a(event.content.parts)
                    if parts:
                        await task_updater.add_artifact(parts)
                    await task_updater.complete()
                    return

                if event.get_function_calls():
                    function_calls_to_execute.extend(event.get_function_calls())
                elif event.content and event.content.parts:
                    await task_updater.update_status(
                        TaskState.working,
                        message=task_updater.new_agent_message(
                            convert_genai_parts_to_a2a(event.content.parts),
                        ),
                    )

            if not function_calls_to_execute:
                logger.warning("ADK agent stream ended unexpectedly. Completing task.")
                await task_updater.complete()
                return

            # 执行工具调用
            tool_outputs = []
            for call in function_calls_to_execute:
                tool_name = call.name
                tool_args = dict(call.args)
                logger.debug(f"Executing tool '{tool_name}' with args: {tool_args}")

                target_tool = next(
                    (
                        t
                        for t in self.runner.agent.tools
                        if getattr(t, "__name__", None) == tool_name
                    ),
                    None,
                )
                if not target_tool:
                    raise ValueError(f"Tool '{tool_name}' not found on agent.")

                try:
                    # x402PaymentRequiredException 需要向上传播
                    tool_result = target_tool(**tool_args)
                    tool_outputs.append(
                        types.Part(
                            function_response=types.FunctionResponse(
                                name=tool_name, response={"result": tool_result}
                            )
                        )
                    )
                except x402PaymentRequiredException:
                    raise  # 传播给 x402_executor
                except Exception as e:
                    logger.error(f"Tool '{tool_name}' failed: {e}", exc_info=True)
                    tool_outputs.append(
                        types.Part(
                            function_response=types.FunctionResponse(
                                name=tool_name, response={"error": str(e)}
                            )
                        )
                    )

            current_message = types.Content(parts=tool_outputs, role="tool")

    async def execute(self, context: RequestContext, event_queue: EventQueue):
        """主入口: 处理请求，支持支付验证后的续接。"""
        task_updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        await self._upsert_session(context.context_id)

        # 检查是否是支付验证后的续接
        if context.current_task and context.current_task.metadata.get(
            "x402_payment_verified", False
        ):
            session = await self._upsert_session(context.context_id)
            # 获取产品名 — 从 payment requirements 的 extra 字段
            product_name = (
                context.current_task.status.message.metadata.get(
                    "x402.payment.required", {}
                )
                .get("accepts", [{}])[0]
                .get("extra", {})
                .get("product", {})
                .get("name", "the item")
            )
            session.state["payment_verified_data"] = {
                "product": product_name,
                "status": "SUCCESS",
            }
            user_message = types.UserContent(
                parts=[types.Part(text="Payment verified. Please proceed.")]
            )
            session = await self._upsert_session(session.id)
        else:
            user_message = types.UserContent(
                parts=convert_a2a_parts_to_genai(context.message.parts)
            )

        await self._process_request(user_message, context.context_id, task_updater)

    async def cancel(self, context: RequestContext, event_queue: EventQueue):
        raise ServerError(error=UnsupportedOperationError())

    async def _upsert_session(self, session_id: str):
        session = await self.runner.session_service.get_session(
            app_name=self.runner.app_name, user_id="self", session_id=session_id
        )
        if session:
            return session
        return await self.runner.session_service.create_session(
            app_name=self.runner.app_name, user_id="self", session_id=session_id
        )


# ── Part conversion utilities ──────────────────────────────────────


def convert_a2a_parts_to_genai(parts: list[Part]) -> list[types.Part]:
    return [convert_a2a_part_to_genai(part) for part in parts]


def convert_a2a_part_to_genai(part: Part) -> types.Part:
    part = part.root
    if isinstance(part, TextPart):
        return types.Part(text=part.text)
    if isinstance(part, DataPart):
        return types.Part(text=f"Received structured data:\n```json\n{json.dumps(part.data)}\n```")
    if isinstance(part, FilePart):
        if isinstance(part.file, FileWithUri):
            return types.Part(
                file_data=types.FileData(file_uri=part.file.uri, mime_type=part.file.mimeType)
            )
        if isinstance(part.file, FileWithBytes):
            return types.Part(
                inline_data=types.Blob(data=part.file.bytes, mime_type=part.file.mimeType)
            )
        raise ValueError(f"Unsupported file type: {type(part.file)}")
    raise ValueError(f"Unsupported part type: {type(part)}")


def convert_genai_parts_to_a2a(parts: list[types.Part]) -> list[Part]:
    return [
        convert_genai_part_to_a2a(part)
        for part in parts
        if (part.text or part.file_data or part.inline_data or part.function_response)
    ]


def convert_genai_part_to_a2a(part: types.Part) -> Part:
    if part.text:
        return Part(root=TextPart(text=part.text))
    if part.file_data:
        return Part(root=FilePart(file=FileWithUri(uri=part.file_data.file_uri, mimeType=part.file_data.mime_type)))
    if part.inline_data:
        return Part(root=FilePart(file=FileWithBytes(bytes=part.inline_data.data, mimeType=part.inline_data.mime_type)))
    if part.function_response:
        return Part(root=DataPart(data=part.function_response.response))
    raise ValueError(f"Unsupported part type: {part}")
