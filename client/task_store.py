"""
TaskStore — 客户端任务状态管理。
跟踪与远程 agent 交互产生的 task 和 artifact。
"""

import uuid

from a2a.types import (
    Artifact,
    Message,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)

from .remote_connection import TaskCallbackArg


class TaskStore:
    """管理客户端侧的 task 状态。"""

    def __init__(self):
        self._tasks: list[Task] = []
        self._artifact_chunks: dict[str, list[Artifact]] = {}
        self._task_map: dict[str, str] = {}

    def _add_task(self, task: Task):
        self._tasks.append(task)

    def _update_task(self, task: Task):
        for i, t in enumerate(self._tasks):
            if t.id == task.id:
                self._tasks[i] = task
                return

    def update_task(self, task: TaskCallbackArg):
        """处理 task 更新事件 (TaskStatusUpdateEvent / TaskArtifactUpdateEvent / Task)。"""
        if isinstance(task, TaskStatusUpdateEvent):
            current_task = self._add_or_get_task(task)
            current_task.status = task.status
            self._attach_message_to_task(task.status.message, current_task.id)
            self._insert_message_history(current_task, task.status.message)
            self._update_task(current_task)
            return current_task

        elif isinstance(task, TaskArtifactUpdateEvent):
            for part in task.artifact.parts:
                if part.root and hasattr(part.root, "text"):
                    print(part.root.text)
            current_task = self._add_or_get_task(task)
            self._process_artifact_event(current_task, task)
            self._update_task(current_task)
            return current_task

        elif not any(filter(lambda x: x and x.id == task.id, self._tasks)):
            self._attach_message_to_task(task.status.message, task.id)
            self._add_task(task)
            return task
        else:
            self._attach_message_to_task(task.status.message, task.id)
            self._update_task(task)
            return task

    def _attach_message_to_task(self, message: Message | None, task_id: str):
        if message:
            self._task_map[message.message_id] = task_id

    def _insert_message_history(self, task: Task, message: Message | None):
        if not message:
            return
        if task.history is None:
            task.history = []
        if not message.message_id:
            return
        if task.history and (
            task.status.message
            and task.status.message.message_id
            not in [x.message_id for x in task.history]
        ):
            task.history.append(task.status.message)
        elif not task.history and task.status.message:
            task.history = [task.status.message]

    def _add_or_get_task(self, event: TaskCallbackArg):
        task_id = None
        if isinstance(event, Message):
            task_id = event.task_id
        elif isinstance(event, Task):
            task_id = event.id
        else:
            task_id = event.task_id

        if not task_id:
            task_id = str(uuid.uuid4())

        current_task = next(filter(lambda x: x.id == task_id, self._tasks), None)
        if not current_task:
            context_id = event.context_id
            current_task = Task(
                id=task_id,
                status=TaskStatus(state=TaskState.submitted),
                artifacts=[],
                contextId=context_id,
            )
            self._add_task(current_task)
        return current_task

    def _process_artifact_event(
        self, current_task: Task, event: TaskArtifactUpdateEvent
    ):
        artifact = event.artifact
        if not event.append:
            if event.last_chunk is None or event.last_chunk:
                if not current_task.artifacts:
                    current_task.artifacts = []
                current_task.artifacts.append(artifact)
            else:
                if artifact.artifactId not in self._artifact_chunks:
                    self._artifact_chunks[artifact.artifactId] = []
                self._artifact_chunks[artifact.artifactId].append(artifact)
        else:
            current_temp = self._artifact_chunks[artifact.artifactId][-1]
            current_temp.parts.extend(artifact.parts)
            if event.last_chunk:
                if current_task.artifacts:
                    current_task.artifacts.append(current_temp)
                else:
                    current_task.artifacts = [current_temp]
                del self._artifact_chunks[artifact.artifactId][-1]
