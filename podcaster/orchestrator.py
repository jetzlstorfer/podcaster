"""Stage orchestration: run each pipeline stage in-process or via a hosted agent.

The workflow executors call :func:`research`, :func:`write_script`, and
:func:`narrate` here instead of talking to the agents directly. Each function
picks its execution path from configuration:

* **In-process** (default, local development) — builds a ``FoundryChatClient``
  agent and runs it in this process, exactly as before. Nothing needs to be
  deployed, so ``make run`` / ``make cli`` / tests keep working.
* **Hosted** — when the stage's ``*_AGENT_NAME`` is configured, the deployed
  Foundry hosted agent is invoked over the project endpoint with the backend's
  managed identity (``DefaultAzureCredential``). The agents have no public
  ingress; only an identity with the *Azure AI User* role can reach them.

This keeps the single-process local experience intact while letting the cloud
backend fan work out to the three private hosted agents.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path

from azure.identity import DefaultAzureCredential

from podcaster import config, storage
from podcaster.agents._resilience import InvalidModelResponse, run_agent_resilient
from podcaster.agents.narrator import (
    audio_blob_name,
    run_narrator,
    synthesize_script,
)
from podcaster.agents.researcher import parse_research_brief, run_researcher
from podcaster.agents.scriptwriter import parse_script, run_scriptwriter
from podcaster.models import PodcastRequest, PodcastScript, ResearchBrief

logger = logging.getLogger(__name__)


async def _invoke_hosted(
    agent_name: str,
    agent_version: str,
    prompt: str,
    *,
    validate_text: Callable[[str], object] | None = None,
) -> str:
    """Invoke a deployed Foundry hosted agent (with retry) and return its text.

    Reuses ``run_agent_resilient`` so hosted invocations get the same 429 /
    empty-response / transient-400 backoff as the in-process path. The model /
    endpoint fallback args are ignored — a hosted agent has a fixed identity.
    """
    from agent_framework.foundry import FoundryAgent

    def build(_model: str | None, _endpoint: str | None):
        return FoundryAgent(
            project_endpoint=config.FOUNDRY_PROJECT_ENDPOINT,
            agent_name=agent_name,
            agent_version=agent_version or None,
            credential=DefaultAzureCredential(),
            # Hosted agents can only be reached through their dedicated agent
            # endpoint; allow_preview routes the OpenAI client via
            # ``get_openai_client(agent_name=...)`` instead of hitting the
            # project-level responses endpoint (which rejects hosted agents).
            allow_preview=True,
        )

    def validate(result: object) -> None:
        if validate_text is None:
            return
        text = str(getattr(result, "text", "") or "")
        try:
            validate_text(text)
        except (TypeError, ValueError) as exc:
            raise InvalidModelResponse(
                f"Hosted agent {agent_name} returned invalid structured output"
            ) from exc

    result = await run_agent_resilient(build, prompt, validate_result=validate)
    return getattr(result, "text", "") or ""


async def research(request: PodcastRequest) -> ResearchBrief:
    """Produce a research brief — via the hosted researcher agent or in-process."""
    if config.RESEARCHER_AGENT_NAME:
        logger.info("[research] invoking hosted agent %s", config.RESEARCHER_AGENT_NAME)
        text = await _invoke_hosted(
            config.RESEARCHER_AGENT_NAME,
            config.RESEARCHER_AGENT_VERSION,
            request.model_dump_json(),
            validate_text=lambda text: parse_research_brief(text, request),
        )
        return parse_research_brief(text, request)
    return await run_researcher(request)


async def write_script(brief: ResearchBrief) -> PodcastScript:
    """Turn a brief into a script — via the hosted scriptwriter agent or in-process."""
    if config.SCRIPTWRITER_AGENT_NAME:
        logger.info(
            "[write_script] invoking hosted agent %s", config.SCRIPTWRITER_AGENT_NAME
        )
        text = await _invoke_hosted(
            config.SCRIPTWRITER_AGENT_NAME,
            config.SCRIPTWRITER_AGENT_VERSION,
            brief.model_dump_json(),
            validate_text=lambda text: parse_script(text, brief),
        )
        return parse_script(text, brief)
    return await run_scriptwriter(brief)


async def narrate(script: PodcastScript) -> str:
    """Synthesize audio and return an ``/audio/<name>`` reference (or skip message).

    Never raises for the "audio not configured" case — it returns a
    human-readable ``[Audio skipped: ...]`` string so the workflow's fan-in
    barrier is never blocked.
    """
    if config.NARRATOR_AGENT_NAME:
        logger.info("[narrate] invoking hosted agent %s", config.NARRATOR_AGENT_NAME)
        text = await _invoke_hosted(
            config.NARRATOR_AGENT_NAME,
            config.NARRATOR_AGENT_VERSION,
            script.model_dump_json(),
        )
        return _audio_ref_from_hosted(text)

    if not config.AZURE_SPEECH_ENDPOINT:
        return (
            "[Audio skipped: AZURE_SPEECH_ENDPOINT is not set — provision an "
            "Azure Speech resource that supports MAI-Voice-2.]"
        )

    audio = await synthesize_script(script)
    if storage.storage_configured():
        blob = audio_blob_name(script)
        import asyncio

        await asyncio.to_thread(storage.upload_bytes, audio, blob)
        return f"/audio/{blob}"

    # Local-file fallback (no storage account configured).
    path = await run_narrator(script)
    return f"/audio/{Path(path).name}"


def _audio_ref_from_hosted(text: str) -> str:
    """Map a hosted narrator's JSON result ({blob, container}) to an /audio ref."""
    stripped = (text or "").strip()
    try:
        data = json.loads(stripped)
    except (ValueError, TypeError):
        # The remote returned a plain skip/error string — surface it as-is.
        return stripped or "[Audio skipped: narrator returned no result]"
    blob = data.get("blob") or data.get("blob_name")
    if not blob:
        return stripped or "[Audio skipped: narrator returned no blob name]"
    return f"/audio/{blob}"
