"""Cover-art generation: art-director agent + MAI image model.

Two steps, mirroring the researcher/scriptwriter pattern:

1. An "art director" ``Agent`` (Foundry chat model) turns the podcast script
   into a single vivid text-to-image prompt.
2. That prompt is sent to a MAI-family image deployment (e.g.
   ``MAI-Image-2.5-Flash``) via the Azure OpenAI images REST API. The returned
   base64 PNG is written to ``output/<title>.png``.

The image deployment lives in the same Foundry project as the chat model, so it
reuses ``FOUNDRY_PROJECT_ENDPOINT`` (only the deployment name differs) and the
same keyless ``AzureCliCredential`` Entra auth.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import time
from pathlib import Path

import requests
from agent_framework import Agent
from azure.identity import AzureCliCredential, get_bearer_token_provider

from src.podcaster import config
from src.podcaster.agents._resilience import make_foundry_client, run_agent_resilient
from src.podcaster.agents.narrator import _safe_filename
from src.podcaster.models import PodcastScript

logger = logging.getLogger(__name__)

# Lazy token provider — created once per process. The Foundry account exposes
# the OpenAI-compatible images route and accepts the standard Cognitive Services
# scope (same scope the narrator uses for the Speech REST API).
_token_provider = None

# Transient statuses worth retrying. The MAI images endpoint enforces a low
# per-minute quota (429) and can hit gateway hiccups (5xx); both are retried
# with backoff. A 404 on the official route means a wrong deployment name, so it
# is surfaced immediately rather than retried.
_RETRY_STATUSES = {429, 500, 502, 503, 504}
_MAX_ATTEMPTS = 6
# Image renders buffer server-side before responding; keep a generous timeout.
_REQUEST_TIMEOUT = 120

_ART_DIRECTOR_INSTRUCTIONS = """\
You are an art director creating cover art for a podcast episode. Given the \
episode title and a short excerpt of the two-host dialogue, write ONE vivid, \
concrete text-to-image prompt for the episode's cover image.

Guidelines:
- Describe a single striking scene or visual metaphor that captures the topic.
- Specify subject, setting, composition, lighting, mood, and art style \
(e.g. editorial illustration, cinematic 3D render, flat vector, oil painting).
- Do NOT include any text, words, letters, logos, or captions in the image.
- No real people's faces or trademarked characters.
- Keep it to 1-3 sentences, suitable for a square 1:1 cover.

Respond with a single JSON object — no markdown, no extra text:
{"prompt": "<the image generation prompt>"}
"""


def _get_token_provider():
    global _token_provider
    if _token_provider is None:
        _token_provider = get_bearer_token_provider(
            AzureCliCredential(),
            "https://cognitiveservices.azure.com/.default",
        )
    return _token_provider


def _extract_json(text: str) -> str:
    """Strip markdown fences / prose and return the raw JSON object."""
    text = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
    if fenced:
        text = fenced.group(1)
    else:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start != -1 and end > start:
            text = text[start:end]
    return re.sub(r",(\s*[}\]])", r"\1", text)


def _build_prompt(script: PodcastScript) -> str:
    """Build the art-director input from the title and a dialogue excerpt."""
    excerpt = "\n".join(f"{t.speaker}: {t.text}" for t in script.turns[:12])
    return f"Episode title: {script.title}\n\nDialogue excerpt:\n{excerpt}"


async def run_art_director(script: PodcastScript) -> str:
    """Generate a text-to-image prompt from the podcast script."""

    def build(model: str | None, endpoint: str | None) -> Agent:
        return Agent(
            client=make_foundry_client(model, endpoint),
            instructions=_ART_DIRECTOR_INSTRUCTIONS,
        )

    result = await run_agent_resilient(build, _build_prompt(script))
    logger.debug("Art director raw response: %d chars", len(result.text or ""))
    try:
        data = json.loads(_extract_json(result.text))
        prompt = str(data.get("prompt", "")).strip()
    except (json.JSONDecodeError, AttributeError):
        # Fall back to the raw text if the model didn't return valid JSON.
        prompt = (result.text or "").strip()
    if not prompt:
        raise RuntimeError("Art director returned an empty image prompt.")
    return prompt


def _image_headers() -> dict[str, str]:
    token = _get_token_provider()()
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _image_url() -> str:
    """The official Microsoft-managed MAI image generations endpoint.

    Documented at
    https://learn.microsoft.com/azure/foundry/foundry-models/how-to/use-foundry-models-mai
    — it lives at the account root (not under ``/api/projects/<project>``) and is
    stable, unlike the ``/openai/v1`` compat route which intermittently 404s.
    """
    return f"{config.image_account_endpoint()}/mai/v1/images/generations"


def _image_dimensions() -> tuple[int, int]:
    """Parse ``IMAGE_SIZE`` (``"<width>x<height>"``) into a (width, height) pair.

    The MAI images API takes explicit ``width``/``height`` (each >= 768, product
    <= 1,048,576) rather than a single ``size`` string.
    """
    raw = config.IMAGE_SIZE.lower().replace(" ", "")
    try:
        width_str, height_str = raw.split("x", 1)
        return int(width_str), int(height_str)
    except (ValueError, AttributeError):
        return 1024, 1024


def _post_image(url: str, body: dict) -> requests.Response:
    """POST one image request with retry on transient gateway statuses."""
    resp = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            resp = requests.post(
                url,
                headers=_image_headers(),
                json=body,
                timeout=_REQUEST_TIMEOUT,
            )
        except requests.exceptions.RequestException as exc:
            if attempt == _MAX_ATTEMPTS:
                raise
            logger.warning(
                "Image request %s (attempt %d/%d); retrying: %r",
                type(exc).__name__,
                attempt,
                _MAX_ATTEMPTS,
                exc,
            )
            time.sleep(2 * attempt)
            continue

        if resp.status_code in _RETRY_STATUSES and attempt < _MAX_ATTEMPTS:
            # Honour Retry-After (image quota is a low per-minute limit), else
            # back off. Cap the wait so a stuck branch never blocks the fan-in.
            retry_after = resp.headers.get("retry-after") or resp.headers.get("Retry-After")
            try:
                wait = min(float(retry_after), 30.0) if retry_after else 2.0 * attempt
            except (TypeError, ValueError):
                wait = 2.0 * attempt
            logger.warning(
                "Image request returned %d (attempt %d/%d); retrying in %.0fs",
                resp.status_code,
                attempt,
                _MAX_ATTEMPTS,
                wait,
            )
            time.sleep(wait)
            continue
        break
    return resp


def _generate_image_sync(prompt: str, out_path: Path) -> Path:
    """Call the MAI image model and write the returned PNG to ``out_path``."""
    width, height = _image_dimensions()
    body = {
        "model": config.FOUNDRY_IMAGE_MODEL,
        "prompt": prompt,
        "width": width,
        "height": height,
    }
    url = _image_url()
    resp = _post_image(url, body)
    if resp is None:
        raise RuntimeError(f"No response from images endpoint {url}.")
    if resp.status_code != 200:
        # Surface the response body — the status line alone hides the cause
        # (invalid property, content filter, rate limit, …).
        raise RuntimeError(
            f"Image generation failed ({resp.status_code}) at {url}: {resp.text[:500]}"
        )
    data = resp.json()
    b64 = data["data"][0]["b64_json"]
    out_path.write_bytes(base64.b64decode(b64))
    logger.info("Wrote cover image to %s", out_path)
    return out_path


async def run_image_designer(
    script: PodcastScript,
    *,
    out_name: str | None = None,
) -> Path:
    """Design a prompt from the script and generate the episode cover art.

    Returns the path to the written PNG. Raises ``RuntimeError`` when the image
    model isn't configured so the caller can treat the step as optional.
    """
    if not config.FOUNDRY_IMAGE_MODEL:
        raise RuntimeError(
            "FOUNDRY_IMAGE_MODEL is not set. Deploy a MAI image model in your "
            "Foundry project and add its deployment name to your .env file."
        )

    prompt = await run_art_director(script)
    logger.info("[image] prompt: %s", prompt)

    out_dir = Path(config.OUTPUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    filename = out_name or _safe_filename(script.title)
    out_path = out_dir / f"{filename}.png"
    # The blocking ``requests`` call runs in a worker thread so it never stalls
    # the asyncio event loop (which is also driving the parallel narrate branch).
    return await asyncio.to_thread(_generate_image_sync, prompt, out_path)
