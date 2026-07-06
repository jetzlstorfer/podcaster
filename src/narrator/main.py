"""Hosted Foundry agent — Narrator (non-LLM code agent).

Unlike the researcher/scriptwriter, the narrator runs no model: it is a piece of
code hosted as a Foundry agent. It is exposed over the ``responses`` protocol as
a single-executor agent-framework *workflow* (``workflow.as_agent()``).

Given a ``PodcastScript`` JSON document it synthesizes the episode to MP3 via the
Azure Speech REST API and uploads the bytes to a **private** blob container, then
returns ``{"blob": "<name>", "container": "<container>"}`` as JSON. The backend
streams the blob back to the browser through its ``/audio/<blob>`` proxy.

Runtime env: ``AZURE_SPEECH_ENDPOINT``, ``AZURE_SPEECH_RESOURCE_ID``,
``USE_SPEECH_ENTRA_AUTH``, ``AZURE_STORAGE_ACCOUNT_URL``, ``AZURE_STORAGE_CONTAINER``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from agent_framework import (
    Executor,
    Message,
    WorkflowBuilder,
    WorkflowContext,
    handler,
)
from agent_framework_foundry_hosting import ResponsesHostServer
from dotenv import load_dotenv
from typing_extensions import Never

from podcaster import config, storage
from podcaster.agents.narrator import audio_blob_name, synthesize_script
from podcaster.models import PodcastScript
from podcaster.observability import setup_observability

load_dotenv()
logger = logging.getLogger(__name__)


def _extract_json(text: str) -> str:
    """Strip markdown fences if a caller wrapped the JSON, else return as-is."""
    text = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
    if fenced:
        return fenced.group(1).strip()
    start, end = text.find("{"), text.rfind("}") + 1
    return text[start:end] if start != -1 and end > start else text


def _text_from_messages(messages: list[Any]) -> str:
    def text_of(m: Any) -> str:
        if isinstance(m, dict):
            return str(m.get("content") or m.get("text") or "")
        return str(getattr(m, "text", "") or "")

    for m in reversed(messages):
        if text_of(m):
            return text_of(m)
    return ""


class NarrateExecutor(Executor):
    """Synthesize a script to MP3 and upload it to blob storage."""

    @handler
    async def run_text(self, message: str, ctx: WorkflowContext[Never, str]) -> None:
        await self._narrate(message, ctx)

    @handler
    async def run_messages(
        self, messages: list[Message], ctx: WorkflowContext[Never, str]
    ) -> None:
        await self._narrate(_text_from_messages(messages), ctx)

    async def _narrate(self, text: str, ctx: WorkflowContext[Never, str]) -> None:
        # Always yield *something*: an unhandled exception here would surface to
        # the caller as an empty (HTTP 200, no text) response, which the
        # orchestrator can only interpret as an "empty model response" and retry
        # up to six times before failing the whole run. Catching it and yielding
        # a human-readable ``[Audio skipped: ...]`` message fails fast with the
        # real reason and keeps the workflow's fan-in barrier unblocked.
        try:
            script = PodcastScript.model_validate_json(_extract_json(text))
            logger.info("[narrate] synthesizing %d turns", len(script.turns))
            audio = await synthesize_script(script)
            blob = audio_blob_name(script)
            await asyncio.to_thread(storage.upload_bytes, audio, blob)
            await ctx.yield_output(
                json.dumps({"blob": blob, "container": config.AZURE_STORAGE_CONTAINER})
            )
        except Exception as exc:  # noqa: BLE001 - surfaced as a skip message
            logger.exception("[narrate] failed")
            await ctx.yield_output(f"[Audio skipped: {type(exc).__name__}: {exc}]")


def build_agent():
    narrate = NarrateExecutor(id="narrate")
    workflow = WorkflowBuilder(name="narrator", start_executor=narrate).build()
    return workflow.as_agent(name="narrator")


def main() -> None:
    setup_observability()
    ResponsesHostServer(build_agent()).run()


if __name__ == "__main__":
    main()
