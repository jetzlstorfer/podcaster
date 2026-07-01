from __future__ import annotations

import json
import re

from agent_framework import Agent
from agent_framework.foundry import FoundryChatClient

from src.podcaster.agents._resilience import make_foundry_client, run_agent_resilient
from src.podcaster.models import PodcastRequest, ResearchBrief, length_spec

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
    data = json.loads(_extract_json(result.text))
    return ResearchBrief(
        **data,
        language=request.language,
        length=request.length,
    )
