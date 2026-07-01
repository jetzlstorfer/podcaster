from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

Length = Literal["short", "medium", "long"]
Language = Literal["english", "german"]


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


class DialogueTurn(BaseModel):
    speaker: Literal["Alex", "Jordan"]
    text: str


class PodcastScript(BaseModel):
    title: str
    turns: list[DialogueTurn]
    language: Language = "english"
