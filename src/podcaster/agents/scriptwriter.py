from __future__ import annotations

import json
import re

from agent_framework import Agent

from src.podcaster.agents._resilience import make_foundry_client, run_agent_resilient
from src.podcaster.models import (
    DialogueTurn,
    Language,
    Length,
    PodcastScript,
    ResearchBrief,
    length_spec,
)

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

Target length — this is important, aim for {minutes} of audio:
- Write {words}
- Aim for {turn_target}
- When read aloud at a natural pace (~150 words per minute), the script should \
fill the target duration. Do NOT stop early — keep exploring the material until \
you reach the word budget, but never pad with filler or repetition.

Guidelines:
- Write the ENTIRE dialogue (title and every turn) in {language_name}
- Keep the speaker names exactly "Alex" and "Jordan" (do not translate them)
- Write conversational, natural dialogue — not formal or lecture-like
- Each turn should be 1-4 sentences
- Include a mix of explanations, reactions, follow-up questions, and insights
- For longer episodes, cover each sub-topic in the brief in depth, one at a \
time, with the hosts digging into examples, data, and differing perspectives
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
    spec = length_spec(length)
    return _INSTRUCTIONS_TEMPLATE.format(
        language_name=_LANGUAGE_NAMES.get(language, "English"),
        minutes=spec.minutes,
        words=spec.words,
        turn_target=spec.turns,
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
        text = fenced.group(1)
    else:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start != -1 and end > start:
            text = text[start:end]
    # Repair trailing commas (e.g. ``[..., ]`` or ``{..., }``), which language
    # models frequently emit and strict ``json.loads`` rejects. This is the most
    # common cause of decode failures on the larger scripts of long episodes.
    return re.sub(r",(\s*[}\]])", r"\1", text)


async def run_scriptwriter(brief: ResearchBrief) -> PodcastScript:
    def build(model: str | None, endpoint: str | None) -> Agent:
        return Agent(
            client=make_foundry_client(model, endpoint),
            instructions=_build_instructions(brief.language, brief.length),
        )

    result = await run_agent_resilient(build, _build_prompt(brief))
    data = json.loads(_extract_json(result.text))
    turns = [DialogueTurn(**t) for t in data["turns"]]
    return PodcastScript(title=data["title"], turns=turns, language=brief.language)
