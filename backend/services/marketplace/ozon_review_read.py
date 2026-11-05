"""Additive Ozon review-v2 reads, not an unanswered/product import policy.

List row IDs are a local admission rule for detail requests; the uploaded list
schema does not expand nested rows. Info fields use the explicit info schema.
Budgets bound this invocation, not Ozon's default selection or snapshot scope.
The decoded-JSON byte budget applies AFTER the shared HTTP client has parsed a
response; it is not a streaming wire/memory-allocation limit.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
import json
import math
import re
from time import monotonic

from .base_client import BaseMarketplaceClient
from .errors import ExecutionError


class OzonReviewReadIncomplete(ExecutionError):
    """Fail closed without exposing response text, IDs, cursors or credentials."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(self.PAGINATION_INCOMPLETE,
                         f"ozon review read incomplete ({reason})", retryable=False)


def _invalid() -> None:
    raise ExecutionError(ExecutionError.VALIDATION, "invalid ozon review read options")


def _integer(value, bits: int) -> bool:
    return type(value) is int and -(2 ** (bits - 1)) <= value < 2 ** (bits - 1)


def _identifier(value) -> bool:
    # Local safety bound, not an Ozon UUID/length requirement. Never normalize IDs.
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 4096


@dataclass(frozen=True)
class ReviewReadLimits:
    max_pages: int = 20
    max_details: int = 1000
    max_attempts: int = 1020
    deadline_seconds: float = 30.0
    max_decoded_bytes: int = 8 * 1024 * 1024

    def __post_init__(self):
        for value in (self.max_pages, self.max_details, self.max_attempts, self.max_decoded_bytes):
            if type(value) is not int or value <= 0:
                _invalid()
        if type(self.deadline_seconds) not in (int, float) or self.deadline_seconds <= 0:
            _invalid()
        try:
            finite = math.isfinite(self.deadline_seconds)
        except OverflowError:
            _invalid()
        if not finite:
            _invalid()


@dataclass(frozen=True)
class ReviewPage:
    reviews: tuple[dict, ...]
    has_next: bool
    last_id: str


@dataclass(frozen=True)
class ReviewInfo:
    id: str
    sku: int
    published_at: str
    rating: int
    status: str
    text: str


class _Budget:
    def __init__(self, limits: ReviewReadLimits):
        if not isinstance(limits, ReviewReadLimits):
            _invalid()
        self.limits = limits
        self.deadline = monotonic() + limits.deadline_seconds
        self.attempts = 0
        self.details = 0
        self.decoded_bytes = 0

    def remaining(self) -> float:
        remaining = self.deadline - monotonic()
        if remaining <= 0:
            raise OzonReviewReadIncomplete("deadline")
        return remaining

    def consume(self, data) -> None:
        try:
            # A cumulative decoded-payload budget, not a raw socket size limit.
            for chunk in json.JSONEncoder(ensure_ascii=False, allow_nan=False,
                                          separators=(",", ":")).iterencode(data):
                self.decoded_bytes += len(chunk.encode("utf-8"))
                if self.decoded_bytes > self.limits.max_decoded_bytes:
                    raise OzonReviewReadIncomplete("byte_limit")
        except (TypeError, ValueError, RecursionError, UnicodeError):
            raise OzonReviewReadIncomplete("schema") from None
        self.remaining()


def _list_body(limit: int, sort_dir: str, last_id: str | None) -> dict:
    if type(limit) is not int or not 20 <= limit <= 100 or sort_dir not in ("ASC", "DESC"):
        _invalid()
    if last_id is not None and (not isinstance(last_id, str) or len(last_id) > 4096):
        _invalid()
    body = {"limit": limit, "sort_dir": sort_dir}
    if last_id is not None:  # None means omit; explicit empty string stays distinct.
        body["last_id"] = last_id
    return body


def _page(data, *, limit: int, previous: str | None) -> ReviewPage:
    if not isinstance(data, dict):
        raise OzonReviewReadIncomplete("schema")
    rows, more, cursor = data.get("reviews"), data.get("has_next"), data.get("last_id")
    if (not isinstance(rows, list) or len(rows) > limit or type(more) is not bool
            or not isinstance(cursor, str) or len(cursor) > 4096):
        raise OzonReviewReadIncomplete("schema")
    if any(not isinstance(row, dict) or not _identifier(row.get("id")) for row in rows):
        raise OzonReviewReadIncomplete("schema")
    if more and not _identifier(cursor):
        raise OzonReviewReadIncomplete("schema")
    if more and cursor == previous:
        raise OzonReviewReadIncomplete("cycle")
    return ReviewPage(tuple(dict(row) for row in rows), more, cursor)


_DATE_TIME = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt][0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]+)?(?:[Zz]|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])")


def _info(data, *, review_id: str, expected_sku: int | None) -> ReviewInfo:
    if not isinstance(data, dict):
        raise OzonReviewReadIncomplete("schema")
    if not _identifier(data.get("id")) or data["id"] != review_id:
        raise OzonReviewReadIncomplete("identity")
    sku, rating, status, published = (data.get(name) for name in
                                    ("sku", "rating", "status", "published_at"))
    if (not _integer(sku, 64) or not _integer(rating, 32)
            or not isinstance(status, str) or status not in ("NEW", "VIEWED", "PROCESSED")
            or not isinstance(data.get("text"), str)
            or not isinstance(published, str) or not _DATE_TIME.fullmatch(published)):
        raise OzonReviewReadIncomplete("schema")
    try:
        parsed = datetime.fromisoformat(published.upper().replace("Z", "+00:00"))
        if parsed.utcoffset() is None:
            raise ValueError
    except ValueError:
        raise OzonReviewReadIncomplete("schema") from None
    if expected_sku is not None and sku != expected_sku:
        raise OzonReviewReadIncomplete("identity")
    return ReviewInfo(data["id"], sku, published, rating, status, data["text"])


class OzonReviewReader:
    def __init__(self, base_url: str):
        # Separate from existing Seller writes/reads. Even throttle retries are
        # disabled: one budgeted call is exactly one underlying HTTP attempt.
        self._transport = BaseMarketplaceClient(base_url, max_retries=0)

    async def _request(self, path: str, *, token: str, headers: dict, body: dict,
                       budget: _Budget):
        remaining = budget.remaining()
        if budget.attempts >= budget.limits.max_attempts:
            raise OzonReviewReadIncomplete("attempt_limit")
        budget.attempts += 1
        try:
            data = await asyncio.wait_for(self._transport.request(
                "POST", path, token=token, auth_header="Api-Key",
                extra_headers=headers, json=body, retry_safe=True), timeout=remaining)
        except asyncio.TimeoutError:
            raise OzonReviewReadIncomplete("deadline") from None
        except (RecursionError, UnicodeError):
            raise OzonReviewReadIncomplete("schema") from None
        except ExecutionError as exc:
            # Shared 4xx details may include a response body. Preserve taxonomy,
            # not untrusted payloads, in this new read-only API's diagnostics.
            raise ExecutionError(exc.code, "ozon review read failed",
                                 retryable=exc.retryable) from None
        budget.consume(data)
        return data

    async def list_page(self, *, token: str, headers: dict, limit: int = 100,
                        sort_dir: str = "ASC", last_id: str | None = None,
                        limits: ReviewReadLimits | None = None) -> ReviewPage:
        body = _list_body(limit, sort_dir, last_id)
        budget = _Budget(limits if limits is not None else ReviewReadLimits())
        data = await self._request("/v2/review/list", token=token, headers=headers,
                                   body=body, budget=budget)
        page = _page(data, limit=limit, previous=last_id)
        budget.remaining()
        return page

    async def _detail(self, *, token: str, headers: dict, review_id: str,
                      expected_sku: int | None, budget: _Budget) -> ReviewInfo:
        if budget.details >= budget.limits.max_details:
            raise OzonReviewReadIncomplete("detail_limit")
        budget.details += 1
        data = await self._request("/v2/review/info", token=token, headers=headers,
                                   body={"review_id": review_id}, budget=budget)
        info = _info(data, review_id=review_id, expected_sku=expected_sku)
        budget.remaining()
        return info

    @staticmethod
    def _validate_identity(review_id: str, expected_sku: int | None) -> None:
        if not _identifier(review_id) or (expected_sku is not None and not _integer(expected_sku, 64)):
            _invalid()

    async def info(self, *, token: str, headers: dict, review_id: str,
                   expected_sku: int | None = None,
                   limits: ReviewReadLimits | None = None) -> ReviewInfo:
        self._validate_identity(review_id, expected_sku)
        return await self._detail(token=token, headers=headers, review_id=review_id,
                                  expected_sku=expected_sku,
                                  budget=_Budget(limits if limits is not None else ReviewReadLimits()))

    async def read_selection(self, *, token: str, headers: dict, limit: int = 100,
                             sort_dir: str = "ASC", expected_sku: int | None = None,
                             limits: ReviewReadLimits | None = None) -> tuple[ReviewInfo, ...]:
        """Buffer list IDs then hydrate; no successful partial result on failure.

        Completeness is ONLY for the returned selection. No unanswered filter,
        cabinet ownership, snapshot guarantee or business import is established.
        expected_sku asserts equality for EVERY detail, not a server-side filter.
        """
        _list_body(limit, sort_dir, None)
        if expected_sku is not None and not _integer(expected_sku, 64):
            _invalid()
        budget = _Budget(limits if limits is not None else ReviewReadLimits())
        seen: set[str] = set()
        ids: dict[str, None] = {}
        cursor = None
        for _ in range(budget.limits.max_pages):
            data = await self._request("/v2/review/list", token=token, headers=headers,
                body=_list_body(limit, sort_dir, cursor), budget=budget)
            page = _page(data, limit=limit, previous=cursor)
            for row in page.reviews:
                ids[row["id"]] = None
                if len(ids) > budget.limits.max_details:
                    raise OzonReviewReadIncomplete("detail_limit")
            if not page.has_next:
                break
            if page.last_id in seen:
                raise OzonReviewReadIncomplete("cycle")
            seen.add(page.last_id)
            cursor = page.last_id
        else:
            raise OzonReviewReadIncomplete("page_limit")
        result = []
        for review_id in ids:
            result.append(await self._detail(token=token, headers=headers,
                review_id=review_id, expected_sku=expected_sku, budget=budget))
        budget.remaining()
        return tuple(result)
