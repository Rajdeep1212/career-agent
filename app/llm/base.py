from typing import Any, Protocol

from app.models.chat import ModelCapabilities


class ChatModel(Protocol):
    capabilities: ModelCapabilities

    async def decide(self, context: dict[str, Any]): ...
    async def explain(self, context: dict[str, Any]) -> str | None: ...

