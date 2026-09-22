from abc import ABC, abstractmethod
from app.models.schemas import JobPosting


class JobProvider(ABC):
    name = "provider"
    @abstractmethod
    async def search(self, query: str, page: int = 1) -> list[JobPosting]:
        raise NotImplementedError


class ProviderError(RuntimeError):
    """Only safe, user-readable messages cross a provider boundary."""
