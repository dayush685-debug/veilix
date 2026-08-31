"""Structured logging.

This logger must not be able to record a search query, even in a stack trace.
Enforced by a processor on every event, not by convention.
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

# Replaced rather than dropped, so a redaction is visible in the output and
# whoever logged it can see it did not go through.
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
    """Redact privacy-sensitive keys (docs/privacy.md §4).

    A denylist, not an allowlist: an allowlist would swallow new operational
    fields and push detail into the free-text message instead.
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

    ``json_output=False`` selects a console renderer for local development.
    Redaction runs in both modes; a terminal is where screen-sharing happens.
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
    # output is one consistent stream instead of two interleaved formats.
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
