"""Unit tests for the length/language-aware pipeline logic.

These are pure-function tests: they do not call Azure and run in every
environment (unlike the audio integration tests in ``test_narrator.py``).
"""

from __future__ import annotations

from src.podcaster.agents.narrator import _build_ssml
from src.podcaster.agents.scriptwriter import _build_instructions
from src.podcaster.models import (
    DialogueTurn,
    PodcastRequest,
    PodcastScript,
    length_spec,
)
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
    assert length_spec("short").turns in short
    assert length_spec("long").turns in long
    assert length_spec("short").words in short
    assert length_spec("long").words in long
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


def test_is_rate_limit_detects_429():
    from src.podcaster.agents._resilience import _is_rate_limit

    assert _is_rate_limit(Exception("Error code: 429 - rate_limit_exceeded"))
    assert not _is_rate_limit(Exception("some other failure"))


def test_transient_invalid_payload_is_retryable():
    from src.podcaster.agents._resilience import _is_transient_bad_request, _should_retry

    err = Exception(
        "Error code: 400 - {'error': {'code': 'invalid_payload', "
        "'message': 'Invalid request payload.'}}"
    )
    assert _is_transient_bad_request(err)
    assert _should_retry(err)
    assert not _is_transient_bad_request(Exception("some other failure"))


def test_run_agent_resilient_retries_then_succeeds():
    import asyncio

    from src.podcaster.agents import _resilience

    calls = {"n": 0}

    class _FakeAgent:
        async def run(self, prompt):
            calls["n"] += 1
            if calls["n"] < 3:
                raise Exception("Error code: 429 - rate_limit_exceeded")
            return f"ok:{prompt}"

    async def _no_sleep(_seconds):
        return None

    result = asyncio.run(
        _resilience.run_agent_resilient(
            lambda model, endpoint: _FakeAgent(),
            "hello",
            sleep=_no_sleep,
        )
    )
    assert result == "ok:hello"
    assert calls["n"] == 3


def test_run_agent_resilient_reraises_non_rate_limit():
    import asyncio

    from src.podcaster.agents import _resilience

    class _FakeAgent:
        async def run(self, prompt):
            raise ValueError("bad request")

    async def _no_sleep(_seconds):
        return None

    try:
        asyncio.run(
            _resilience.run_agent_resilient(
                lambda model, endpoint: _FakeAgent(),
                "hello",
                sleep=_no_sleep,
            )
        )
    except ValueError as exc:
        assert "bad request" in str(exc)
    else:
        raise AssertionError("expected ValueError to propagate")


def _mai_script(text: str, style: str = "neutral") -> PodcastScript:
    return PodcastScript(
        title="T",
        turns=[DialogueTurn(speaker="Alex", text=text, style=style)],
        language="english",
    )


def test_inline_cue_stripped_to_break_for_mai_voice():
    """Bracketed cues must never be sent verbatim.

    The ``cognitiveservices/v1`` REST endpoint rejects verbatim cues (e.g.
    ``[laughs]``) for MAI-Voice-2 with an upstream 502, which stalls the whole
    request, so the narrator converts every cue to a short ``<break>`` pause.
    """
    ssml = _build_ssml(_mai_script("That's wild! [laughs] Amazing."))
    assert "[laughs]" not in ssml
    assert "<break" in ssml


def test_inline_cue_stripped_to_break_for_neural_voice():
    """Voices that can't perform cues get a pause instead of the cue word."""
    from src.podcaster.agents.narrator import _build_ssml_for_turns

    turns = [DialogueTurn(speaker="Alex", text="Wow. [laughs] Okay.")]
    ssml = _build_ssml_for_turns(turns, "en-US", "en-US-AndrewNeural", "en-US-AvaNeural")
    assert "[laughs]" not in ssml
    assert "<break" in ssml


def test_style_applies_prosody():
    ssml = _build_ssml(_mai_script("This is huge news.", style="excited"))
    assert "<prosody" in ssml
    assert 'rate="+7%"' in ssml


def test_neutral_style_has_no_prosody():
    ssml = _build_ssml(_mai_script("Just a normal line."))
    assert "<prosody" not in ssml


def test_cue_text_is_escaped():
    """Ampersands in dialogue must be XML-escaped even alongside cues."""
    ssml = _build_ssml(_mai_script("Bread & butter [laughs] classics."))
    assert "&amp;" in ssml
    assert "Bread & butter" not in ssml
