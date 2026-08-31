"""Error taxonomy and RFC 9457 problem+json rendering.

Two rules shape this module:

1. Errors are classified, not stringified. Each class carries the HTTP
   status and problem type it maps to, so the API layer never re-derives a
   status code from a message.
2. No error may leak a search query. Detail strings are written by us and
   are safe to return and to log. Upstream exception text is captured for
   metrics as a category, never interpolated into a response.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

PROBLEM_CONTENT_TYPE = "application/problem+json"
HTTP_UNPROCESSABLE_ENTITY = 422
_PROBLEM_BASE = "https://veilix.dev/problems"


class VeilixError(Exception):
    """Base class for errors that map to a defined API response."""

    status_code: int = 500
    problem_type: str = "internal-error"
    title: str = "Internal Server Error"
    detail: str = "An unexpected error occurred."

    def __init__(self, detail: str | None = None, **extra: Any) -> None:
        self.detail = detail or self.detail
        self.extra = extra
        super().__init__(self.detail)

    def to_problem(self, request_id: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {
            "type": f"{_PROBLEM_BASE}/{self.problem_type}",
            "title": self.title,
            "status": self.status_code,
            "detail": self.detail,
        }
        if request_id:
            body["request_id"] = request_id
        body.update(self.extra)
        return body


# ---------------------------------------------------------------------------
# Client errors
# ---------------------------------------------------------------------------


class InvalidRequestError(VeilixError):
    status_code = 400
    problem_type = "invalid-request"
    title = "Invalid Request"
    detail = "The request parameters failed validation."


class AuthenticationRequiredError(VeilixError):
    status_code = 401
    problem_type = "authentication-required"
    title = "Authentication Required"
    detail = "This endpoint requires a valid credential."


class ForbiddenError(VeilixError):
    status_code = 403
    problem_type = "forbidden"
    title = "Forbidden"
    detail = "The supplied credential does not grant access to this resource."


class RateLimitedError(VeilixError):
    """Client exceeded its request budget.

    ``retry_after`` becomes both a response field and a ``Retry-After``
    header, so well-behaved clients back off without guessing.
    """

    status_code = 429
    problem_type = "rate-limited"
    title = "Too Many Requests"
    detail = "Request budget exhausted. Retry after the indicated interval."

    def __init__(self, retry_after: int, limit: int, detail: str | None = None) -> None:
        super().__init__(detail, retry_after=retry_after, limit=limit)
        self.retry_after = retry_after


# ---------------------------------------------------------------------------
# Upstream and dependency errors
# ---------------------------------------------------------------------------


class UpstreamError(VeilixError):
    """Base for failures originating below this service."""

    status_code = 502
    problem_type = "upstream-error"
    title = "Upstream Error"
    detail = "The search backend returned an unexpected response."


class UpstreamTimeoutError(UpstreamError):
    status_code = 504
    problem_type = "upstream-timeout"
    title = "Upstream Timeout"
    detail = "The search backend did not respond within the allotted time."


class UpstreamUnavailableError(UpstreamError):
    status_code = 503
    problem_type = "upstream-unavailable"
    title = "Search Temporarily Unavailable"
    detail = "The search backend is not reachable."


class CircuitOpenError(UpstreamError):
    """The breaker is open, so the call was refused without being attempted.

    Distinct from ``UpstreamUnavailableError`` on purpose: this is Veilix
    deliberately shedding load to let a struggling dependency recover, and
    conflating the two would hide that in the metrics.
    """

    status_code = 503
    problem_type = "circuit-open"
    title = "Search Temporarily Unavailable"
    detail = (
        "The search backend is failing and requests are being shed while it "
        "recovers. Retry shortly."
    )

    def __init__(self, retry_after: int, detail: str | None = None) -> None:
        super().__init__(detail, retry_after=retry_after)
        self.retry_after = retry_after


# ---------------------------------------------------------------------------
# Exception handlers
# ---------------------------------------------------------------------------


def _request_id(request: Request) -> str | None:
    value = getattr(request.state, "request_id", None)
    return str(value) if value else None


async def veilix_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Render a known error as problem+json."""
    assert isinstance(exc, VeilixError)  # noqa: S101 — handler is registered per type

    headers: dict[str, str] = {}
    retry_after = getattr(exc, "retry_after", None)
    if retry_after is not None:
        headers["Retry-After"] = str(int(retry_after))
    if exc.status_code == 401:
        # RFC 7235 requires a challenge on 401. fetch() ignores it, so the
        # in-app credential form is unaffected.
        headers["WWW-Authenticate"] = 'Basic realm="veilix-admin", charset="UTF-8"'

    return JSONResponse(
        status_code=exc.status_code,
        content=exc.to_problem(_request_id(request)),
        media_type=PROBLEM_CONTENT_TYPE,
        headers=headers,
    )


async def validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Render FastAPI validation failures as problem+json.

    Without this, 422s come back in FastAPI's own `{"detail": [...]}` shape
    while every other error is RFC 9457, so a client would need two error
    parsers for one API, and the one it needs is decided by which failure it
    happens to hit.

    The field errors are preserved under `errors`, because "which parameter was
    wrong" is exactly what the caller needs. They are safe to return: they name
    parameters and constraints, and Pydantic echoes the offending value, which
    the client sent us in the first place.
    """
    from fastapi.exceptions import RequestValidationError

    problem = InvalidRequestError()
    body = problem.to_problem(_request_id(request))
    # InvalidRequestError carries 400, but FastAPI answers validation failures
    # with 422 and RFC 9457 requires the member to match the response code. A
    # body claiming 400 inside a 422 makes a client trust one of the two.
    body["status"] = HTTP_UNPROCESSABLE_ENTITY

    if isinstance(exc, RequestValidationError):
        body["errors"] = [
            {
                "field": ".".join(str(p) for p in err.get("loc", ())[1:]),
                "message": err.get("msg", ""),
                "type": err.get("type", ""),
            }
            for err in exc.errors()
        ]

    return JSONResponse(
        status_code=HTTP_UNPROCESSABLE_ENTITY,
        content=body,
        media_type=PROBLEM_CONTENT_TYPE,
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Last-resort handler.

    Deliberately returns nothing about ``exc``. An unexpected exception raised
    while handling a search can easily carry the query in its message, and a
    stack trace can carry request state; either would put user data into a
    response body and into any client-side error reporting. The request ID is
    the bridge to the server-side log, which is where detail belongs.
    """
    return JSONResponse(
        status_code=500,
        content=VeilixError().to_problem(_request_id(request)),
        media_type=PROBLEM_CONTENT_TYPE,
    )
