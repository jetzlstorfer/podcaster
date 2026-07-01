"""Unit tests for the length/language-aware pipeline logic.

These are pure-function tests: they do not call Azure and run in every
environment (unlike the audio integration tests in ``test_narrator.py``).
"""

from __future__ import annotations

from src.podcaster.agents.narrator import _build_ssml
from src.podcaster.agents.scriptwriter import _LENGTH_TURNS, _build_instructions
from src.podcaster.models import DialogueTurn, PodcastRequest, PodcastScript
from src.podcaster.workflow import _parse_request, _text_from_messages


def test_parse_request_from_json():
    req = _parse_request('{"topic": "AI", "length": "short", "language": "german"}')
    assert isinstance(req, PodcastRequest)
    assert req.topic == "AI"
    assert req.length == "short"
    assert req.language == "german"


def test_parse_request_plain_text_defaults():
    req = _parse_request("The history of jazz")
    assert req.topic == "The history of jazz"
    assert req.length == "medium"
    assert req.language == "english"


def test_text_from_messages_dicts():
    messages = [
        {"role": "assistant", "content": "hi"},
        {"role": "user", "content": '{"topic": "AI"}'},
    ]
    assert _text_from_messages(messages) == '{"topic": "AI"}'



def test_build_instructions_varies_by_length():
    short = _build_instructions("english", "short")
    long = _build_instructions("english", "long")
    assert _LENGTH_TURNS["short"] in short
    assert _LENGTH_TURNS["long"] in long
    assert short != long


def test_build_instructions_uses_language_name():
    german = _build_instructions("german", "medium")
    assert "German" in german


def test_ssml_language_english():
    script = PodcastScript(
        title="EN",
        turns=[DialogueTurn(speaker="Alex", text="Hello")],
        language="english",
    )
    ssml = _build_ssml(script)
    assert 'xml:lang="en-US"' in ssml


def test_ssml_language_german():
    script = PodcastScript(
        title="DE",
        turns=[DialogueTurn(speaker="Alex", text="Hallo")],
        language="german",
    )
    ssml = _build_ssml(script)
    assert 'xml:lang="de-DE"' in ssml
    assert "de-DE-" in ssml  # a German voice was selected
