"""Shared resilience helpers for the Foundry-backed agents.

The model deployment enforces per-minute rate limits (TPM/RPM). Longer episodes
send much larger prompts, so the researcher and scriptwriter now frequently trip
a 429 ``rate_limit_exceeded``. These 429s are almost always transient and clear
within seconds, so the primary fix is retry-with-backoff that honours the
``Retry-After`` header.

As a secondary layer, if a *fallback* deployment with independent quota is
configured (ideally in a different region), we switch to it after the primary
keeps failing.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from typing import Any

from agent_framework import Agent
from azure.identity import AzureCliCredential

from src.podcaster import config

logger = logging.getLogger(__name__)

try:  # FoundryChatClient is only importable when the agent framework is installed.
    from agent_framework.foundry import FoundryChatClient
except Exception:  # pragma: no cover - import guard for lint/test environments
    FoundryChatClient = None  # type: ignore[assignment]

# Number of attempts before switching to the fallback deployment (if configured).
_ATTEMPTS_BEFORE_FALLBACK = 3
_MAX_ATTEMPTS = 6
_BASE_DELAY = 2.0
_MAX_DELAY = 30.0

_RATE_LIMIT_MARKERS = ("rate_limit", "rate limit", "429", "too many requests")

# The Foundry Responses API intermittently rejects an otherwise-valid request
# with a 400 ``invalid_payload`` (empty ``details``, just a request id). These
# are transient server-side hiccups that clear on retry, so we treat them like
# rate limits and back off rather than failing the whole workflow.
_TRANSIENT_BAD_REQUEST_MARKERS = ("invalid_payload", "invalid request payload")

# A builder receives (model, project_endpoint) — ``None`` means "use the
# env-configured primary" — and returns a ready-to-run Agent.
AgentBuilder = Callable[[str | None, str | None], Agent]


class EmptyModelResponse(RuntimeError):
    """Raised when the model returns a run with no usable text.

    Reasoning models (e.g. gpt-5-mini) with the web-search tool occasionally
    spend their whole token budget on reasoning and emit an empty final
    message. That is a successful run as far as the transport is concerned, so
    it isn't caught by the rate-limit/bad-request handling — but it's just as
    transient, so we treat it as retryable.
    """


def make_foundry_client(
    model: str | None = None, project_endpoint: str | None = None
) -> Any:
    """Construct a ``FoundryChatClient``; ``None`` args fall back to env config."""
    kwargs: dict[str, Any] = {"credential": AzureCliCredential()}
    if model:
        kwargs["model"] = model
    if project_endpoint:
        kwargs["project_endpoint"] = project_endpoint
    return FoundryChatClient(**kwargs)


def _is_rate_limit(exc: BaseException) -> bool:
    message = str(exc).lower()
    if any(marker in message for marker in _RATE_LIMIT_MARKERS):
        return True
    # Walk the cause/context chain for an openai RateLimitError.
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if type(cur).__name__ == "RateLimitError" or getattr(cur, "status_code", None) == 429:
            return True
        cur = cur.__cause__ or cur.__context__
    return False


def _is_transient_bad_request(exc: BaseException) -> bool:
    """Detect the Foundry ``invalid_payload`` 400s that clear on retry."""
    message = str(exc).lower()
    return any(marker in message for marker in _TRANSIENT_BAD_REQUEST_MARKERS)


def _should_retry(exc: BaseException) -> bool:
    return (
        isinstance(exc, EmptyModelResponse)
        or _is_rate_limit(exc)
        or _is_transient_bad_request(exc)
    )


def _retry_after_seconds(exc: BaseException) -> float | None:
    """Best-effort extraction of the ``Retry-After`` header from the error chain."""
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        response = getattr(cur, "response", None)
        headers = getattr(response, "headers", None)
        if headers:
            value = headers.get("retry-after") or headers.get("Retry-After")
            if value:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    pass
        cur = cur.__cause__ or cur.__context__
    return None


async def run_agent_resilient(
    build_agent: AgentBuilder,
    prompt: str,
    *,
    max_attempts: int = _MAX_ATTEMPTS,
    require_text: bool = True,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> Any:
    """Run ``agent.run(prompt)`` with backoff on retryable errors, then fail over.

    Retryable = rate limits (429), the Foundry transient ``invalid_payload``
    400s, and — when ``require_text`` is set — runs that return an empty final
    message. ``build_agent(model, endpoint)`` builds the agent; ``None`` selects
    the env-configured primary. Other errors are raised immediately.
    """
    has_fallback = bool(
        config.FOUNDRY_MODEL_FALLBACK or config.FOUNDRY_PROJECT_ENDPOINT_FALLBACK
    )
    delay = _BASE_DELAY

    for attempt in range(1, max_attempts + 1):
        use_fallback = has_fallback and attempt > _ATTEMPTS_BEFORE_FALLBACK
        model = config.FOUNDRY_MODEL_FALLBACK or None if use_fallback else None
        endpoint = (
            config.FOUNDRY_PROJECT_ENDPOINT_FALLBACK or None if use_fallback else None
        )
        agent = build_agent(model, endpoint)
        if use_fallback:
            logger.warning(
                "Retry %d/%d using fallback deployment (model=%s endpoint=%s)",
                attempt,
                max_attempts,
                model or "<primary>",
                endpoint or "<primary>",
            )
        try:
            result = await agent.run(prompt)
            if require_text and not (getattr(result, "text", None) or "").strip():
                raise EmptyModelResponse(
                    "Model returned an empty response (no final text)"
                )
            return result
        except Exception as exc:  # noqa: BLE001 - re-raised unless it's retryable
            if not _should_retry(exc) or attempt == max_attempts:
                if attempt == max_attempts:
                    logger.error("Giving up after %d attempts: %s", attempt, exc)
                raise
            wait = _retry_after_seconds(exc)
            if wait is None:
                wait = delay
                delay = min(delay * 2, _MAX_DELAY)
            if isinstance(exc, EmptyModelResponse):
                reason = "empty response"
            elif _is_rate_limit(exc):
                reason = "rate limit"
            else:
                reason = "transient invalid_payload"
            total_wait = wait + random.uniform(0, 0.5)
            logger.warning(
                "Attempt %d/%d failed (%s); retrying in %.1fs",
                attempt,
                max_attempts,
                reason,
                total_wait,
            )
            await sleep(total_wait)
