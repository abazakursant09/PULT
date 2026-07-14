"""Execution error taxonomy (RFC §5.5). Secret-safe: messages never carry tokens."""
from __future__ import annotations


class ExecutionError(Exception):
    """Raised inside the execution pipeline. `code` drives retry/handling."""

    # not retryable
    VALIDATION = "VALIDATION"
    NO_CONNECTION = "NO_CONNECTION"
    MISSING_SCOPE = "MISSING_SCOPE"
    AUTH = "AUTH"
    MARKETPLACE_4XX = "MARKETPLACE_4XX"
    UNKNOWN_ACTION = "UNKNOWN_ACTION"
    CAPABILITY_NOT_SUPPORTED = "CAPABILITY_NOT_SUPPORTED"  # marketplace can't do this action
    # guard rejections carry a GUARD_<reason> code, built at raise site
    # retryable
    RATE_LIMIT = "RATE_LIMIT"
    MARKETPLACE_5XX = "MARKETPLACE_5XX"
    TIMEOUT = "TIMEOUT"

    _RETRYABLE = {RATE_LIMIT, MARKETPLACE_5XX, TIMEOUT}

    # Ambiguous: the request may have reached the marketplace and committed, but no clean response
    # came back (a lost response, not a rejection). Such a write must NEVER be auto-repeated — a
    # retry could double-post. RATE_LIMIT is deliberately NOT here: a 429 means the request was
    # refused before processing, so it is a clean, safely-retryable failure.
    _AMBIGUOUS = {TIMEOUT, MARKETPLACE_5XX}

    def __init__(self, code: str, detail: str = "", *, retryable: bool | None = None):
        self.code = code
        self.detail = detail
        self.retryable = (
            retryable if retryable is not None else code in self._RETRYABLE
        )
        super().__init__(f"{code}: {detail}" if detail else code)

    @staticmethod
    def guard(reason: str, detail: str = "") -> "ExecutionError":
        return ExecutionError(f"GUARD_{reason}", detail, retryable=False)

    @classmethod
    def is_ambiguous_error(cls, code: str | None) -> bool:
        """A dispatch failure whose delivery is unknown — must not be auto-repeated."""
        return code in cls._AMBIGUOUS

    def to_dict(self) -> dict:
        return {"code": self.code, "detail": self.detail, "retryable": self.retryable}
