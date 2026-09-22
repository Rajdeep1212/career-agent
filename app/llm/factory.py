from app.models.chat import ModelCapabilities


class DisabledChatModel:
    capabilities = ModelCapabilities()

    async def decide(self, _context):
        return None

    async def explain(self, _context):
        return None


def build_chat_model(
    provider: str | None = None,
    *,
    model: str | None = None,
    base_url: str | None = None,
    structured_output: bool | None = None,
    tool_calling: bool | None = None,
    timeout_seconds: float | None = None,
    api_key: str | None = None,
    _evaluation_access: object | None = None,
):
    from app.core.config import settings

    provider = (provider if provider is not None else settings.chat_model_provider).strip().casefold()
    if provider in ("", "none"):
        return DisabledChatModel()
    if provider == "ollama":
        from app.llm.ollama import OllamaChatModel
        return OllamaChatModel(
            model=model or settings.chat_model_name,
            base_url=base_url or settings.ollama_base_url,
            structured_output=(settings.chat_model_structured_output
                               if structured_output is None else structured_output),
            tool_calling=(settings.chat_model_tool_calling
                          if tool_calling is None else tool_calling),
            timeout_seconds=(settings.chat_model_timeout_seconds
                             if timeout_seconds is None else timeout_seconds),
        )
    if provider == "gemini":
        if base_url is not None:
            raise ValueError("Gemini uses a fixed Google API destination.")
        from app.llm.gemini import GeminiChatModel, has_synthetic_evaluation_access
        if not has_synthetic_evaluation_access(_evaluation_access):
            raise ValueError("Gemini is benchmark-only until hosted-model evaluation is complete.")
        return GeminiChatModel(
            model=model or settings.gemini_model_name,
            api_key=api_key or settings.gemini_api_key,
            structured_output=(settings.chat_model_structured_output
                               if structured_output is None else structured_output),
            tool_calling=(settings.chat_model_tool_calling
                          if tool_calling is None else tool_calling),
            timeout_seconds=(settings.chat_model_timeout_seconds
                             if timeout_seconds is None else timeout_seconds),
        )
    raise ValueError("Unsupported chat model provider.")
