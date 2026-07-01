"""Graph-based podcast workflow.

The pipeline is modelled as executors wired linearly:

    parse  →  research  →  write_script  →  narrate

A graph ``Workflow`` (unlike the functional ``@workflow``) streams each
executor's start/finish events live, so devui shows progress per stage instead
of stalling until the whole pipeline completes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent_framework import (
    Executor,
    Workflow,
    WorkflowBuilder,
    WorkflowContext,
    handler,
)
from typing_extensions import Never

from src.podcaster.agents.narrator import run_narrator
from src.podcaster.agents.researcher import run_researcher
from src.podcaster.agents.scriptwriter import run_scriptwriter
from src.podcaster.models import PodcastRequest, PodcastScript, ResearchBrief


class ParseRequestExecutor(Executor):
    """Decode the incoming message into a structured ``PodcastRequest``.

    The web UI sends a JSON object ``{topic, length, language}``. For backwards
    compatibility (CLI / devui / plain chat), a non-JSON message is treated as a
    bare topic and the defaults are applied.
    """

    @handler
    async def run(self, message: str, ctx: WorkflowContext[PodcastRequest]) -> None:
        request = _parse_request(message)
        await ctx.send_message(request)

    @handler
    async def run_messages(
        self, messages: list, ctx: WorkflowContext[PodcastRequest]
    ) -> None:
        # The AG-UI adapter delivers the conversation as a list of chat messages.
        request = _parse_request(_text_from_messages(messages))
        await ctx.send_message(request)


class ResearchExecutor(Executor):
    """Search the web and produce a structured research brief."""

    @handler
    async def run(self, request: PodcastRequest, ctx: WorkflowContext[ResearchBrief]) -> None:
        brief = await run_researcher(request)
        await ctx.send_message(brief)


class ScriptExecutor(Executor):
    """Turn a research brief into a two-host podcast dialogue."""

    @handler
    async def run(self, brief: ResearchBrief, ctx: WorkflowContext[PodcastScript]) -> None:
        script = await run_scriptwriter(brief)
        await ctx.send_message(script)


class NarrateExecutor(Executor):
    """Synthesise the script to MP3 and emit the final result."""

    @handler
    async def run(
        self,
        script: PodcastScript,
        ctx: WorkflowContext[Never, dict[str, Any]],
    ) -> None:
        try:
            path = await run_narrator(script)
            # Served by the FastAPI static mount at /audio.
            audio = f"/audio/{Path(path).name}"
        except RuntimeError as exc:
            # Audio step is optional while the Speech resource isn't provisioned.
            audio = f"[Audio skipped: {exc}]"
        await ctx.yield_output(
            {
                "title": script.title,
                "turns": len(script.turns),
                "language": script.language,
                "audio": audio,
                "script": [{"speaker": t.speaker, "text": t.text} for t in script.turns],
            }
        )


def _parse_request(message: str) -> PodcastRequest:
    """Parse a JSON ``{topic, length, language}`` payload, or fall back to a topic."""
    text = (message or "").strip()
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except (ValueError, TypeError):
            return PodcastRequest(topic=text)
        if isinstance(data, dict) and data.get("topic"):
            return PodcastRequest.model_validate(data)
    return PodcastRequest(topic=text)


def _text_from_messages(messages: list[Any]) -> str:
    """Extract the latest user message text from a list of chat messages.

    Handles both agent-framework ``ChatMessage`` objects (``.role`` / ``.text``)
    and plain dicts (``{"role", "content"}``).
    """

    def role_of(m: Any) -> str:
        role = m.get("role") if isinstance(m, dict) else getattr(m, "role", None)
        return str(getattr(role, "value", role) or "").lower()

    def text_of(m: Any) -> str:
        if isinstance(m, dict):
            return str(m.get("content") or m.get("text") or "")
        return str(getattr(m, "text", "") or "")

    for m in reversed(messages):
        if role_of(m) == "user" and text_of(m):
            return text_of(m)
    return text_of(messages[-1]) if messages else ""



def make_workflow(name: str = "PodcastOrchestrator") -> Workflow:
    """Build the linear parse → research → write_script → narrate workflow."""
    parse = ParseRequestExecutor(id="parse")
    research = ResearchExecutor(id="research")
    write_script = ScriptExecutor(id="write_script")
    narrate = NarrateExecutor(id="narrate")
    return (
        WorkflowBuilder(name=name, start_executor=parse)
        .add_edge(parse, research)
        .add_edge(research, write_script)
        .add_edge(write_script, narrate)
        .build()
    )
