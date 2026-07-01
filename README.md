# Podcaster

A multi-agent pipeline that turns a research question into a ready-to-play podcast MP3 (narrated by two hosts) 🎙️

```mermaid
flowchart TD
    Q[Research question] --> R["Researcher<br/>(Agent)"]
    R -- "ResearchBrief<br/>(web search)" --> S["Scriptwriter<br/>(Agent)"]
    S -- "PodcastScript" --> N["Narrator<br/>(MAI-Voice-2)"]
    N --> MP3[output/podcast.mp3]
```

| Agent | Role |
|---|---|
| **Researcher** | Uses the Foundry native web-search tool to gather real-time facts and sources |
| **Scriptwriter** | Writes a natural two-host dialogue (Alex ♂ / Jordan ♀) with intro, discussion, and outro |
| **Narrator** | Synthesises the script to MP3 using **MAI-Voice-2** voices via the Azure Speech REST API |

Built with **Microsoft Agent Framework** (`agent-framework-foundry`) and **Microsoft Foundry** (project `podcaster`, model `gpt-5-mini`).

---

## Prerequisites

- Python 3.11+
- `az login` — Azure CLI authenticated (keyless Entra ID auth, no API keys)
- A Foundry project with `gpt-5-mini` deployed (already set up: `podcaster-resource`)
- *(For audio)* An Azure Speech / Foundry resource in a MAI-Voice-2-supported
  region (e.g. `swedencentral`, `eastus`) with the **Cognitive Services Speech
  User** role assigned to your identity

---

## Quick start

```bash
# 1. Install dependencies
make install

# 2. Copy and configure environment
cp .env.example .env   # already done if you cloned this repo
# Edit .env — FOUNDRY_PROJECT_ENDPOINT and FOUNDRY_MODEL are pre-filled.
# Add AZURE_SPEECH_ENDPOINT to enable audio output.

# 3. Run the pipeline (interactive devui server)
make run

# 4. Or run once from the command line
make cli Q="What are the latest breakthroughs in quantum computing?"
```

---

## Makefile targets

| Target | Description |
|---|---|
| `make install` | Install Python dependencies from `requirements.txt` |
| `make run` | Start the devui HTTP server on port 8088 (Agent Inspector) |
| `make web` | Start the FastAPI **AG-UI** backend on port 8089 (for the web UI) |
| `make ui` | Start the Vite dev server for the web UI (http://127.0.0.1:5173) |
| `make cli Q="..."` | Run the full pipeline once and print the script |
| `make test` | Run the test suite (synthesizes real MP3s into `output/`) |
| `make lint` | Run `ruff` linter across the source tree |
| `make clean` | Remove `__pycache__` directories and generated MP3s |

---

## Web UI (AG-UI)

A custom **Vite + React** front end talks to the pipeline over the
[AG-UI protocol](https://docs.ag-ui.com) using `@ag-ui/client`. It streams
per-stage progress (parse → research → write script → narrate), renders the
two-host script, and plays the generated MP3 in the browser. You can also pick a
**length** (short / medium / long) and **language** (English / German).

```bash
# Terminal 1 — start the AG-UI backend (FastAPI + uvicorn)
make web

# Terminal 2 — start the web UI
cd frontend && npm install   # first time only
make ui
```

Then open http://127.0.0.1:5173. The UI defaults to the backend at
`http://127.0.0.1:8089`; override with `VITE_BACKEND_URL` (see
`frontend/.env.example`).

- **Backend** — `server.py` mounts the workflow at `POST /podcast` via
  `add_agent_framework_fastapi_endpoint`, serves generated audio from `/audio`,
  and exposes `/healthz`.
- **Request shape** — the UI sends `{ "topic", "length", "language" }` as the
  message content; the workflow's `parse` stage validates it into a
  `PodcastRequest`. Plain-text messages still work and fall back to defaults.

---

## Project structure

```
Podcaster/
├── main.py                        # Entrypoint  --server / --cli
├── server.py                      # FastAPI AG-UI backend (make web)
├── Makefile
├── requirements.txt
├── .env.example                   # Copy to .env and fill in values
├── .vscode/
│   ├── launch.json                # Debugger attach configs
│   └── tasks.json                 # agentdev server + Agent Inspector tasks
├── frontend/                      # Vite + React web UI (AG-UI client)
│   ├── index.html
│   └── src/
│       ├── App.tsx                # Form, progress stepper, script + audio
│       ├── api.ts                 # @ag-ui/client HttpAgent wiring
│       └── types.ts
├── output/                        # Generated MP3 files land here
├── tests/
│   ├── test_narrator.py           # Integration tests → synthesize real MP3s
│   └── test_pipeline.py           # Unit tests (parse, length, language SSML)
└── src/podcaster/
    ├── models.py                  # Pydantic: PodcastRequest, ResearchBrief, PodcastScript
    ├── config.py                  # Env settings + VOICE_PRESETS + LANGUAGE_VOICES
    ├── workflow.py                # Graph Workflow (WorkflowBuilder) + make_workflow()
    └── agents/
        ├── researcher.py          # Agent + Foundry web-search tool
        ├── scriptwriter.py        # Agent → structured JSON dialogue
        └── narrator.py            # MAI-Voice-2 REST → MP3
```

---

## Environment variables (`.env`)

| Variable | Required | Description |
|---|---|---|
| `FOUNDRY_PROJECT_ENDPOINT` | ✅ | `https://<resource>.services.ai.azure.com/api/projects/<project>` |
| `FOUNDRY_MODEL` | ✅ | Deployment name (default: `gpt-5-mini`) |
| `AZURE_SPEECH_ENDPOINT` | Audio only | `https://<region>.tts.speech.microsoft.com/` |
| `AZURE_SPEECH_RESOURCE_ID` | Audio (Entra) | Account-level ARM resource ID — required for Entra ID auth (see below) |
| `AZURE_SPEECH_KEY` | Optional | Only for resources with key auth enabled; leave blank to use Entra ID |
| `USE_SPEECH_ENTRA_AUTH` | Optional | `true` (default) to use `az login` / Entra ID |
| `PODCAST_VOICE_MALE` | Optional | Male voice (default: `en-US-Ethan:MAI-Voice-2`) |
| `PODCAST_VOICE_FEMALE` | Optional | Female voice (default: `en-US-Harper:MAI-Voice-2`) |

---

## Running with the Agent Inspector

1. Press **F5** in VS Code and select *"Podcaster: HTTP Server (Agent Inspector)"*.
2. The Foundry Toolkit Agent Inspector opens automatically.
3. Type a research question (e.g. *"What is the current state of fusion energy?"*) and send it.
4. Watch the three agents run in sequence; find the MP3 in `output/` when done.

---

## Audio output (MAI-Voice-2)

The Narrator agent is **optional** — if `AZURE_SPEECH_ENDPOINT` is not set the
pipeline still runs and prints the full script. To enable audio you need a Speech
(or multi-service Foundry) resource whose region supports **MAI voices**
(e.g. `swedencentral`, `eastus`).

### Authentication (Entra ID / keyless)

Foundry resources typically have key auth **disabled**, so the Narrator uses
Entra ID. The Speech text-to-speech REST API requires the Entra token wrapped in
the `aad#<resourceId>#<token>` format, so two settings are needed:

```bash
# 1. The regional TTS endpoint
AZURE_SPEECH_ENDPOINT=https://swedencentral.tts.speech.microsoft.com/

# 2. The account-level ARM resource ID (NO /projects/... suffix)
AZURE_SPEECH_RESOURCE_ID=/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.CognitiveServices/accounts/<name>
```

Assign yourself the **Cognitive Services Speech User** role on that resource:

```bash
SCOPE="/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.CognitiveServices/accounts/<name>"
az role assignment create \
  --assignee-object-id "$(az ad signed-in-user show --query id -o tsv)" \
  --assignee-principal-type User \
  --role "Cognitive Services Speech User" \
  --scope "$SCOPE"
```

Leave `AZURE_SPEECH_KEY` blank to keep using keyless Entra ID auth.

> **Note on voice styles:** each turn carries a delivery *style* drawn from the
> emotions MAI-Voice-2 supports natively (e.g. `happy`, `excited`, `sad`,
> `whispering`). For the MAI hosts the Narrator renders these with
> `mstts:express-as` so the model performs the emotion; voices without native
> style support (standard neural voices, `en-US-Grant:MAI-Voice-2`) fall back to
> a `prosody` approximation, and `neutral` emits no style tag.

---

## Tests

```bash
make test          # or: python -m pytest -v tests/
```

The tests in [`tests/test_narrator.py`](tests/test_narrator.py) are **integration
tests** — they call the Azure Speech API and write real MP3s to `output/`. They
require `AZURE_SPEECH_ENDPOINT` (and `az login`); if the endpoint isn't
configured the tests are **skipped** so the suite still passes without Azure
access.

---

## Comparing voice models

`config.VOICE_PRESETS` maps a label to a `(male_voice, female_voice)` pair. The
test `test_voice_preset_generates_mp3` synthesizes one MP3 per preset into
`output/compare_<preset>.mp3` so you can A/B the audio quality:

```python
VOICE_PRESETS = {
    "mai2":     ("en-US-Ethan:MAI-Voice-2", "en-US-Harper:MAI-Voice-2"),
    "mai2-alt": ("en-US-Grant:MAI-Voice-2", "en-US-Olivia:MAI-Voice-2"),
    "neural":   ("en-US-AndrewNeural",      "en-US-AvaNeural"),
}
```

Add or edit entries in [`src/podcaster/config.py`](src/podcaster/config.py), then
run `make test` to regenerate the comparison clips. At runtime you can also pass
`voice_male` / `voice_female` directly to `run_narrator(...)`.
