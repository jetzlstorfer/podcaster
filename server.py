"""FastAPI AG-UI server for the podcaster pipeline.

Exposes the research → write_script → narrate workflow over the AG-UI protocol
so a web UI can stream per-stage progress and receive the final script + audio.

Run with:

    make web
    # or
    uvicorn server:app --host 127.0.0.1 --port 8089
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import AsyncIterator
from pathlib import Path

from agent_framework import Workflow
from agent_framework.ag_ui import (
    AgentFrameworkWorkflow,
    add_agent_framework_fastapi_endpoint,
)
from azure.core.exceptions import AzureError
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.responses import FileResponse, StreamingResponse

from podcaster import config, storage
from podcaster.observability import setup_observability
from podcaster.workflow import make_workflow

logger = logging.getLogger(__name__)

load_dotenv(override=False)

# Configure logging + optional OpenTelemetry as early as possible so startup and
# every request is traced.
setup_observability()

# AG-UI event types the workflow adapter emits that the @ag-ui/client SSE parser
# doesn't recognise (agent-framework extensions). We drop them so the browser
# client doesn't reject the stream; STEP_STARTED/STEP_FINISHED cover progress.
_DROPPED_EVENT_TYPES = (b'"ACTIVITY_SNAPSHOT"',)

# Origins allowed to call the AG-UI endpoint (the Vite dev server by default).
CORS_ORIGINS = [
    origin.strip()
    for origin in os.environ.get(
        "CORS_ORIGINS", "http://127.0.0.1:5173,http://localhost:5173"
    ).split(",")
    if origin.strip()
]

# Also allow any localhost/127.0.0.1 port and common forwarded-dev domains
# (VS Code tunnels, GitHub Codespaces, Gitpod) so the UI works when opened
# through a forwarded port rather than raw localhost. Override with
# CORS_ORIGIN_REGEX to restrict or extend this.
CORS_ORIGIN_REGEX = os.environ.get(
    "CORS_ORIGIN_REGEX",
    r"https?://(localhost|127\.0\.0\.1)(:\d+)?"
    r"|https://.*\.(app\.github\.dev|github\.dev|gitpod\.io|devtunnels\.ms)",
)

app = FastAPI(title="Podcaster AG-UI Server")

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_origin_regex=CORS_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def strip_unsupported_agui_events(request: Request, call_next):
    """Drop SSE events the browser AG-UI client can't parse (e.g. ACTIVITY_SNAPSHOT)."""
    response = await call_next(request)
    if request.url.path != "/podcast" or "text/event-stream" not in response.headers.get(
        "content-type", ""
    ):
        return response

    source = response.body_iterator

    async def filtered() -> AsyncIterator[bytes]:
        buffer = b""
        async for chunk in source:
            buffer += chunk if isinstance(chunk, bytes) else chunk.encode()
            while b"\n\n" in buffer:
                event, buffer = buffer.split(b"\n\n", 1)
                if not any(t in event for t in _DROPPED_EVENT_TYPES):
                    yield event + b"\n\n"
        if buffer and not any(t in buffer for t in _DROPPED_EVENT_TYPES):
            yield buffer

    return StreamingResponse(
        filtered(),
        status_code=response.status_code,
        headers={
            k: v for k, v in response.headers.items() if k.lower() != "content-length"
        },
        media_type=response.media_type,
    )


# Serve generated MP3s. In the cloud the narrator uploads each episode to a
# PRIVATE blob container, so /audio/<blob> streams it back through the backend's
# managed identity (Storage Blob Data Contributor). Locally (no storage account) it
# serves the file the in-process narrator wrote to OUTPUT_DIR.
_output_dir = Path(config.OUTPUT_DIR)
_output_dir.mkdir(parents=True, exist_ok=True)
# Only a bare `<name>.mp3` is accepted — no slashes — which blocks path
# traversal and blob-name injection.
_AUDIO_NAME_RE = re.compile(r"^[\w.\-]+\.mp3$")
_EPISODE_NAME_RE = re.compile(r"^[\w.\-]+\.json$")


def _resolve_audio_ref(base_name: str) -> str:
    blob_name = f"{base_name}.mp3"
    if storage.storage_configured():
        if storage.blob_exists(blob_name):
            return f"/audio/{blob_name}"
        return "[Audio not found for this episode]"
    path = _output_dir / blob_name
    if path.is_file():
        return f"/audio/{blob_name}"
    return "[Audio not found for this episode]"


def _resolve_image_ref(base_name: str) -> str | None:
    image_name = f"{base_name}.png"
    path = _output_dir / image_name
    if path.is_file():
        return f"/images/{image_name}"
    return None


def _episode_payload(data: dict, *, episode_id: str, updated: int) -> dict:
    turns = data.get("turns")
    script = turns if isinstance(turns, list) else []
    base_name = Path(episode_id).stem
    return {
        "id": episode_id,
        "title": data.get("title") or base_name,
        "language": data.get("language") or "english",
        "turns": len(script),
        "audio": _resolve_audio_ref(base_name),
        "image": _resolve_image_ref(base_name),
        "script": script,
        "updated": updated,
    }


def _episode_payload_from_path(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    return _episode_payload(data, episode_id=path.name, updated=int(path.stat().st_mtime))


def _episode_payload_from_blob(blob_name: str, updated: int) -> dict:
    data = json.loads(storage.download_bytes(blob_name).decode("utf-8"))
    return _episode_payload(data, episode_id=blob_name, updated=updated)


@app.get("/audio/{blob_name}")
async def get_audio(blob_name: str):
    if not _AUDIO_NAME_RE.match(blob_name):
        raise HTTPException(status_code=400, detail="Invalid audio name")
    if storage.storage_configured():
        if not storage.blob_exists(blob_name):
            raise HTTPException(status_code=404, detail="Audio not found")
        return StreamingResponse(
            storage.download_stream(blob_name), media_type="audio/mpeg"
        )
    path = _output_dir / blob_name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Audio not found")
    return FileResponse(path, media_type="audio/mpeg")


@app.get("/episodes")
async def list_episodes() -> list[dict]:
    summaries_by_id: dict[str, dict] = {}

    if storage.storage_configured():
        try:
            for blob in storage.list_blobs(suffix=".json"):
                try:
                    blob_name = str(blob["name"])
                    payload = _episode_payload_from_blob(
                        blob_name,
                        int(blob.get("updated") or 0),
                    )
                    summaries_by_id[payload["id"]] = {
                        "id": payload["id"],
                        "title": payload["title"],
                        "language": payload["language"],
                        "turns": payload["turns"],
                        "audio": payload["audio"],
                        "image": payload["image"],
                        "updated": payload["updated"],
                    }
                except (OSError, ValueError, TypeError, UnicodeDecodeError):
                    continue
        except (AzureError, OSError, ValueError, TypeError, UnicodeDecodeError) as exc:
            # Fall back to local files if blob listing is unavailable.
            logger.warning("Blob episode listing unavailable; falling back to local files: %s", exc)

    for path in _output_dir.glob("*.json"):
        try:
            payload = _episode_payload_from_path(path)
            summaries_by_id.setdefault(
                payload["id"],
                {
                    "id": payload["id"],
                    "title": payload["title"],
                    "language": payload["language"],
                    "turns": payload["turns"],
                    "audio": payload["audio"],
                    "image": payload["image"],
                    "updated": payload["updated"],
                },
            )
        except (OSError, ValueError, TypeError):
            # Ignore malformed or partial files while listing history.
            continue
    episodes = list(summaries_by_id.values())
    episodes.sort(key=lambda item: item["updated"], reverse=True)
    return episodes


@app.get("/episodes/{episode_name}")
async def get_episode(episode_name: str) -> dict:
    if not _EPISODE_NAME_RE.match(episode_name):
        raise HTTPException(status_code=400, detail="Invalid episode name")

    if storage.storage_configured() and storage.blob_exists(episode_name):
        try:
            payload = _episode_payload_from_blob(episode_name, updated=0)
            return {
                "title": payload["title"],
                "turns": payload["turns"],
                "language": payload["language"],
                "audio": payload["audio"],
                "image": payload["image"],
                "script": payload["script"],
            }
        except (OSError, ValueError, TypeError, UnicodeDecodeError):
            raise HTTPException(status_code=500, detail="Failed to read episode")

    path = _output_dir / episode_name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Episode not found")
    try:
        payload = _episode_payload_from_path(path)
    except (OSError, ValueError, TypeError):
        raise HTTPException(status_code=500, detail="Failed to read episode")
    return {
        "title": payload["title"],
        "turns": payload["turns"],
        "language": payload["language"],
        "audio": payload["audio"],
        "image": payload["image"],
        "script": payload["script"],
    }


@app.delete("/episodes/{episode_name}", status_code=204)
async def delete_episode(episode_name: str) -> Response:
    if not _EPISODE_NAME_RE.match(episode_name):
        raise HTTPException(status_code=400, detail="Invalid episode name")

    base_name = Path(episode_name).stem
    deleted = False
    try:
        if storage.storage_configured():
            prefix = f"{base_name}."
            blob_names = [
                str(blob["name"])
                for blob in storage.list_blobs()
                if "/" not in str(blob["name"])
                and str(blob["name"]).startswith(prefix)
            ]
            for blob_name in blob_names:
                storage.delete_blob(blob_name)
                deleted = True

        for path in _output_dir.glob(f"{base_name}.*"):
            if path.is_file():
                path.unlink()
                deleted = True
    except (AzureError, OSError, RuntimeError, TypeError, ValueError) as exc:
        logger.exception("Failed to delete episode %s", episode_name)
        raise HTTPException(status_code=500, detail="Failed to delete episode") from exc

    if not deleted:
        raise HTTPException(status_code=404, detail="Episode not found")
    return Response(status_code=204)


# Serve generated cover-art PNGs (e.g. /images/podcast.png). The image branch
# runs in-process in the backend, so its PNGs live on the container's disk.
app.mount("/images", StaticFiles(directory=str(_output_dir)), name="images")


class _PerRunWorkflow(AgentFrameworkWorkflow):
    """AG-UI wrapper that builds a fresh workflow for every run.

    The podcast pipeline is single-shot (one request -> one episode) and keeps
    no cross-turn state, but a graph ``Workflow`` is stateful *within* a run.
    The default wrapper reuses one instance per thread, so a run that fails
    partway leaves undrained in-flight messages and every later run aborts with
    "Cannot start a new run ... in-flight executor messages remain". Rebuilding
    the workflow per run isolates each request and removes that failure mode.
    """

    def _resolve_workflow(
        self, thread_id: str, snapshot_scope: str | None = None
    ) -> Workflow:
        return make_workflow()


# Stream the podcast workflow over AG-UI at POST /podcast.
add_agent_framework_fastapi_endpoint(app, _PerRunWorkflow(), "/podcast")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


# Serve the built React SPA (Vite `npm run build` output) at the site root, so a
# single Container App serves both the API and the UI (same origin). Mounted
# LAST so it never shadows /podcast, /audio, /images, or /healthz. Absent in
# local dev (the Vite dev server serves the UI on :5173) — the mount is skipped.
_spa_dir = Path(__file__).parent / "frontend" / "dist"
if _spa_dir.is_dir():
    app.mount("/", StaticFiles(directory=str(_spa_dir), html=True), name="spa")


def main() -> None:
    import uvicorn

    host = os.environ.get("WEB_HOST", "127.0.0.1")
    port = int(os.environ.get("WEB_PORT", "8089"))
    print(f"Podcaster AG-UI server running at http://{host}:{port}")
    print("AG-UI endpoint: POST /podcast")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
