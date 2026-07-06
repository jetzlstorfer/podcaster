"""Image designer (cover art) integration test.

This is an integration test: it calls the Foundry chat model (art director) and
the MAI image deployment, then writes a real PNG to ``output/``. It requires:
  - ``FOUNDRY_PROJECT_ENDPOINT`` + ``FOUNDRY_IMAGE_MODEL`` configured
  - ``az login`` (keyless Entra ID auth)

Because ``FOUNDRY_IMAGE_MODEL`` has a default value, this test is opt-in: set
``RUN_IMAGE_INTEGRATION=1`` to run it. Otherwise it's skipped so the suite still
passes in environments without Azure access.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from podcaster.agents.image_designer import run_image_designer
from podcaster.models import DialogueTurn, PodcastScript

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_IMAGE_INTEGRATION") != "1",
    reason="RUN_IMAGE_INTEGRATION not set; skipping image integration test.",
)


def _sample_script() -> PodcastScript:
    return PodcastScript(
        title="Testing the Cover Art Generator",
        turns=[
            DialogueTurn(speaker="Alex", text="Today we generate a cover image."),
            DialogueTurn(speaker="Jordan", text="A single striking illustration."),
        ],
    )


def _assert_valid_png(path: Path) -> None:
    assert path.exists(), f"expected a PNG at {path}"
    data = path.read_bytes()
    assert len(data) > 1000, f"PNG at {path} is suspiciously small ({len(data)} bytes)"
    # PNG files start with the 8-byte signature 0x89 'P' 'N' 'G'.
    assert data[:4] == b"\x89PNG", "file is not a valid PNG"


def test_image_designer_generates_png():
    """The image designer writes a valid PNG cover for the episode."""
    import asyncio

    path = asyncio.run(run_image_designer(_sample_script()))
    _assert_valid_png(path)
