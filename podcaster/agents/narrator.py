from __future__ import annotations

import asyncio
import html
import logging
import re
import time
from pathlib import Path

import requests
from azure.identity import DefaultAzureCredential, get_bearer_token_provider

from podcaster import config
from podcaster.models import DialogueTurn, PodcastScript

logger = logging.getLogger(__name__)

# Lazy token provider — created once per process.
_token_provider = None

# Transient gateway statuses worth retrying (the MAI-Voice-2 upstream
# intermittently resets connections behind the Speech gateway).
_RETRY_STATUSES = {500, 502, 503, 504}
_MAX_ATTEMPTS = 4

# Per-request timeout (seconds). The endpoint buffers the whole clip before
# responding, so this must comfortably exceed the render time of one chunk.
_REQUEST_TIMEOUT = 180

# The Speech ``cognitiveservices/v1`` REST endpoint buffers the *entire* clip
# before responding, so a single large request can easily exceed the request
# timeout while the server renders several minutes of audio (MAI-Voice-2 is
# slow). Keeping each request small means every request returns well within
# ``_REQUEST_TIMEOUT``; long episodes are split into several requests and the
# MP3 output concatenated. At ~150 words/minute, 300 words is ~2 minutes of
# audio — comfortably under the timeout even on a slow render.
_MAX_WORDS_PER_REQUEST = 300


# Inline non-verbal performance cues the scriptwriter may embed in a turn's
# text, e.g. "That's wild! [laughs] I can't believe it." MAI-Voice-2 performs
# these natively (they are passed through verbatim); voices that can't perform
# them get a short pause instead, so the cue word is never read aloud. The value
# is the pause (in ms) used for that fallback path.
INLINE_CUES: dict[str, int] = {
    "laughs": 350,
    "chuckles": 300,
    "sighs": 400,
    "gasps": 300,
    "whispers": 200,
    "clears throat": 350,
    "breath": 300,
    "pause": 600,
}

_CUE_RE = re.compile(
    r"\[\s*(" + "|".join(re.escape(c) for c in INLINE_CUES) + r")\s*\]",
    re.IGNORECASE,
)

# Prosody fallback for voices that can't perform a native ``<mstts:express-as>``
# style (standard neural voices, the German multilingual voices, and
# ``en-US-Grant:MAI-Voice-2``). Prosody (rate/pitch/volume) is the broadly
# supported, low-risk lever across voice families; unsupported values are
# ignored rather than rejected. Keys match the MAI-native style names in
# ``DELIVERY_STYLES`` so the same style renders natively on MAI voices and as a
# rough approximation elsewhere.
_STYLE_PROSODY: dict[str, str] = {
    "happy": 'pitch="+4%"',
    "excited": 'rate="+7%" pitch="+7%"',
    "hopeful": 'pitch="+3%"',
    "joyful": 'pitch="+5%" rate="+3%"',
    "relieved": 'rate="-3%"',
    "determined": 'rate="+2%" pitch="-2%"',
    "confused": 'pitch="+3%" rate="-3%"',
    "sad": 'rate="-5%" pitch="-4%"',
    "whispering": 'volume="x-soft" rate="-3%"',
    "softvoice": 'volume="soft"',
}


def _get_token_provider():
    global _token_provider
    if _token_provider is None:
        _token_provider = get_bearer_token_provider(
            DefaultAzureCredential(),
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


def _render_cues(text: str, performs_cues: bool) -> str:
    """Turn a line's inline ``[cue]`` markers into escaped SSML.

    When ``performs_cues`` is True (MAI-Voice-2) the bracketed cue is kept in the
    text so the model acts it out. Otherwise it is replaced with a short
    ``<break>`` so the cue word is never spoken aloud.
    """
    parts: list[str] = []
    last = 0
    for m in _CUE_RE.finditer(text):
        parts.append(html.escape(text[last : m.start()]))
        cue = m.group(1).lower()
        if performs_cues:
            parts.append(html.escape(m.group(0)))
        else:
            parts.append(f'<break time="{INLINE_CUES.get(cue, 300)}ms"/>')
        last = m.end()
    parts.append(html.escape(text[last:]))
    return "".join(parts).strip()


def _render_turn(turn: DialogueTurn, voice: str, performs_cues: bool) -> str:
    """Render a single turn's text (cues + delivery style) to SSML markup.

    When ``voice`` natively supports the turn's delivery style (the MAI-Voice-2
    hosts), it is wrapped in ``<mstts:express-as>`` so the model performs the
    emotion. Otherwise the style is approximated with ``<prosody>``; ``neutral``
    and unknown styles emit no wrapper.
    """
    body = _render_cues(turn.text, performs_cues)
    style = turn.style
    if not style or style == "neutral":
        return body
    if style in config.voice_supported_styles(voice):
        return f'<mstts:express-as style="{style}">{body}</mstts:express-as>'
    prosody = _STYLE_PROSODY.get(style)
    if prosody:
        return f'<prosody {prosody}>{body}</prosody>'
    return body


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
        body = _render_turn(turn, voice, config.voice_performs_cues(voice))
        turns_xml += (
            f'\n  <voice name="{voice}">'
            f"\n    {body}"
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


async def synthesize_script(
    script: PodcastScript,
    *,
    voice_male: str | None = None,
    voice_female: str | None = None,
) -> bytes:
    """Synthesize a podcast script to MP3 bytes via the Azure Speech REST API.

    Pass ``voice_male`` / ``voice_female`` to override the configured voice
    models (useful for A/B quality comparison across voice models). The caller
    decides what to do with the bytes (write to disk locally, or upload to Blob
    in the hosted narrator).
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
    logger.info(
        "Synthesizing %d turns in %d request(s) (voices: %s / %s)",
        len(script.turns),
        len(chunks),
        male,
        female,
    )
    # ``_synthesize`` uses the blocking ``requests`` library. Run each request in
    # a worker thread so it never blocks the asyncio event loop — otherwise the
    # whole process (including Ctrl+C handling and any concurrent work) freezes
    # for the full duration of every synthesis request.
    parts: list[bytes] = []
    for turns in chunks:
        ssml = _build_ssml_for_turns(turns, xml_lang, male, female)
        parts.append(await asyncio.to_thread(_synthesize, url, ssml))
    return b"".join(parts)


async def run_narrator(
    script: PodcastScript,
    *,
    voice_male: str | None = None,
    voice_female: str | None = None,
    out_name: str | None = None,
) -> Path:
    """Synthesize a script to an MP3 file in ``OUTPUT_DIR`` and return its path.

    This is the local/in-process path (CLI, devui, tests). The hosted narrator
    uploads to Blob instead — see :func:`podcaster.storage.upload_bytes`.
    """
    audio = await synthesize_script(
        script, voice_male=voice_male, voice_female=voice_female
    )
    out_dir = Path(config.OUTPUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    filename = out_name or _safe_filename(script.title)
    out_path = out_dir / f"{filename}.mp3"
    out_path.write_bytes(audio)
    logger.info("Wrote %d bytes of audio to %s", len(audio), out_path)
    return out_path


def audio_blob_name(script: PodcastScript) -> str:
    """Deterministic ``.mp3`` blob name for a script (safe title slug)."""
    return f"{_safe_filename(script.title)}.mp3"


def _synthesize(url: str, ssml: str) -> bytes:
    """POST one SSML document to the Speech endpoint and return the MP3 bytes."""
    resp = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            resp = requests.post(
                url,
                headers=_headers(),
                data=ssml.encode("utf-8"),
                timeout=_REQUEST_TIMEOUT,
            )
        except requests.exceptions.RequestException as exc:
            if attempt == _MAX_ATTEMPTS:
                raise
            logger.warning(
                "Speech request %s (attempt %d/%d); retrying: %r",
                type(exc).__name__,
                attempt,
                _MAX_ATTEMPTS,
                exc,
            )
            time.sleep(2 * attempt)
            continue

        if resp.status_code in _RETRY_STATUSES and attempt < _MAX_ATTEMPTS:
            logger.warning(
                "Speech request returned %d (attempt %d/%d); retrying",
                resp.status_code,
                attempt,
                _MAX_ATTEMPTS,
            )
            time.sleep(2 * attempt)
            continue
        break

    resp.raise_for_status()
    return resp.content
