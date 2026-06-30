from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class ResearchBrief(BaseModel):
    topic: str
    summary: str
    key_facts: list[str]
    sources: list[str]


class DialogueTurn(BaseModel):
    speaker: Literal["Alex", "Jordan"]
    text: str


class PodcastScript(BaseModel):
    title: str
    turns: list[DialogueTurn]
