"""Message helpers used by the data analyst graph nodes."""

from __future__ import annotations

import json
import uuid
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from data_analyst_agent.artifacts import Artifact, build_message_artifact_metadata
from data_analyst_agent.state import DataAnalystState


def tool_call(name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": f"{name}-{uuid.uuid4().hex[:10]}",
        "name": name,
        "args": args or {},
    }


def request_tools(*calls: dict[str, Any]) -> AIMessage:
    return AIMessage(content="", tool_calls=list(calls))


def assistant_with_artifacts(content: str, artifacts: list[Artifact]) -> AIMessage:
    return AIMessage(
        content=content,
        additional_kwargs=build_message_artifact_metadata(artifacts),
    )


def task_status_messages(
    flow_name: str,
    description: str,
    summary: str,
) -> list[BaseMessage]:
    call = tool_call(
        "task",
        {
            "subagent_type": flow_name,
            "description": description,
        },
    )
    return [
        request_tools(call),
        ToolMessage(
            content=summary,
            name="task",
            tool_call_id=call["id"],
        ),
    ]


def last_user_text(state: DataAnalystState) -> str:
    for message in reversed(state.get("messages", [])):
        if isinstance(message, HumanMessage):
            return str(message.content)
    return ""


def latest_tool_json(state: DataAnalystState, name: str) -> Any:
    for message in reversed(state.get("messages", [])):
        if isinstance(message, ToolMessage) and message.name == name:
            return json.loads(str(message.content))
    raise ValueError(f"Tool result not found for {name}")
