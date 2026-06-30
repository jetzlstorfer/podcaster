# Podcaster – Copilot Instructions

## Commands

```bash
make install          # Install dependencies from requirements.txt
make run              # Start devui HTTP server on port 8088 (Agent Inspector)
make cli Q="..."      # Run pipeline once, print script to stdout
make test             # Run full test suite (pytest -v tests/)
make lint             # Ruff linter: ruff check src/ main.py
make clean            # Remove __pycache__ and output/*.mp3

# Run a single test
python -m pytest -v tests/test_narrator.py::test_narrator_generates_mp3
```

Tests require `AZURE_SPEECH_ENDPOINT` and `az login`; the suite auto-skips audio tests when that env var is not set.

## Architecture

Three-stage linear graph workflow (`src/podcaster/workflow.py`):

```
question → ResearchExecutor → ScriptExecutor → NarrateExecutor → output/podcast.mp3
```

Each stage is an `Executor` subclass wired via `WorkflowBuilder`. The graph workflow (not the functional `@workflow`) is used so devui streams per-stage progress events.

- **Researcher** (`agents/researcher.py`): `Agent` + Foundry native web-search tool → returns `ResearchBrief`
- **Scriptwriter** (`agents/scriptwriter.py`): `Agent` (no tools) → structured JSON → `PodcastScript`
- **Narrator** (`agents/narrator.py`): Calls Azure Speech REST API directly (no agent framework) → writes MP3 to `output/`

The narrator is **optional** — if `AZURE_SPEECH_ENDPOINT` is not configured the pipeline still runs and the audio step emits a skip message instead of raising.

## Key Conventions

**Pydantic models are the inter-stage contracts.** `ResearchBrief` and `PodcastScript` (in `models.py`) are what executors pass via `ctx.send_message()`. Speakers are typed `Literal["Alex", "Jordan"]` in `DialogueTurn`.

**All agents use `AzureCliCredential` (keyless Entra ID auth).** There are no API key strings in agent construction — both `FoundryChatClient` and the Speech narrator authenticate via `az login`.

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
| `AZURE_SPEECH_ENDPOINT` | Regional TTS endpoint (audio only) |
| `AZURE_SPEECH_RESOURCE_ID` | ARM resource ID for Entra auth (audio only) |
