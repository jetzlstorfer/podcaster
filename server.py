"""FastAPI AG-UI server for the podcaster pipeline.

Exposes the research → write_script → narrate workflow over the AG-UI protocol
so a web UI can stream per-stage progress and receive the final script + audio.

Run with:

    make web
    # or
    uvicorn server:app --host 127.0.0.1 --port 8089
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

from agent_framework.ag_ui import add_agent_framework_fastapi_endpoint
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.responses import StreamingResponse

from src.podcaster import config
from src.podcaster.observability import setup_observability
from src.podcaster.workflow import make_workflow

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


# Serve generated MP3s so the browser can play them via the URL the workflow
# returns (e.g. /audio/podcast.mp3).
_output_dir = Path(config.OUTPUT_DIR)
_output_dir.mkdir(parents=True, exist_ok=True)
app.mount("/audio", StaticFiles(directory=str(_output_dir)), name="audio")

# Stream the podcast workflow over AG-UI at POST /podcast.
add_agent_framework_fastapi_endpoint(app, make_workflow(), "/podcast")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


def main() -> None:
    import uvicorn

    host = os.environ.get("WEB_HOST", "127.0.0.1")
    port = int(os.environ.get("WEB_PORT", "8089"))
    print(f"Podcaster AG-UI server running at http://{host}:{port}")
    print("AG-UI endpoint: POST /podcast")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
