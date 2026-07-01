from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv(override=False)

FOUNDRY_PROJECT_ENDPOINT: str = os.environ.get(
    "FOUNDRY_PROJECT_ENDPOINT",
    "https://podcaster-resource.services.ai.azure.com/api/projects/podcaster",
)
FOUNDRY_MODEL: str = os.environ.get("FOUNDRY_MODEL", "gpt-5-mini")

# MAI-Voice-2 (Azure Speech)
AZURE_SPEECH_ENDPOINT: str = os.environ.get("AZURE_SPEECH_ENDPOINT", "")
AZURE_SPEECH_KEY: str = os.environ.get("AZURE_SPEECH_KEY", "")
# Full ARM account resource ID, required for Entra ID auth against the TTS REST API.
AZURE_SPEECH_RESOURCE_ID: str = os.environ.get("AZURE_SPEECH_RESOURCE_ID", "")
USE_SPEECH_ENTRA_AUTH: bool = (
    os.environ.get("USE_SPEECH_ENTRA_AUTH", "true").lower() == "true"
    or not AZURE_SPEECH_KEY
)

# Podcast hosts
PODCAST_VOICE_MALE: str = os.environ.get(
    "PODCAST_VOICE_MALE", "en-US-Ethan:MAI-Voice-2"
)
PODCAST_VOICE_FEMALE: str = os.environ.get(
    "PODCAST_VOICE_FEMALE", "en-US-Harper:MAI-Voice-2"
)
HOST_MALE: str = os.environ.get("HOST_MALE", "Alex")
HOST_FEMALE: str = os.environ.get("HOST_FEMALE", "Jordan")

# Voice-model presets for A/B quality comparison.
# Each label maps to a (male_voice, female_voice) pair. Used by the tests to
# synthesize one MP3 per preset so you can compare audio quality.
VOICE_PRESETS: dict[str, tuple[str, str]] = {
    "mai2": ("en-US-Ethan:MAI-Voice-2", "en-US-Harper:MAI-Voice-2"),
    "mai2-alt": ("en-US-Grant:MAI-Voice-2", "en-US-Olivia:MAI-Voice-2"),
    "neural": ("en-US-AndrewNeural", "en-US-AvaNeural"),
}

# Per-language settings: SSML locale + (male_voice, female_voice) pair.
# MAI-Voice-2 is English-only, so German falls back to multilingual neural voices.
LANGUAGE_VOICES: dict[str, tuple[str, str, str]] = {
    # language: (xml_lang, male_voice, female_voice)
    "english": ("en-US", "en-US-Ethan:MAI-Voice-2", "en-US-Harper:MAI-Voice-2"),
    "german": (
        "de-DE",
        "de-DE-FlorianMultilingualNeural",
        "de-DE-SeraphinaMultilingualNeural",
    ),
}

OUTPUT_DIR: str = "output"
