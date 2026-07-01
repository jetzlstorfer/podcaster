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
import random
from collections.abc import Awaitable, Callable
from typing import Any

from agent_framework import Agent
from azure.identity import AzureCliCredential

from src.podcaster import config

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

# A builder receives (model, project_endpoint) — ``None`` means "use the
# env-configured primary" — and returns a ready-to-run Agent.
AgentBuilder = Callable[[str | None, str | None], Agent]


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
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> Any:
    """Run ``agent.run(prompt)`` with backoff on 429s, then fail over.

    ``build_agent(model, endpoint)`` builds the agent; ``None`` selects the
    env-configured primary. Non-rate-limit errors are raised immediately.
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
        try:
            return await agent.run(prompt)
        except Exception as exc:  # noqa: BLE001 - re-raised unless it's a rate limit
            if not _is_rate_limit(exc) or attempt == max_attempts:
                raise
            wait = _retry_after_seconds(exc)
            if wait is None:
                wait = delay
                delay = min(delay * 2, _MAX_DELAY)
            await sleep(wait + random.uniform(0, 0.5))
