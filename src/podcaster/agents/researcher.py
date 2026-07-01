from __future__ import annotations

import json
import re

from agent_framework import Agent
from agent_framework.foundry import FoundryChatClient
from azure.identity import AzureCliCredential

from src.podcaster.models import PodcastRequest, ResearchBrief

_INSTRUCTIONS = """\
You are a thorough research assistant. Given a research question or topic, use \
the web search tool to gather comprehensive, accurate, and up-to-date information.

Your goal is to produce a structured research brief that covers:
- A concise summary of the topic (2-4 sentences)
- 5-10 concrete key facts, statistics, or findings
- Important recent trends or developments
- The source URLs you used

Conduct multiple searches if needed to build a complete picture.

Respond with a single JSON object — no markdown, no extra text — using this schema:
{
  "topic": "<the topic>",
  "summary": "<concise overview>",
  "key_facts": ["<fact 1>", "<fact 2>", ...],
  "sources": ["<url 1>", "<url 2>", ...]
}
"""


def _extract_json(text: str) -> str:
    """Strip markdown code fences if present, then return raw JSON."""
    text = text.strip()
    # Remove ```json ... ``` or ``` ... ``` wrappers
    fenced = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
    if fenced:
        return fenced.group(1)
    # Fall back: find first { ... }
    start = text.find("{")
    end = text.rfind("}") + 1
    if start != -1 and end > start:
        return text[start:end]
    return text


async def run_researcher(request: PodcastRequest) -> ResearchBrief:
    # WebSearchTool is a MutableMapping, not a dict subclass; dict() converts it
    # to the plain {"type": "web_search"} dict that the agent framework expects.
    agent = Agent(
        client=FoundryChatClient(credential=AzureCliCredential()),
        instructions=_INSTRUCTIONS,
        tools=[dict(FoundryChatClient.get_web_search_tool())],
    )
    result = await agent.run(request.topic)
    data = json.loads(_extract_json(result.text))
    return ResearchBrief(
        **data,
        language=request.language,
        length=request.length,
    )
