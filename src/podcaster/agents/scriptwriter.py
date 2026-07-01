from __future__ import annotations

import json
import re

from agent_framework import Agent
from agent_framework.foundry import FoundryChatClient
from azure.identity import AzureCliCredential

from src.podcaster.models import DialogueTurn, Language, Length, PodcastScript, ResearchBrief

# Target dialogue length per requested podcast length.
_LENGTH_TURNS: dict[str, str] = {
    "short": "8-12 turns total",
    "medium": "14-20 turns total",
    "long": "26-34 turns total",
}

_LANGUAGE_NAMES: dict[str, str] = {
    "english": "English",
    "german": "German (Deutsch)",
}

_INSTRUCTIONS_TEMPLATE = """\
You are an expert podcast script writer. You craft engaging, natural-sounding \
conversations between two hosts:

- Alex (male): enthusiastic, curious, provides context and asks great questions
- Jordan (female): analytical, connects ideas, offers deeper insight and nuance

Given a research brief, write a complete podcast episode with:
1. A warm intro where both hosts introduce the topic to the listener
2. A natural back-and-forth discussion exploring the key points and facts
3. A concise outro where both hosts summarise the main takeaways

Guidelines:
- Write the ENTIRE dialogue (title and every turn) in {language_name}
- Keep the speaker names exactly "Alex" and "Jordan" (do not translate them)
- Write conversational, natural dialogue — not formal or lecture-like
- Each turn should be 1-4 sentences
- Include a mix of explanations, reactions, follow-up questions, and insights
- Aim for {turn_target}
- Make it engaging and accessible to a curious general audience

Respond with a single JSON object — no markdown, no extra text:
{{
  "title": "<short episode title>",
  "turns": [
    {{"speaker": "Alex",   "text": "..."}},
    {{"speaker": "Jordan", "text": "..."}}
  ]
}}
"""


def _build_instructions(language: Language, length: Length) -> str:
    return _INSTRUCTIONS_TEMPLATE.format(
        language_name=_LANGUAGE_NAMES.get(language, "English"),
        turn_target=_LENGTH_TURNS.get(length, _LENGTH_TURNS["medium"]),
    )


def _build_prompt(brief: ResearchBrief) -> str:
    facts = "\n".join(f"- {f}" for f in brief.key_facts)
    sources = ", ".join(brief.sources[:6]) if brief.sources else "N/A"
    return (
        f"Topic: {brief.topic}\n\n"
        f"Summary:\n{brief.summary}\n\n"
        f"Key Facts:\n{facts}\n\n"
        f"Sources: {sources}"
    )


def _extract_json(text: str) -> str:
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
    if fenced:
        return fenced.group(1)
    start = text.find("{")
    end = text.rfind("}") + 1
    if start != -1 and end > start:
        return text[start:end]
    return text


async def run_scriptwriter(brief: ResearchBrief) -> PodcastScript:
    agent = Agent(
        client=FoundryChatClient(credential=AzureCliCredential()),
        instructions=_build_instructions(brief.language, brief.length),
    )
    result = await agent.run(_build_prompt(brief))
    data = json.loads(_extract_json(result.text))
    turns = [DialogueTurn(**t) for t in data["turns"]]
    return PodcastScript(title=data["title"], turns=turns, language=brief.language)
