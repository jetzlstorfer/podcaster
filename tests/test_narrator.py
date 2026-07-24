"""Narrator (text-to-speech) tests.

These are integration tests: they call the Azure Speech REST API and write a
real MP3 to ``output/``. They require:
  - ``AZURE_SPEECH_ENDPOINT`` configured in ``.env``
  - ``az login`` (Entra ID auth) or ``AZURE_SPEECH_KEY`` set

If the Speech endpoint isn't configured the tests are skipped so the suite
still passes in environments without Azure access.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import requests

from podcaster import config
from podcaster.agents.narrator import run_narrator
from podcaster.models import DialogueTurn, PodcastScript

# Skip the whole module when audio output isn't configured.
pytestmark = pytest.mark.skipif(
    not config.AZURE_SPEECH_ENDPOINT,
    reason="AZURE_SPEECH_ENDPOINT not set; skipping audio integration tests.",
)


def _sample_script(title: str) -> PodcastScript:
    return PodcastScript(
        title=title,
        turns=[
            DialogueTurn(
                speaker=config.HOST_MALE,
                text="Welcome to the show. Today we test text to speech.",
            ),
            DialogueTurn(
                speaker=config.HOST_FEMALE,
                text="And we confirm the narrator produces a real MP3 file.",
            ),
        ],
    )


def _assert_valid_mp3(path: Path) -> None:
    assert path.exists(), f"expected an MP3 at {path}"
    data = path.read_bytes()
    assert len(data) > 1000, f"MP3 at {path} is suspiciously small ({len(data)} bytes)"
    # MP3 files start with an ID3 tag or an MPEG audio frame sync (0xFF).
    assert data[:3] == b"ID3" or data[0] == 0xFF, "file is not a valid MP3"


def _run_narrator_or_skip(*args, **kwargs) -> Path:
    """Run narrator integration call, skipping when Azure auth is unavailable.

    In shared/dev environments AZ CLI tokens or Speech role bindings may be
    missing, which produces HTTP 401/403 for otherwise healthy code paths.
    """
    try:
        return asyncio.run(run_narrator(*args, **kwargs))
    except RuntimeError as exc:
        if "AZURE_SPEECH_RESOURCE_ID is not set" in str(exc):
            pytest.skip("AZURE_SPEECH_RESOURCE_ID missing for Entra auth")
        raise
    except requests.exceptions.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else None
        if status in {401, 403}:
            pytest.skip(f"Azure Speech auth unavailable (HTTP {status})")
        raise


def test_narrator_generates_mp3(tmp_path):
    """The narrator synthesizes the default voices to a valid MP3."""
    script = _sample_script("Test Default Voices")
    path = _run_narrator_or_skip(script)
    _assert_valid_mp3(path)


@pytest.mark.parametrize("preset", list(config.VOICE_PRESETS))
def test_voice_preset_generates_mp3(preset):
    """Generate one MP3 per voice preset so audio quality can be compared.

    Outputs land in ``output/compare_<preset>.mp3``.
    """
    male, female = config.VOICE_PRESETS[preset]
    script = _sample_script(f"Voice comparison: {preset}")
    path = _run_narrator_or_skip(
        script,
        voice_male=male,
        voice_female=female,
        out_name=f"compare_{preset}",
    )
    _assert_valid_mp3(path)
    assert path.name == f"compare_{preset}.mp3"
