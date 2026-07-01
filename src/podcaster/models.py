from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

Length = Literal["short", "medium", "long"]
Language = Literal["english", "german"]


@dataclass(frozen=True)
class LengthSpec:
    """Target depth/duration for a given podcast length.

    ``words`` is the primary driver: spoken conversational English runs at
    roughly 150 words/minute, so the word budget is what actually determines
    the audio duration. ``turns`` and ``facts`` keep the script and research
    depth in proportion so a "long" episode has enough material to fill the
    runtime without padding.
    """

    minutes: str
    words: str
    turns: str
    facts: str
    research_depth: str


# Keyed by ``Length``. Word budgets assume ~150 words/minute of spoken audio.
LENGTH_SPECS: dict[str, LengthSpec] = {
    "short": LengthSpec(
        minutes="about 5 minutes",
        words="roughly 700-800 words of spoken dialogue",
        turns="14-18 turns total",
        facts="5-7 concrete key facts",
        research_depth=(
            "Cover the essentials: the core definition, why it matters, and the "
            "2-3 most important points. Keep it focused."
        ),
    ),
    "medium": LengthSpec(
        minutes="about 10 minutes",
        words="roughly 1400-1600 words of spoken dialogue",
        turns="28-36 turns total",
        facts="10-14 concrete key facts",
        research_depth=(
            "Go beyond the basics: cover the main sub-topics, notable examples, "
            "recent developments, and a couple of contrasting viewpoints."
        ),
    ),
    "long": LengthSpec(
        minutes="about 30-40 minutes",
        words="roughly 4500-6000 words of spoken dialogue",
        turns="80-110 turns total",
        facts="20-30 concrete key facts",
        research_depth=(
            "Produce a deep, comprehensive briefing. Break the topic into several "
            "distinct sub-topics, and for each provide history/background, key "
            "details, concrete examples and data, recent developments, differing "
            "perspectives, and open questions or future outlook. There must be "
            "enough material to sustain a 30-40 minute conversation without repetition."
        ),
    ),
}


def length_spec(length: str) -> LengthSpec:
    """Return the spec for ``length``, falling back to medium."""
    return LENGTH_SPECS.get(length, LENGTH_SPECS["medium"])


class PodcastRequest(BaseModel):
    """User request that drives a single pipeline run."""

    topic: str
    length: Length = "medium"
    language: Language = "english"


class ResearchBrief(BaseModel):
    topic: str
    summary: str
    key_facts: list[str]
    sources: list[str]
    language: Language = "english"
    length: Length = "medium"


# Delivery styles the scriptwriter may attach to a turn to shape how it is
# performed. These are native MAI-Voice-2 emotion names, so the narrator can
# render them directly via ``<mstts:express-as>`` on the MAI hosts. The set is
# the intersection supported by both default hosts (Ethan and Harper), so either
# speaker can use any of them; "neutral" is the default and emits no style tag.
# For non-MAI voices the narrator approximates each with prosody.
DELIVERY_STYLES: tuple[str, ...] = (
    "neutral",
    "happy",
    "excited",
    "hopeful",
    "joyful",
    "relieved",
    "determined",
    "confused",
    "sad",
    "whispering",
    "softvoice",
)


class DialogueTurn(BaseModel):
    speaker: Literal["Alex", "Jordan"]
    text: str
    # How the line is delivered (see ``DELIVERY_STYLES``). Free-form so an
    # unexpected value never breaks parsing; the narrator falls back to neutral.
    style: str = "neutral"


class PodcastScript(BaseModel):
    title: str
    turns: list[DialogueTurn]
    language: Language = "english"


class NarrationResult(BaseModel):
    """Output of the narrate branch, consumed by the finalize join.

    Carries the full script payload (title/turns/language + per-turn lines) so
    the finalize executor can build the final result without a separate edge
    from the script stage. ``audio`` is either a ``/audio/...`` URL or a
    human-readable ``[Audio skipped: ...]`` message when synthesis was skipped.
    """

    title: str
    turns: int
    language: Language = "english"
    audio: str
    script: list[DialogueTurn]


class ImageResult(BaseModel):
    """Output of the parallel image branch, consumed by the finalize join.

    ``image`` is either an ``/images/...`` URL or a human-readable
    ``[Image skipped: ...]`` message when generation was skipped or failed.
    """

    image: str

