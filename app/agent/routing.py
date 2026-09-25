import re
from dataclasses import dataclass

from app.models.chat import ModelDecision


_SECRET_VALUE = r'(?:"[^"\r\n]{1,500}"|\'[^\'\r\n]{1,500}\'|[^\s,;]{1,500})'
_AUTHORIZATION_VALUE = re.compile(
    rf"\bauthorization\s*[:=]\s*(?:bearer\s+)?{_SECRET_VALUE}", re.I
)
_BEARER_VALUE = re.compile(r"\bbearer\s+[A-Za-z0-9._~+/=-]{4,}", re.I)
_NAMED_SECRET_VALUE = re.compile(
    rf"\b(?:password|passphrase|api[_-]?key|token|access[_-]?token|"
    rf"refresh[_-]?token|client[_-]?secret|encryption[_-]?key|secret)\b"
    rf"\s*(?:=|:|\bis\b)\s*{_SECRET_VALUE}",
    re.I,
)
_EMBEDDED_SECRET_VALUE = re.compile(
    r"\b[A-Za-z0-9.]+[-_](?:secret|token)[-_][A-Za-z0-9._-]+\b", re.I
)


def sanitize_text(value: object, limit: int = 1000) -> str:
    text = " ".join(str(value or "").split())
    for pattern in (
        _AUTHORIZATION_VALUE,
        _BEARER_VALUE,
        _NAMED_SECRET_VALUE,
        _EMBEDDED_SECRET_VALUE,
    ):
        text = pattern.sub("[redacted]", text)
    return text[:limit]


def deterministic_action(message: str) -> str:
    value = message.casefold()
    if re.search(r"\b(?:send|email)\s+(?:the\s+)?draft\b|\bsend\s+draft\s+\d+\b", value):
        return "send_draft"
    if re.search(r"\b(?:draft|prepare|write)\b.{0,30}\b(?:outreach|email|message)\b", value):
        return "prepare_outreach"
    if re.search(r"\b(?:mark|change|update)\b.{0,40}\b(?:application|status|notes?)\b", value):
        return "update_application"
    if re.search(r"\b(?:find|search|show|recommend)\b.{0,80}\b(?:jobs?|roles?|vacancies|openings)\b|\bjob\s+search\b", value):
        return "search_jobs"
    if re.search(r"\b(?:save|track)\b.{0,30}\b(?:job|application)\b", value):
        return "save_application"
    if _is_role_query(value):
        return "search_jobs"
    return "career_advice"


_ROLE_NOUN = re.compile(
    r"\b(?:engineers?|developers?|analysts?|scientists?|designers?|interns?|internships?|trainees?|"
    r"testers?|architects?|researchers?|consultants?|associates?|executives?|specialists?|"
    r"administrators?|accountants?|recruiters?|writers?|managers?)\b"
)
_QUESTION = re.compile(
    r"\?|^\s*(?:what|how|why|when|where|which|who|should|can|could|would|is|are|do|does|tell|explain|help)\b"
)


def _is_role_query(value: str) -> bool:
    """A short role phrase such as "GenAI engineer fresher" is a search, not a question."""
    return len(value.split()) <= 10 and not _QUESTION.search(value) and bool(_ROLE_NOUN.search(value))


def is_ambiguous_followup(message: str, *, has_reference: bool = False) -> bool:
    return (not has_reference and bool(re.search(
        r"\b(?:that|this|it)\s+(?:one|job|role)\b|^\s*(?:what about|and)\s+(?:that|it)\b",
        message,
        re.I,
    )))


_UNSAFE_MODEL_REQUEST = re.compile(
    r"\b(?:ignore|override)\b.{0,40}\b(?:rules?|safeguards?|tools?|instructions?)\b|"
    r"\bbypass\b.{0,20}\b(?:approval|confirmation|safety)\b|"
    r"\b(?:reveal|show|expose|print)\b.{0,60}"
    r"\b(?:api[_ -]?keys?|oauth\s+tokens?|passwords?|secrets?|credentials?)\b",
    re.I,
)


def is_unsafe_model_request(message: object) -> bool:
    return bool(_UNSAFE_MODEL_REQUEST.search(str(message or "")))


@dataclass
class ModelAdvice:
    decision: ModelDecision | None = None
    explanation: str | None = None


def _capability(model, name: str) -> bool:
    capabilities = getattr(model, "capabilities", None)
    if isinstance(capabilities, dict):
        return bool(capabilities.get(name))
    return bool(getattr(capabilities, name, False))


async def validated_model_advice(model, context: dict) -> ModelAdvice:
    if is_unsafe_model_request(context.get("message")):
        return ModelAdvice()
    decision = None
    if _capability(model, "structured_output"):
        try:
            raw = await model.decide(context)
            decision = raw if isinstance(raw, ModelDecision) else ModelDecision.model_validate(raw)
        except Exception:
            decision = None
    explanation = None
    try:
        explanation = sanitize_text(await model.explain(context), 2000) or None
    except Exception:
        explanation = None
    return ModelAdvice(decision=decision, explanation=explanation)
