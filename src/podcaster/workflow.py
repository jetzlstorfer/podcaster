"""Graph-based podcast workflow.

The pipeline is modelled as executors wired linearly:

    parse  →  research  →  write_script  →  narrate

A graph ``Workflow`` (unlike the functional ``@workflow``) streams each
executor's start/finish events live, so devui shows progress per stage instead
of stalling until the whole pipeline completes.
"""

from __future__ import annotations

import json
import logging
import time
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

from src.podcaster import config
from src.podcaster.agents.image_designer import run_image_designer
from src.podcaster.agents.narrator import _safe_filename, run_narrator
from src.podcaster.agents.researcher import run_researcher
from src.podcaster.agents.scriptwriter import run_scriptwriter
from src.podcaster.models import (
    ImageResult,
    NarrationResult,
    PodcastRequest,
    PodcastScript,
    ResearchBrief,
)

logger = logging.getLogger(__name__)


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
        logger.info(
            "[research] start topic=%r length=%s language=%s",
            request.topic,
            request.length,
            request.language,
        )
        started = time.perf_counter()
        brief = await run_researcher(request)
        logger.info(
            "[research] done in %.1fs — %d key facts, %d sources",
            time.perf_counter() - started,
            len(brief.key_facts),
            len(brief.sources),
        )
        await ctx.send_message(brief)


class ScriptExecutor(Executor):
    """Turn a research brief into a two-host podcast dialogue."""

    @handler
    async def run(self, brief: ResearchBrief, ctx: WorkflowContext[PodcastScript]) -> None:
        logger.info("[write_script] start topic=%r length=%s", brief.topic, brief.length)
        started = time.perf_counter()
        script = await run_scriptwriter(brief)
        script_path = _save_script(script)
        logger.info(
            "[write_script] done in %.1fs — %r, %d turns (saved to %s)",
            time.perf_counter() - started,
            script.title,
            len(script.turns),
            script_path,
        )
        await ctx.send_message(script)


class NarrateExecutor(Executor):
    """Synthesise the script to MP3 and forward the result to the join."""

    @handler
    async def run(
        self,
        script: PodcastScript,
        ctx: WorkflowContext[NarrationResult],
    ) -> None:
        logger.info("[narrate] start — synthesizing %d turns", len(script.turns))
        started = time.perf_counter()
        try:
            path = await run_narrator(script)
            # Served by the FastAPI static mount at /audio.
            audio = f"/audio/{Path(path).name}"
            logger.info(
                "[narrate] done in %.1fs — %s", time.perf_counter() - started, audio
            )
        except RuntimeError as exc:
            # Audio step is optional while the Speech resource isn't provisioned.
            audio = f"[Audio skipped: {exc}]"
            logger.warning("[narrate] skipped after %.1fs: %s", time.perf_counter() - started, exc)
        await ctx.send_message(
            NarrationResult(
                title=script.title,
                turns=len(script.turns),
                language=script.language,
                audio=audio,
                script=list(script.turns),
            )
        )


class ImageExecutor(Executor):
    """Generate episode cover art in parallel with narration."""

    @handler
    async def run(
        self,
        script: PodcastScript,
        ctx: WorkflowContext[ImageResult],
    ) -> None:
        logger.info("[generate_image] start — %r", script.title)
        started = time.perf_counter()
        try:
            path = await run_image_designer(script)
            # Served by the FastAPI static mount at /images.
            image = f"/images/{Path(path).name}"
            logger.info(
                "[generate_image] done in %.1fs — %s",
                time.perf_counter() - started,
                image,
            )
        except Exception as exc:  # noqa: BLE001 - image is optional; never block the join
            # Swallow every failure into a skip message. The fan-in barrier only
            # fires once BOTH branches complete, so this branch must not raise.
            image = f"[Image skipped: {exc}]"
            logger.warning(
                "[generate_image] skipped after %.1fs: %s",
                time.perf_counter() - started,
                exc,
            )
        await ctx.send_message(ImageResult(image=image))


class FinalizeExecutor(Executor):
    """Join the narrate + image branches and emit the final result."""

    @handler
    async def run(
        self,
        results: list[NarrationResult | ImageResult],
        ctx: WorkflowContext[Never, dict[str, Any]],
    ) -> None:
        narration = next((r for r in results if isinstance(r, NarrationResult)), None)
        image = next((r for r in results if isinstance(r, ImageResult)), None)
        if narration is None:
            raise RuntimeError("Finalize expected a narration result but got none.")
        await ctx.yield_output(
            {
                "title": narration.title,
                "turns": narration.turns,
                "language": narration.language,
                "audio": narration.audio,
                "image": image.image if image else "[Image skipped: no result]",
                "script": [
                    {"speaker": t.speaker, "text": t.text, "style": t.style}
                    for t in narration.script
                ],
            }
        )


def _save_script(script: PodcastScript) -> Path:
    """Persist the generated script to ``output/<title>.json``.

    The filename matches the narrator's MP3 naming so the script and its audio
    pair up, and the JSON can be replayed later via ``main.py --script``.
    """
    out_dir = Path(config.OUTPUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{_safe_filename(script.title)}.json"
    path.write_text(script.model_dump_json(indent=2), encoding="utf-8")
    return path


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
    """Build the parse → research → write_script → (narrate ∥ image) → finalize workflow.

    After the script is written the workflow fans out to two parallel branches —
    narration (MP3) and cover-art image — then fans in to a finalize step that
    merges both into the final result. The image branch swallows its own errors
    so a missing/failed image never blocks the join.
    """
    parse = ParseRequestExecutor(id="parse")
    research = ResearchExecutor(id="research")
    write_script = ScriptExecutor(id="write_script")
    narrate = NarrateExecutor(id="narrate")
    generate_image = ImageExecutor(id="generate_image")
    finalize = FinalizeExecutor(id="finalize")
    return (
        WorkflowBuilder(name=name, start_executor=parse)
        .add_edge(parse, research)
        .add_edge(research, write_script)
        .add_fan_out_edges(write_script, [narrate, generate_image])
        .add_fan_in_edges([narrate, generate_image], finalize)
        .build()
    )
