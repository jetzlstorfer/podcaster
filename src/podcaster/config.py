from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv(override=False)

FOUNDRY_PROJECT_ENDPOINT: str = os.environ.get(
    "FOUNDRY_PROJECT_ENDPOINT",
    "https://podcaster-resource.services.ai.azure.com/api/projects/podcaster",
)
FOUNDRY_MODEL: str = os.environ.get("FOUNDRY_MODEL", "gpt-5-mini")

# Optional failover deployment used when the primary model returns repeated
# rate-limit (429) errors. For this to add real capacity it must have
# *independent* quota — i.e. a deployment in a DIFFERENT region (set both the
# fallback endpoint and model) or a separate resource. A second deployment in
# the same region shares the same regional quota pool and won't help.
FOUNDRY_MODEL_FALLBACK: str = os.environ.get("FOUNDRY_MODEL_FALLBACK", "")
FOUNDRY_PROJECT_ENDPOINT_FALLBACK: str = os.environ.get(
    "FOUNDRY_PROJECT_ENDPOINT_FALLBACK", ""
)

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

# Native emotional styles each MAI-Voice-2 voice supports via
# ``<mstts:express-as>``. This is a fixed, per-voice enumeration: passing a
# style a voice does not support makes the Speech service drop the whole element
# (the line renders neutral), so the narrator validates against these sets
# before emitting a style tag. Voices absent from this map — standard neural
# voices, the German multilingual voices, and ``en-US-Grant:MAI-Voice-2`` (which
# ships no styles) — get an empty set and fall back to prosody/neutral.
_MAI_FULL_STYLES: frozenset[str] = frozenset(
    {
        "angry",
        "confused",
        "determined",
        "disgusted",
        "embarrassed",
        "excited",
        "fearful",
        "happy",
        "hopeful",
        "jealous",
        "joyful",
        "regretful",
        "relieved",
        "sad",
        "shouting",
        "softvoice",
        "surprised",
        "whispering",
    }
)
# Harper supports the full set minus these four.
_MAI_HARPER_STYLES: frozenset[str] = _MAI_FULL_STYLES - {
    "disgusted",
    "fearful",
    "jealous",
    "surprised",
}
VOICE_STYLE_SUPPORT: dict[str, frozenset[str]] = {
    "en-us-ethan:mai-voice-2": _MAI_FULL_STYLES,
    "en-us-olivia:mai-voice-2": _MAI_FULL_STYLES,
    "en-us-harper:mai-voice-2": _MAI_HARPER_STYLES,
}


def voice_supported_styles(voice: str) -> frozenset[str]:
    """Native ``<mstts:express-as>`` styles supported by ``voice``.

    Returns an empty set for any voice without a documented style list — the
    standard neural voices, the German multilingual voices, and
    ``en-US-Grant:MAI-Voice-2`` — which then fall back to prosody or neutral.
    """
    return VOICE_STYLE_SUPPORT.get(voice.lower(), frozenset())


OUTPUT_DIR: str = "output"

# Observability / logging
# ``LOG_LEVEL`` controls the app's console logging (DEBUG/INFO/WARNING/…).
LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "INFO").upper()
# Turn on OpenTelemetry instrumentation from the Microsoft Agent Framework
# (traces + metrics + logs for every agent/chat/workflow step). Off by default.
ENABLE_OTEL: bool = os.environ.get("ENABLE_OTEL", "false").lower() == "true"
# Print OTel spans/metrics to the console (handy for local debugging with no
# collector). Where the spans/metrics/logs are exported is otherwise driven by
# the standard ``OTEL_EXPORTER_OTLP_*`` environment variables.
OTEL_CONSOLE: bool = os.environ.get("ENABLE_CONSOLE_EXPORTERS", "false").lower() == "true"
# Emit OTel to the AI Toolkit / Azure AI Foundry VS Code extension when set.
_vscode_port = os.environ.get("VS_CODE_EXTENSION_PORT", "")
VS_CODE_EXTENSION_PORT: int | None = int(_vscode_port) if _vscode_port.isdigit() else None
# Optional Azure Monitor / Application Insights connection string. When set (and
# ``azure-monitor-opentelemetry`` is installed) telemetry is also sent there.
APPLICATIONINSIGHTS_CONNECTION_STRING: str = os.environ.get(
    "APPLICATIONINSIGHTS_CONNECTION_STRING", ""
)


def voice_performs_cues(voice: str) -> bool:
    """Whether a voice natively performs inline non-verbal cues in the text.

    Currently always ``False``. The ``cognitiveservices/v1`` REST endpoint does
    not accept verbatim bracketed cues (e.g. ``[gasps]``, ``[laughs]``) in the
    SSML text: MAI-Voice-2's upstream rejects them with an HTTP 502 "protocol
    error", and when such a cue sits inside a larger multi-turn request the whole
    synthesis stalls until the client read-timeout. So for every voice the
    narrator converts cues to short ``<break>`` pauses (see ``_render_cues``)
    rather than passing them through, which keeps the cue word from being read
    aloud and avoids the upstream failure.
    """
    return False
