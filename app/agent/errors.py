"""Chat-runtime errors, importable without loading LangGraph."""


class ThreadNotFoundError(ValueError):
    pass


class ResumeConflictError(ValueError):
    pass
