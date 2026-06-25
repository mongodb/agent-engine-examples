"""Atlas Admin Agent state."""

from __future__ import annotations

from typing import Annotated, Optional

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class AtlasAdminState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    today: Optional[str]
    atlas_org_id: Optional[str]
