"""
RemoteAgentConnections — 封装与远程 A2A agent 的通信。
"""

from typing import Callable

import httpx
from a2a.client import A2AClient
from a2a.types import (
    AgentCard,
    JSONRPCErrorResponse,
    Message,
    MessageSendParams,
    SendMessageRequest,
    SendStreamingMessageRequest,
    Task,
    TaskArtifactUpdateEvent,
    TaskStatusUpdateEvent,
)

type TaskCallbackArg = Task | TaskStatusUpdateEvent | TaskArtifactUpdateEvent
TaskUpdateCallback = Callable[[TaskCallbackArg], Task]


class RemoteAgentConnections:
    """管理与单个远程 agent 的 A2A 连接。"""

    def __init__(self, client: httpx.AsyncClient, agent_card: AgentCard):
        self.agent_client = A2AClient(client, agent_card)
        self.card = agent_card
        self.pending_tasks: set[str] = set()

    def get_agent(self) -> AgentCard:
        return self.card

    async def send_message(
        self,
        id: int | str,
        request: MessageSendParams,
        task_callback: TaskUpdateCallback | None,
    ) -> Task | Message | None:
        """发送消息到远程 agent，支持 streaming 和 non-streaming。"""
        if self.card.capabilities.streaming:
            task = None
            async for response in self.agent_client.send_message_streaming(
                SendStreamingMessageRequest(id=id, params=request)
            ):
                if not response.root.result:
                    return response.root.error
                event = response.root.result
                if isinstance(event, Message):
                    return event
                if task_callback and event:
                    task = task_callback(event)
                if hasattr(event, "final") and event.final:
                    break
            return task
        else:
            response = await self.agent_client.send_message(
                SendMessageRequest(id=id, params=request)
            )
            if isinstance(response.root, JSONRPCErrorResponse):
                return response.root.error
            if isinstance(response.root.result, Message):
                return response.root.result
            if task_callback:
                task_callback(response.root.result)
            return response.root.result
