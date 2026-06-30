"""Graph-based podcast workflow.

The pipeline is modelled as three executors wired linearly:

    research  →  write_script  →  narrate

A graph ``Workflow`` (unlike the functional ``@workflow``) streams each
executor's start/finish events live, so devui shows progress per stage instead
of stalling until the whole pipeline completes.
"""

from __future__ import annotations

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
from src.podcaster.models import PodcastScript, ResearchBrief


class ResearchExecutor(Executor):
    """Search the web and produce a structured research brief."""

    @handler
    async def run(self, question: str, ctx: WorkflowContext[ResearchBrief]) -> None:
        brief = await run_researcher(question)
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
            audio = str(await run_narrator(script))
        except RuntimeError as exc:
            # Audio step is optional while the Speech resource isn't provisioned.
            audio = f"[Audio skipped: {exc}]"
        await ctx.yield_output(
            {
                "title": script.title,
                "turns": len(script.turns),
                "audio": audio,
                "script": [{"speaker": t.speaker, "text": t.text} for t in script.turns],
            }
        )


def make_workflow(name: str = "PodcastOrchestrator") -> Workflow:
    """Build the linear research → write_script → narrate workflow."""
    research = ResearchExecutor(id="research")
    write_script = ScriptExecutor(id="write_script")
    narrate = NarrateExecutor(id="narrate")
    return (
        WorkflowBuilder(name=name, start_executor=research)
        .add_edge(research, write_script)
        .add_edge(write_script, narrate)
        .build()
    )
