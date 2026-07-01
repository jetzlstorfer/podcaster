from __future__ import annotations

import html
import re
import time
from pathlib import Path

import requests
from azure.identity import AzureCliCredential, get_bearer_token_provider

from src.podcaster import config
from src.podcaster.models import DialogueTurn, PodcastScript

# Lazy token provider — created once per process.
_token_provider = None

# Transient gateway statuses worth retrying (the MAI-Voice-2 upstream
# intermittently resets connections behind the Speech gateway).
_RETRY_STATUSES = {500, 502, 503, 504}
_MAX_ATTEMPTS = 4

# The Speech ``cognitiveservices/v1`` REST endpoint synthesizes at most ~10
# minutes of audio per request (and caps SSML payload size). Long episodes must
# be split into several requests and the MP3 output concatenated. At ~150
# words/minute, 1200 words is roughly 8 minutes — a safe margin under the cap.
_MAX_WORDS_PER_REQUEST = 1200


def _get_token_provider():
    global _token_provider
    if _token_provider is None:
        _token_provider = get_bearer_token_provider(
            AzureCliCredential(),
            "https://cognitiveservices.azure.com/.default",
        )
    return _token_provider


def _headers() -> dict[str, str]:
    h: dict[str, str] = {
        "Content-Type": "application/ssml+xml",
        "X-Microsoft-OutputFormat": "audio-24khz-160kbitrate-mono-mp3",
        "User-Agent": "podcaster-narrator/1.0",
    }
    if config.USE_SPEECH_ENTRA_AUTH:
        if not config.AZURE_SPEECH_RESOURCE_ID:
            raise RuntimeError(
                "AZURE_SPEECH_RESOURCE_ID is not set. Entra ID auth for the Speech "
                "TTS REST API requires the token in 'aad#<resourceId>#<token>' "
                "format. Add the account resource ID to your .env file."
            )
        token = _get_token_provider()()
        auth_token = f"aad#{config.AZURE_SPEECH_RESOURCE_ID}#{token}"
        h["Authorization"] = f"Bearer {auth_token}"
    else:
        h["Ocp-Apim-Subscription-Key"] = config.AZURE_SPEECH_KEY
    return h


def _build_ssml(
    script: PodcastScript,
    voice_male: str | None = None,
    voice_female: str | None = None,
) -> str:
    xml_lang, default_male, default_female = config.LANGUAGE_VOICES.get(
        script.language, config.LANGUAGE_VOICES["english"]
    )
    male = voice_male or default_male
    female = voice_female or default_female
    return _build_ssml_for_turns(script.turns, xml_lang, male, female)


def _build_ssml_for_turns(
    turns: list[DialogueTurn],
    xml_lang: str,
    male: str,
    female: str,
) -> str:
    voice_map = {
        config.HOST_MALE: male,
        config.HOST_FEMALE: female,
    }
    turns_xml = ""
    for turn in turns:
        voice = voice_map.get(turn.speaker, male)
        escaped = html.escape(turn.text)
        turns_xml += (
            f'\n  <voice name="{voice}">'
            f"\n    {escaped}"
            f'\n    <break time="400ms"/>'
            f"\n  </voice>"
        )
    return (
        '<speak version="1.0"'
        ' xmlns="http://www.w3.org/2001/10/synthesis"'
        ' xmlns:mstts="http://www.w3.org/2001/mstts"'
        f' xml:lang="{xml_lang}">'
        f"{turns_xml}\n</speak>"
    )


def _chunk_turns(
    turns: list[DialogueTurn], max_words: int = _MAX_WORDS_PER_REQUEST
) -> list[list[DialogueTurn]]:
    """Group consecutive turns so each chunk stays under ``max_words``.

    Keeps whole turns together (never splits a turn) so speaker boundaries and
    voices stay intact. A single oversized turn becomes its own chunk.
    """
    chunks: list[list[DialogueTurn]] = []
    current: list[DialogueTurn] = []
    current_words = 0
    for turn in turns:
        words = len(turn.text.split())
        if current and current_words + words > max_words:
            chunks.append(current)
            current = []
            current_words = 0
        current.append(turn)
        current_words += words
    if current:
        chunks.append(current)
    return chunks


def _safe_filename(title: str) -> str:
    name = re.sub(r"[^\w\s-]", "", title)
    name = re.sub(r"\s+", "_", name.strip())
    return name[:60] or "podcast"


async def run_narrator(
    script: PodcastScript,
    *,
    voice_male: str | None = None,
    voice_female: str | None = None,
    out_name: str | None = None,
) -> Path:
    """Synthesize a podcast script to MP3 via the Azure Speech REST API.

    Pass ``voice_male`` / ``voice_female`` to override the configured voice
    models (useful for A/B quality comparison across voice models). Pass
    ``out_name`` to control the output filename (without extension).
    """
    if not config.AZURE_SPEECH_ENDPOINT:
        raise RuntimeError(
            "AZURE_SPEECH_ENDPOINT is not set. "
            "Create an Azure Speech resource that supports MAI-Voice-2 "
            "and add it to your .env file."
        )

    xml_lang, default_male, default_female = config.LANGUAGE_VOICES.get(
        script.language, config.LANGUAGE_VOICES["english"]
    )
    male = voice_male or default_male
    female = voice_female or default_female
    url = config.AZURE_SPEECH_ENDPOINT.rstrip("/") + "/cognitiveservices/v1"

    # The v1 endpoint caps a single request at ~10 minutes of audio, so long
    # episodes are synthesized in chunks and the MP3 audio concatenated.
    chunks = _chunk_turns(script.turns)
    audio = b"".join(
        _synthesize(url, _build_ssml_for_turns(turns, xml_lang, male, female))
        for turns in chunks
    )

    out_dir = Path(config.OUTPUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    filename = out_name or _safe_filename(script.title)
    out_path = out_dir / f"{filename}.mp3"
    out_path.write_bytes(audio)
    return out_path


def _synthesize(url: str, ssml: str) -> bytes:
    """POST one SSML document to the Speech endpoint and return the MP3 bytes."""
    resp = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            resp = requests.post(
                url,
                headers=_headers(),
                data=ssml.encode("utf-8"),
                timeout=180,
            )
        except requests.exceptions.ConnectionError:
            if attempt == _MAX_ATTEMPTS:
                raise
            time.sleep(2 * attempt)
            continue

        if resp.status_code in _RETRY_STATUSES and attempt < _MAX_ATTEMPTS:
            time.sleep(2 * attempt)
            continue
        break

    resp.raise_for_status()
    return resp.content
