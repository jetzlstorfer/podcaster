# Podcaster – Copilot Instructions

## Commands

```bash
make install          # Install dependencies from requirements.txt
make run              # Start devui HTTP server on port 8088 (Agent Inspector)
make cli Q="..."      # Run pipeline once, print script to stdout
make test             # Run full test suite (pytest -v tests/)
make lint             # Ruff linter: ruff check podcaster/ src/ main.py server.py
make clean            # Remove __pycache__, output/*.mp3, vendored wheels, dist/build

# Run a single test
python -m pytest -v tests/test_narrator.py::test_narrator_generates_mp3
```

Tests require `AZURE_SPEECH_ENDPOINT` and `az login`; the suite auto-skips audio tests when that env var is not set.

## Architecture

Multi-stage graph workflow (`podcaster/workflow.py`, built by `make_workflow()`):

```
topic → ParseRequestExecutor → ResearchExecutor → ScriptExecutor → ┬→ NarrateExecutor  ┬→ FinalizeExecutor → output/<title>.{mp3,png}
                                                                  └→ ImageExecutor    ┘
```

Each stage is an `Executor` subclass wired via `WorkflowBuilder`. The graph workflow (not the functional `@workflow`) is used so devui streams per-stage progress events. `ParseRequestExecutor` decodes the incoming `{topic, length, language}` JSON (or a bare topic) into a `PodcastRequest`. After the script is written the graph **fans out** to two parallel branches (narration + cover art) via `add_fan_out_edges`, then **fans in** to `FinalizeExecutor` via `add_fan_in_edges` (which receives a `list` of both branch results once both complete).

Executors call `podcaster/orchestrator.py` (`research` / `write_script` / `narrate`), which runs each stage **in-process** by default or invokes a **deployed Foundry hosted agent** when the stage's `*_AGENT_NAME` env var is set.

- **Researcher** (`podcaster/agents/researcher.py`): `Agent` + Foundry native web-search tool → returns `ResearchBrief`
- **Scriptwriter** (`podcaster/agents/scriptwriter.py`): `Agent` (no tools) → structured JSON → `PodcastScript`
- **Narrator** (`podcaster/agents/narrator.py`): Calls Azure Speech REST API directly (no agent framework) → writes MP3 to `output/` (or uploads to blob when `AZURE_STORAGE_ACCOUNT_URL` is set)
- **Image designer** (`podcaster/agents/image_designer.py`): art-director `Agent` writes a text-to-image prompt → MAI image model (`FOUNDRY_IMAGE_MODEL`, same Foundry project) via the documented **MAI images REST API** (`POST {account}/mai/v1/images/generations`, body `{model, prompt, width, height}`, always returns PNG) → writes PNG to `output/`

The narrator is **optional** — if `AZURE_SPEECH_ENDPOINT` is not configured the pipeline still runs and the audio step emits a skip message instead of raising. The image designer is likewise **optional and non-blocking**: `ImageExecutor` swallows *all* exceptions into an `[Image skipped: …]` message, because the fan-in barrier only fires once **both** branches complete — an unhandled exception in either branch would stall the whole workflow.

## Key Conventions

**Pydantic models are the inter-stage contracts.** `PodcastRequest`, `ResearchBrief`, `PodcastScript`, `NarrationResult`, and `ImageResult` (in `podcaster/models.py`) are what executors pass via `ctx.send_message()`. The two parallel branches emit **distinct** types (`NarrationResult` vs `ImageResult`) so `FinalizeExecutor` can discriminate them in the aggregated list. Speakers are typed `Literal["Alex", "Jordan"]` in `DialogueTurn`.

**All agents use `AzureCliCredential` (keyless Entra ID auth).** There are no API key strings in agent construction — `FoundryChatClient`, the Speech narrator, and the image REST call all authenticate via `az login`.

**Speech Entra auth uses the `aad#<resourceId>#<token>` format.** The narrator builds this in `_headers()`. `AZURE_SPEECH_RESOURCE_ID` is the ARM account-level ID (no `/projects/...` suffix); it's required when `USE_SPEECH_ENTRA_AUTH=true`.

**JSON is parsed from model output manually.** Both `researcher.py` and `scriptwriter.py` have a local `_extract_json()` helper that strips markdown fences before `json.loads()`. Agent instructions explicitly forbid markdown wrappers.

**Narrator retries on gateway errors.** `_RETRY_STATUSES = {500, 502, 503, 504}` with up to 4 attempts and exponential backoff — MAI-Voice-2 intermittently resets connections.

**Voice presets live in `config.VOICE_PRESETS`.** Add new `(male_voice, female_voice)` pairs there; `test_voice_preset_generates_mp3` is parametrized over all presets and generates `output/compare_<preset>.mp3` for A/B comparison.

## Environment

Copy `.env.example` to `.env`. Minimum required:

| Variable | Purpose |
|---|---|
| `FOUNDRY_PROJECT_ENDPOINT` | Foundry project URL |
| `FOUNDRY_MODEL` | Deployment name (default: `gpt-5-mini`) |
| `FOUNDRY_IMAGE_MODEL` | MAI image deployment name, same project (default: `MAI-Image-2.5-Flash`; image only) |
| `AZURE_SPEECH_ENDPOINT` | Regional TTS endpoint (audio only) |
| `AZURE_SPEECH_RESOURCE_ID` | ARM resource ID for Entra auth (audio only) |
