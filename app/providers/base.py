from abc import ABC, abstractmethod
from app.models.schemas import JobPosting


class JobProvider(ABC):
    name = "provider"
    @abstractmethod
    async def search(self, query: str, page: int = 1) -> list[JobPosting]:
        raise NotImplementedError


class ProviderError(RuntimeError):
    """Only safe, user-readable messages cross a provider boundary.

    ``reason`` is chosen by the adapter from the HTTP status, never copied from
    the provider's response body. ``reset_at`` is an ISO-8601 UTC time.
    """

    def __init__(self, message: str, *, status_code: int | None = None, reason: str | None = None,
                 reset_at: str | None = None, remaining: int | None = None, limit: int | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.reason = reason
        self.reset_at = reset_at
        self.remaining = remaining
        self.limit = limit
