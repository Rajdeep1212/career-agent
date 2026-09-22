import asyncio
from urllib.parse import urlsplit

from langchain_ollama import ChatOllama

from app.models.chat import ModelCapabilities, ModelDecision


class OllamaChatModel:
    def __init__(
        self, *, model: str, base_url: str, structured_output=False,
        tool_calling=False, timeout_seconds=45.0,
    ):
        parts = urlsplit(base_url)
        if parts.scheme not in ("http", "https") or parts.hostname not in ("localhost", "127.0.0.1", "::1"):
            raise ValueError("Ollama must use a loopback URL.")
        self.capabilities = ModelCapabilities(
            structured_output=bool(structured_output),
            tool_calling=bool(tool_calling),
        )
        if not 0 < float(timeout_seconds) <= 300:
            raise ValueError("Chat model timeout must be between 0 and 300 seconds.")
        self.timeout_seconds = float(timeout_seconds)
        self._client = ChatOllama(model=model, base_url=base_url, temperature=0)

    async def _bounded(self, awaitable):
        return await asyncio.wait_for(awaitable, timeout=self.timeout_seconds)

    async def decide(self, context):
        if not self.capabilities.structured_output:
            return None
        prompt = (
            "Return a conservative career-assistant routing decision. Never request secrets and "
            "never infer side effects. Advisory roles do not change search scope.\nContext: "
            + str(context)[:6000]
        )
        return await self._bounded(
            self._client.with_structured_output(ModelDecision).ainvoke(prompt)
        )

    async def explain(self, context):
        response = await self._bounded(
            self._client.ainvoke(
                "Give a concise career explanation using only the supplied safe facts. "
                + str(context)[:6000]
            )
        )
        return str(response.content) if getattr(response, "content", None) else None
