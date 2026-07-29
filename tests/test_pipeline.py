"""Unit tests for the length/language-aware pipeline logic.

These are pure-function tests: they do not call Azure and run in every
environment (unlike the audio integration tests in ``test_narrator.py``).
"""

from __future__ import annotations

from podcaster.agents.narrator import _build_ssml
from podcaster.agents.scriptwriter import _build_instructions
from podcaster.models import (
    DialogueTurn,
    PodcastRequest,
    PodcastScript,
    length_spec,
)
from podcaster.workflow import _parse_request, _text_from_messages


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
    from podcaster.agents._resilience import _is_rate_limit

    assert _is_rate_limit(Exception("Error code: 429 - rate_limit_exceeded"))
    assert not _is_rate_limit(Exception("some other failure"))


def test_transient_invalid_payload_is_retryable():
    from podcaster.agents._resilience import _is_transient_bad_request, _should_retry

    err = Exception(
        "Error code: 400 - {'error': {'code': 'invalid_payload', "
        "'message': 'Invalid request payload.'}}"
    )
    assert _is_transient_bad_request(err)
    assert _should_retry(err)
    assert not _is_transient_bad_request(Exception("some other failure"))


def test_run_agent_resilient_retries_then_succeeds():
    import asyncio

    from podcaster.agents import _resilience

    calls = {"n": 0}

    class _FakeResult:
        def __init__(self, text):
            self.text = text

    class _FakeAgent:
        async def run(self, prompt):
            calls["n"] += 1
            if calls["n"] < 3:
                raise Exception("Error code: 429 - rate_limit_exceeded")
            return _FakeResult(f"ok:{prompt}")

    async def _no_sleep(_seconds):
        return None

    result = asyncio.run(
        _resilience.run_agent_resilient(
            lambda model, endpoint: _FakeAgent(),
            "hello",
            sleep=_no_sleep,
        )
    )
    assert result.text == "ok:hello"
    assert calls["n"] == 3


def test_run_agent_resilient_retries_on_empty_response():
    import asyncio

    from podcaster.agents import _resilience

    calls = {"n": 0}

    class _FakeResult:
        def __init__(self, text):
            self.text = text

    class _FakeAgent:
        async def run(self, prompt):
            calls["n"] += 1
            # First two runs return an empty final message (reasoning-only),
            # which should be retried rather than surfaced as invalid output.
            return _FakeResult("" if calls["n"] < 3 else "done")

    async def _no_sleep(_seconds):
        return None

    result = asyncio.run(
        _resilience.run_agent_resilient(
            lambda model, endpoint: _FakeAgent(),
            "hello",
            sleep=_no_sleep,
        )
    )
    assert result.text == "done"
    assert calls["n"] == 3


def test_run_agent_resilient_retries_on_invalid_structured_response():
    import asyncio

    from podcaster.agents import _resilience

    calls = {"n": 0}

    class _FakeResult:
        def __init__(self, text):
            self.text = text

    class _FakeAgent:
        async def run(self, prompt):
            calls["n"] += 1
            return _FakeResult("not json" if calls["n"] < 3 else '{"ok": true}')

    def validate(result):
        import json

        try:
            json.loads(result.text)
        except ValueError as exc:
            raise _resilience.InvalidModelResponse("invalid JSON") from exc

    async def _no_sleep(_seconds):
        return None

    result = asyncio.run(
        _resilience.run_agent_resilient(
            lambda model, endpoint: _FakeAgent(),
            "hello",
            validate_result=validate,
            sleep=_no_sleep,
        )
    )
    assert result.text == '{"ok": true}'
    assert calls["n"] == 3


def test_run_agent_resilient_reraises_non_rate_limit():
    import asyncio

    from podcaster.agents import _resilience

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
    from podcaster.agents.narrator import _build_ssml_for_turns

    turns = [DialogueTurn(speaker="Alex", text="Wow. [laughs] Okay.")]
    ssml = _build_ssml_for_turns(turns, "en-US", "en-US-AndrewNeural", "en-US-AvaNeural")
    assert "[laughs]" not in ssml
    assert "<break" in ssml


def test_style_uses_express_as_for_mai_voice():
    """A native MAI emotion renders via <mstts:express-as> on the MAI hosts."""
    ssml = _build_ssml(_mai_script("This is huge news.", style="excited"))
    assert '<mstts:express-as style="excited">' in ssml
    assert "<prosody" not in ssml


def test_style_falls_back_to_prosody_for_neural_voice():
    """Voices without native style support approximate the emotion with prosody."""
    from podcaster.agents.narrator import _build_ssml_for_turns

    turns = [DialogueTurn(speaker="Alex", text="This is huge news.", style="excited")]
    ssml = _build_ssml_for_turns(turns, "en-US", "en-US-AndrewNeural", "en-US-AvaNeural")
    assert "<prosody" in ssml
    assert "express-as" not in ssml


def test_style_falls_back_for_mai_voice_without_styles():
    """en-US-Grant:MAI-Voice-2 ships no styles, so it must not emit express-as."""
    from podcaster.agents.narrator import _build_ssml_for_turns

    turns = [DialogueTurn(speaker="Alex", text="This is huge news.", style="excited")]
    ssml = _build_ssml_for_turns(
        turns, "en-US", "en-US-Grant:MAI-Voice-2", "en-US-Olivia:MAI-Voice-2"
    )
    assert "express-as" not in ssml
    assert "<prosody" in ssml


def test_neutral_style_has_no_style_markup():
    ssml = _build_ssml(_mai_script("Just a normal line."))
    assert "<prosody" not in ssml
    assert "express-as" not in ssml


def test_cue_text_is_escaped():
    """Ampersands in dialogue must be XML-escaped even alongside cues."""
    ssml = _build_ssml(_mai_script("Bread & butter [laughs] classics."))
    assert "&amp;" in ssml
    assert "Bread & butter" not in ssml


# ── Image designer (cover art) ────────────────────────────────────────────────


def test_image_account_endpoint_strips_project_suffix():
    """The images REST route lives at the account root, not the project path."""
    from podcaster import config

    original = config.FOUNDRY_PROJECT_ENDPOINT
    try:
        config.FOUNDRY_PROJECT_ENDPOINT = (
            "https://podcaster-resource.services.ai.azure.com/api/projects/podcaster"
        )
        assert (
            config.image_account_endpoint()
            == "https://podcaster-resource.services.ai.azure.com"
        )
    finally:
        config.FOUNDRY_PROJECT_ENDPOINT = original


def test_image_designer_build_prompt_includes_title_and_dialogue():
    from podcaster.agents.image_designer import _build_prompt

    script = PodcastScript(
        title="Quantum Leaps",
        turns=[
            DialogueTurn(speaker="Alex", text="Qubits are wild."),
            DialogueTurn(speaker="Jordan", text="Superposition explains it."),
        ],
    )
    prompt = _build_prompt(script)
    assert "Quantum Leaps" in prompt
    assert "Alex: Qubits are wild." in prompt
    assert "Jordan: Superposition explains it." in prompt


def test_image_designer_build_prompt_caps_turns():
    from podcaster.agents.image_designer import _build_prompt

    turns = [DialogueTurn(speaker="Alex", text=f"line {i}") for i in range(30)]
    prompt = _build_prompt(PodcastScript(title="Long", turns=turns))
    # Only the first 12 turns are included in the excerpt.
    assert "line 11" in prompt
    assert "line 12" not in prompt


def test_image_designer_extract_json_strips_fences():
    from podcaster.agents.image_designer import _extract_json

    raw = '```json\n{"prompt": "a bear in a forest",}\n```'
    assert _extract_json(raw) == '{"prompt": "a bear in a forest"}'


def test_image_url_uses_official_mai_route():
    from podcaster.agents.image_designer import _image_url

    url = _image_url()
    # The documented Microsoft-managed MAI images endpoint lives at the account
    # root, not under /api/projects/<project> or /openai/v1.
    assert url.endswith("/mai/v1/images/generations")
    assert "/api/projects/" not in url
    assert "/openai/" not in url


def test_image_dimensions_parse_size():
    from podcaster import config
    from podcaster.agents.image_designer import _image_dimensions

    original = config.IMAGE_SIZE
    try:
        config.IMAGE_SIZE = "1024x768"
        assert _image_dimensions() == (1024, 768)
        config.IMAGE_SIZE = "bogus"
        assert _image_dimensions() == (1024, 1024)
    finally:
        config.IMAGE_SIZE = original
