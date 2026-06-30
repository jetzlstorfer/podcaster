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

from src.podcaster import config
from src.podcaster.agents.narrator import run_narrator
from src.podcaster.models import DialogueTurn, PodcastScript

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


def test_narrator_generates_mp3(tmp_path):
    """The narrator synthesizes the default voices to a valid MP3."""
    script = _sample_script("Test Default Voices")
    path = asyncio.run(run_narrator(script))
    _assert_valid_mp3(path)


@pytest.mark.parametrize("preset", list(config.VOICE_PRESETS))
def test_voice_preset_generates_mp3(preset):
    """Generate one MP3 per voice preset so audio quality can be compared.

    Outputs land in ``output/compare_<preset>.mp3``.
    """
    male, female = config.VOICE_PRESETS[preset]
    script = _sample_script(f"Voice comparison: {preset}")
    path = asyncio.run(
        run_narrator(
            script,
            voice_male=male,
            voice_female=female,
            out_name=f"compare_{preset}",
        )
    )
    _assert_valid_mp3(path)
    assert path.name == f"compare_{preset}.mp3"
