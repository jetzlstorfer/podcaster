from __future__ import annotations

import json
import logging
import re

from agent_framework import Agent

from src.podcaster.agents._resilience import make_foundry_client, run_agent_resilient
from src.podcaster.agents.narrator import INLINE_CUES
from src.podcaster.models import (
    DELIVERY_STYLES,
    DialogueTurn,
    Language,
    Length,
    PodcastScript,
    ResearchBrief,
    length_spec,
)

logger = logging.getLogger(__name__)

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

Make it sound HUMAN and immersive. The hosts are real people reacting in real \
time, so let them:
- React emotionally — laugh at something surprising, express awe, hesitate, \
build on each other's energy
- Use natural interjections ("Oh, wow", "Right", "Ha, exactly", "Hmm")

You have two tools to shape the delivery:

1. A "style" on each turn — how the WHOLE line is delivered. Choose one of: \
{styles}. Use "neutral" for most lines; reach for the others only when the \
content genuinely calls for it (e.g. "excited" for a stunning stat, \
"thoughtful" for a tricky nuance, "whispering" for a conspiratorial aside).

2. Inline performance cues written INSIDE the text, in square brackets, exactly \
where they happen: {cues}. For example: \
"Wait — it doubled in a year? [laughs] That's insane." Use them sparingly and \
only when they fit the moment; never stack them or start every line with one.

Respond with a single JSON object — no markdown, no extra text:
{{
  "title": "<short episode title>",
  "turns": [
    {{"speaker": "Alex",   "style": "cheerful", "text": "..."}},
    {{"speaker": "Jordan", "style": "neutral",  "text": "... [laughs] ..."}}
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
        styles=", ".join(f'"{s}"' for s in DELIVERY_STYLES),
        cues=", ".join(f"[{c}]" for c in INLINE_CUES),
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
    logger.debug("Scriptwriter raw response: %d chars", len(result.text or ""))
    try:
        data = json.loads(_extract_json(result.text))
    except json.JSONDecodeError:
        logger.exception("Failed to parse scriptwriter JSON response")
        raise
    turns = [DialogueTurn(**t) for t in data["turns"]]
    return PodcastScript(title=data["title"], turns=turns, language=brief.language)
