# Plan: Podcaster → Foundry Hosted Agents (multi-agent)

## Goal
Convert the single agent-framework Workflow into 3–4 independently hostable Foundry
agents (researcher, scriptwriter, narrator, + optional orchestrator). Scope = scaffold +
make hostable + validate each leaf agent locally with `azd ai agent run`. No provision/deploy.
Reuse existing `podcaster` Foundry project via `--project-id`. Narrator uploads MP3 to Blob,
returns URL.

## Decisions (from user)
- Scope: local run only (scaffold + hostable). No azd provision/deploy.
- Project: existing `podcaster` project (`--project-id`).
- Audio: narrator uploads MP3 to Azure Blob Storage, returns URL.
- Entity: 3 agents (researcher, scriptwriter, narrator), maybe 4th orchestrator.

## Key architecture facts
- Hosted agent contract (per microsoft-foundry skill):
  - Each agent = one `azure.yaml services.<name>` (host: azure.ai.agent) + `src/<name>/agent.yaml`
    (ContainerAgent: kind hosted, protocols, code_configuration{runtime, entry_point}, env vars)
    + `.agentignore` + `requirements.txt` + entry point serving the `responses` protocol on :8088.
  - Model deployment via azd Golden Path (`config.deployments[]`). Existing project → reuse gpt-5-mini.
  - Runtime injects FOUNDRY_PROJECT_ENDPOINT + AZURE_AI_MODEL_DEPLOYMENT_NAME (+ APPLICATIONINSIGHTS_CONNECTION_STRING).
  - Local run: `azd ai agent run` (one agent at a time) + `azd ai agent invoke --local`.
- Current code: agent-framework Workflow (parse→research→write_script→narrate) served via devui/AG-UI.
  Agents use FoundryChatClient(AzureCliCredential()). Researcher uses native web_search.
  Narrator = REST call to Azure Speech, writes MP3 to output/, served via /audio static mount.

## Structural challenge: shared code
Hosted deploy bundles ONLY the service dir. Shared modules (models.py, config.py, _resilience.py,
observability.py) must be importable inside each service. Recommendation: keep `src/podcaster/` as
an installable package and add it to each service's requirements.txt as a path/editable dep, OR
duplicate the minimal Pydantic contracts into each service. Decide before scaffolding.

## Observability (new since first draft)
- `src/podcaster/observability.py` = shared `setup_observability()` (stdlib logging + optional OTEL
  via ENABLE_OTEL / APPLICATIONINSIGHTS_CONNECTION_STRING / ENABLE_CONSOLE_EXPORTERS /
  VS_CODE_EXTENSION_PORT / LOG_LEVEL). Called at startup in main.py and server.py.
- Each hosted agent entry point MUST call setup_observability() at startup.
- Foundry hosted runtime auto-injects APPLICATIONINSIGHTS_CONNECTION_STRING → OTEL wires up in-container
  with no extra config. Do NOT put APPLICATIONINSIGHTS_CONNECTION_STRING in agent.yaml env_vars (runtime-injected).
- _resilience.py now also handles empty model responses (retry) — keep this in researcher/scriptwriter agents.

## Multi-agent orchestration mechanism
- Leaf agents (researcher, scriptwriter, narrator) = independent hosted agents.
- Orchestrator calls them via A2A connections (remote-a2a toolbox) — resolves to deployed endpoints.
- LIMITATION: end-to-end orchestration only testable after deploy. Local milestone = each leaf
  agent runs + invokes individually. Orchestrator code complete with A2A placeholders.

## Target layout
```
src/
  researcher/    agent.yaml  main.py  requirements.txt  .agentignore
  scriptwriter/  ...
  narrator/      ... (+ blob upload)
  orchestrator/  ... (A2A to the 3, optional)
  podcaster/     shared lib (models, config, _resilience, observability) reused by all
azure.yaml       4 services
```

## Steps

### Phase 0 — Prep & decisions
1. Verify env: `./scripts/verify-environment.sh` (microsoft-foundry skill). Confirm az/azd login,
   azure.ai.agents extension. Do NOT run az login for user.
2. Decide shared-code strategy (installable `podcaster` pkg vs duplicate contracts). *blocks scaffolding*
3. Resolve existing project ARM id from FOUNDRY_PROJECT_ENDPOINT via resolve-project-id.sh.

### Phase 1 — Refactor agent logic into hostable units (*parallelizable per agent*)
4. Researcher: wrap run_researcher as an agent-framework `Agent` entry point (topic in → ResearchBrief JSON out).
   Call setup_observability() at startup; keep _resilience (429 + empty-response retry).
5. Scriptwriter: `Agent` entry point (brief in → PodcastScript JSON out). setup_observability() + resilience.
6. Narrator: code entry point (script JSON in → MP3 → Blob upload → URL out). Add azure-storage-blob;
   use DefaultAzureCredential + AZURE_STORAGE_ACCOUNT_URL + container env vars. Drop local /audio mount.
   setup_observability() at startup.
7. Orchestrator (optional): agent that calls the 3 leaf agents as A2A tools (placeholders locally).
   setup_observability() at startup.

### Phase 2 — Scaffold hosted services
8. For each agent: `azd ai agent init` (brownfield `--src ./src/<name> --agent-name <name>
   --deploy-mode code --runtime python_3_13 --entry-point main.py --project-id <arm-id>`).
   Writes azure.yaml service + agent.yaml + .agentignore.
9. Sanity-check each: config.deployments[] non-empty, agent.yaml entry_point matches file,
   no duplicate `<name>-2` services. Narrator may omit model deployment (pure code, no LLM).
10. Add AGENTS.md marker line (microsoft-foundry skill).

### Phase 3 — Wire env + shared code
11. Per-service requirements.txt: agent-framework-core, agent-framework-foundry, azure-identity,
    pydantic, requests, (+ azure-storage-blob for narrator),
    (+ azure-monitor-opentelemetry if OTEL export wanted), + shared podcaster pkg per Phase-0 choice.
12. Per-service .env: FOUNDRY_PROJECT_ENDPOINT, AZURE_AI_MODEL_DEPLOYMENT_NAME (existing project).
    Narrator: AZURE_SPEECH_ENDPOINT, AZURE_SPEECH_RESOURCE_ID, AZURE_STORAGE_ACCOUNT_URL, container.

### Phase 4 — Local validation (per agent)
13. Per leaf agent: service-dir venv + uv, `azd ai agent run --no-inspector`, wait for ready line,
    `azd ai agent invoke --local "<representative prompt>"`, stop server.
14. Narrator: needs an existing Blob container reachable via dev credentials to fully test upload;
    otherwise validate script→speech and stub/skip upload locally.

## Out of scope
- azd provision / azd deploy (billed, live infra).
- Creating the Blob storage account + RBAC role assignments (needed for hosted MI at deploy time).
- End-to-end orchestrator-over-A2A validation (requires deployed leaf agents).
- German/voice-preset changes; existing devui/AG-UI/frontend paths (left as-is or removed later).

## Further considerations (raise with user)
1. Narrator as an "agent" vs a "tool": hosting a non-LLM REST caller as a Foundry agent is unusual;
   alternative is exposing narration as a tool the orchestrator/scriptwriter calls. Recommend keep as
   requested (code agent) but note the oddity.
2. Keep existing single-Workflow app (main.py/server.py/frontend) alongside the new per-agent services,
   or replace it? Recommend keep during transition.
3. Shared-code packaging choice (installable pkg vs duplication).
