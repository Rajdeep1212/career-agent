"""Benchmark-only Gemini adapter with a strict synthetic-input boundary."""
import asyncio
import re
from typing import Any

import httpx

from app.agent.routing import sanitize_text
from app.models.chat import ModelCapabilities, ModelDecision


_GEMINI_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-3.8-flash:generateContent"
)
_SUPPORTED_MODEL = "gemini-3.8-flash"
_SYNTHETIC_MARKER_KEY = "_synthetic_evaluation"
_SYNTHETIC_MARKER = object()
_ALLOWED_CONTEXT_KEYS = {
    "message", "conversation_summary", "deterministic_action", "career_session_id",
    _SYNTHETIC_MARKER_KEY,
}
_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)


class HostedModelInputRejected(ValueError):
    pass


def mark_synthetic_evaluation(context: dict[str, Any]) -> dict[str, Any]:
    """Attach an in-process identity marker that JSON/browser input cannot forge."""
    return {**context, _SYNTHETIC_MARKER_KEY: _SYNTHETIC_MARKER}


def synthetic_evaluation_access() -> object:
    """Return the in-process capability used by the offline benchmark harness."""
    return _SYNTHETIC_MARKER


def has_synthetic_evaluation_access(value: object) -> bool:
    return value is _SYNTHETIC_MARKER


class GeminiChatModel:
    def __init__(
        self, *, model: str, api_key: str | None, structured_output=False,
        tool_calling=False, timeout_seconds=45.0, transport=None,
    ):
        if model != _SUPPORTED_MODEL:
            raise ValueError("Unsupported Gemini model.")
        if not api_key or not api_key.strip():
            raise ValueError("Gemini API key is not configured.")
        if tool_calling:
            raise ValueError("Gemini tool calling is disabled.")
        if not 0 < float(timeout_seconds) <= 300:
            raise ValueError("Chat model timeout must be between 0 and 300 seconds.")
        self.model = model
        self._api_key = api_key.strip()
        self._transport = transport
        self.timeout_seconds = float(timeout_seconds)
        self.capabilities = ModelCapabilities(
            structured_output=bool(structured_output), tool_calling=False,
        )

    @staticmethod
    def _safe_context(context: dict[str, Any]) -> dict[str, Any]:
        if context.get(_SYNTHETIC_MARKER_KEY) is not _SYNTHETIC_MARKER:
            raise HostedModelInputRejected("Hosted models require internal synthetic evaluation input.")
        unexpected = set(context) - _ALLOWED_CONTEXT_KEYS
        if unexpected:
            raise HostedModelInputRejected("Hosted-model context contains unsupported fields.")
        if context.get("career_session_id") not in (None, ""):
            raise HostedModelInputRejected("Hosted-model context cannot contain session identifiers.")

        safe: dict[str, str | None] = {}
        for key in ("message", "conversation_summary", "deterministic_action"):
            raw = str(context.get(key) or "")
            cleaned = sanitize_text(raw, 1000)
            if _EMAIL.search(raw) or "[redacted]" in cleaned:
                raise HostedModelInputRejected("Hosted-model context contains private data.")
            safe[key] = cleaned
        safe["career_session_id"] = None
        return safe

    async def _generate(self, prompt: str, *, schema: dict | None = None) -> str:
        generation_config: dict[str, Any] = {"temperature": 0}
        if schema is not None:
            generation_config.update({
                "responseMimeType": "application/json",
                "responseJsonSchema": schema,
            })
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": generation_config,
        }
        headers = {
            "content-type": "application/json",
            "x-goog-api-key": self._api_key,
        }
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                follow_redirects=False,
                transport=self._transport,
            ) as client:
                response = await asyncio.wait_for(
                    client.post(_GEMINI_ENDPOINT, headers=headers, json=payload),
                    timeout=self.timeout_seconds,
                )
        except asyncio.TimeoutError:
            raise
        except Exception as exc:
            raise RuntimeError("Gemini request failed.") from exc
        if response.status_code != 200:
            raise RuntimeError(f"Gemini request failed with status {response.status_code}.")
        try:
            data = response.json()
            parts = data["candidates"][0]["content"]["parts"]
            text = "".join(str(part.get("text") or "") for part in parts).strip()
        except Exception as exc:
            raise RuntimeError("Gemini returned an invalid response.") from exc
        if not text:
            raise RuntimeError("Gemini returned an empty response.")
        return text

    async def decide(self, context):
        if not self.capabilities.structured_output:
            return None
        safe_context = self._safe_context(context)
        prompt = (
            "Return a conservative career-assistant routing decision. Never request secrets and "
            "never infer side effects. Advisory roles do not change search scope.\nContext: "
            + str(safe_context)[:6000]
        )
        text = await self._generate(prompt, schema=ModelDecision.model_json_schema())
        return ModelDecision.model_validate_json(text)

    async def explain(self, context):
        safe_context = self._safe_context(context)
        text = await self._generate(
            "Give a concise career explanation using only the supplied safe facts. "
            + str(safe_context)[:6000]
        )
        return text or None
