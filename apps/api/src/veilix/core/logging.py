"""Structured logging.

The design constraint is unusual and worth stating: **this logger must not be
able to log a search query**, even by accident, even in a stack trace.

Discipline alone does not survive contact with a growing codebase, so the
privacy rule is enforced by a processor that runs on every event and drops
sensitive keys, rather than by asking developers to remember.
"""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar
from typing import Any

import structlog

# Bound per request, read by the processor below so that every line emitted
# while handling a request carries its ID without being passed around.
request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)

# Keys that must never reach a log sink. Anything matching is replaced rather
# than silently dropped, so a redaction is visible in the output and a
# developer who logs a query sees immediately that it did not go through.
_FORBIDDEN_KEYS = frozenset(
    {
        "q",
        "query",
        "search_query",
        "raw_query",
        "client_ip",
        "ip",
        "remote_addr",
        "x_forwarded_for",
        "user_agent",
        "referer",
        "referrer",
        "cookie",
        "cookies",
        "authorization",
        "api_key",
        "password",
        "secret",
        "token",
        "full_url",
        "query_string",
    }
)

_REDACTED = "<redacted>"


def _drop_sensitive(
    _logger: Any, _method: str, event_dict: structlog.types.EventDict
) -> structlog.types.EventDict:
    """Remove privacy-sensitive keys from every event.

    This is the enforcement point for docs/privacy.md §4. It is deliberately
    a denylist on key names rather than an allowlist, because an allowlist
    would silently swallow useful new operational fields and push developers
    towards stuffing detail into the free-text event message instead.
    """
    for key in list(event_dict):
        if key.lower() in _FORBIDDEN_KEYS:
            event_dict[key] = _REDACTED
    return event_dict


def _bind_request_id(
    _logger: Any, _method: str, event_dict: structlog.types.EventDict
) -> structlog.types.EventDict:
    rid = request_id_ctx.get()
    if rid is not None:
        event_dict.setdefault("request_id", rid)
    return event_dict


def configure_logging(level: str = "info", *, json_output: bool = True) -> None:
    """Install the logging configuration process-wide.

    ``json_output=False`` gives a human-readable console renderer for local
    development. The redaction processor runs in both modes — a developer's
    terminal is still a place a query should not appear, and it is the mode
    where someone is most likely to be screen-sharing.
    """
    log_level = getattr(logging, level.upper(), logging.INFO)

    shared: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        _bind_request_id,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        # Renders exc_info into a string. Combined with _drop_sensitive this
        # keeps tracebacks useful while keeping tagged values out of them.
        structlog.processors.format_exc_info,
        _drop_sensitive,
    ]

    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=True)
    )

    structlog.configure(
        processors=[*shared, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    # Route stdlib logging (uvicorn, httpx) through the same pipeline so the
    # output is one consistent stream rather than two interleaved formats.
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
        force=True,
    )
    for noisy in ("uvicorn.access", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(max(log_level, logging.WARNING))


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)  # type: ignore[no-any-return]
