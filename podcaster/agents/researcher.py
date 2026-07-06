from __future__ import annotations

import json
import logging
import re

from agent_framework import Agent
from agent_framework.foundry import FoundryChatClient

from podcaster.agents._resilience import make_foundry_client, run_agent_resilient
from podcaster.models import LENGTH_SPECS, PodcastRequest, ResearchBrief, length_spec

logger = logging.getLogger(__name__)

_INSTRUCTIONS_TEMPLATE = """\
You are a thorough research assistant. Given a research question or topic, use \
the web search tool to gather comprehensive, accurate, and up-to-date information.

This research will be turned into a podcast episode of {minutes}, so calibrate \
the depth and breadth of your brief accordingly:
{research_depth}

Your goal is to produce a structured research brief that covers:
- A summary of the topic (2-4 sentences for a short episode, a full paragraph \
for a long one)
- {facts}, statistics, or findings
- Important recent trends or developments
- The source URLs you used

Conduct multiple searches if needed to build a complete picture.

Respond with a single JSON object — no markdown, no extra text — using this schema:
{{
  "topic": "<the topic>",
  "summary": "<overview>",
  "key_facts": ["<fact 1>", "<fact 2>", ...],
  "sources": ["<url 1>", "<url 2>", ...]
}}
"""


def _build_instructions(length: str) -> str:
    spec = length_spec(length)
    return _INSTRUCTIONS_TEMPLATE.format(
        minutes=spec.minutes,
        research_depth=spec.research_depth,
        facts=spec.facts,
    )


# Fixed, self-adapting instructions for the DEPLOYED hosted researcher agent.
# A hosted agent's instructions are frozen at deploy time, so (unlike the
# in-process path, which rebuilds instructions per request) these embed the
# calibration rubric for every length and tell the model to pick based on the
# ``length`` field of the incoming JSON request.
_HOSTED_INSTRUCTIONS_TEMPLATE = """\
You are a thorough research assistant for a podcast production pipeline. Use the \
web search tool to gather comprehensive, accurate, and up-to-date information.

The user message is a JSON object: {{"topic": "...", "length": "short|medium|\
long", "language": "..."}}. A bare (non-JSON) message is the topic itself; \
assume length "medium" in that case. Research the ``topic``.

Calibrate the depth and breadth of your brief to the requested ``length``:
{length_rubric}

Produce a structured research brief covering:
- A summary of the topic (2-4 sentences for short, a full paragraph for long)
- Concrete key facts, statistics, or findings (scale the count to the length)
- Important recent trends or developments
- The source URLs you used

Conduct multiple searches if needed to build a complete picture.

Respond with a single JSON object — no markdown, no extra text — using this schema:
{{
  "topic": "<the topic>",
  "summary": "<overview>",
  "key_facts": ["<fact 1>", "<fact 2>", ...],
  "sources": ["<url 1>", "<url 2>", ...]
}}
"""


def build_hosted_instructions() -> str:
    """Self-adapting instructions for the deployed hosted researcher agent."""
    rubric = "\n".join(
        f'- "{name}" ({spec.minutes}): {spec.research_depth} Aim for {spec.facts}.'
        for name, spec in LENGTH_SPECS.items()
    )
    return _HOSTED_INSTRUCTIONS_TEMPLATE.format(length_rubric=rubric)


def _extract_json(text: str) -> str:
    """Strip markdown code fences if present, then return raw JSON."""
    text = text.strip()
    # Remove ```json ... ``` or ``` ... ``` wrappers
    fenced = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
    if fenced:
        text = fenced.group(1)
    else:
        # Fall back: find first { ... }
        start = text.find("{")
        end = text.rfind("}") + 1
        if start != -1 and end > start:
            text = text[start:end]
    # Repair trailing commas (e.g. ``[..., ]`` or ``{..., }``), which language
    # models frequently emit and strict ``json.loads`` rejects. This is the most
    # common cause of decode failures on larger responses.
    return re.sub(r",(\s*[}\]])", r"\1", text)


def parse_research_brief(text: str, request: PodcastRequest) -> ResearchBrief:
    """Parse a researcher agent's raw JSON output into a ``ResearchBrief``.

    Shared by the in-process path and the backend's hosted-agent path so both
    apply the same JSON extraction and re-attach the request's language/length.
    """
    try:
        data = json.loads(_extract_json(text))
    except json.JSONDecodeError:
        logger.exception("Failed to parse researcher JSON response")
        raise
    data.pop("language", None)
    data.pop("length", None)
    return ResearchBrief(**data, language=request.language, length=request.length)


async def run_researcher(request: PodcastRequest) -> ResearchBrief:
    # WebSearchTool is a MutableMapping, not a dict subclass; dict() converts it
    # to the plain {"type": "web_search"} dict that the agent framework expects.
    def build(model: str | None, endpoint: str | None) -> Agent:
        return Agent(
            client=make_foundry_client(model, endpoint),
            instructions=_build_instructions(request.length),
            tools=[dict(FoundryChatClient.get_web_search_tool())],
        )

    result = await run_agent_resilient(build, request.topic)
    logger.debug("Researcher raw response: %d chars", len(result.text or ""))
    return parse_research_brief(result.text, request)
