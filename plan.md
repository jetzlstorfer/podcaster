# Plan: Podcaster → Azure + Foundry Hosted Agents (deploy)

## Goal
Deploy podcaster to Azure. The researcher, scriptwriter, and narrator become **private Foundry
hosted agents** (Entra + RBAC, no public ingress). One **public Azure Container App** serves the
built React SPA + FastAPI AG-UI backend behind **Entra Easy Auth**. The backend orchestrates the
three agents with its managed identity. The narrator uploads the MP3 to a **private Blob container**;
the backend streams it back via an `/audio/<file>` proxy. Everything runs in **Sweden Central**.
Local development flows (devui/CLI + `azd ai agent run`) must keep working.

## Decisions (user-confirmed)
- **Isolation: identity-only (RBAC).** Hosted agents have no public website; they are reachable only
  through the Foundry project endpoint with Entra auth. Only the backend MI (role *Azure AI User*)
  can invoke them. No VNet/Private Link.
- **UI hosting: single Container App** serving both the built SPA (static) and the FastAPI backend.
- **UI auth: Entra ID Easy Auth** in front of the Container App.
- **MP3 delivery: backend proxy** `/audio/<file>` streams from a private Blob container.
- **Orchestration: the public FastAPI backend** orchestrates the 3 hosted agents (reuse workflow.py).
- **Long runs: Option A** — keep SSE streaming; tune Container Apps ingress timeout (no background job).
- **Region: Sweden Central** for all resources (Foundry, Speech, storage, container app).
- Reuse the existing `podcaster` Foundry project + `gpt-5-mini` deployment.
- Cost guardrails: deferred for now.

## Target topology
```
Users → Easy Auth → [Container App: FastAPI + built SPA]  (Sweden Central, public ingress)
   │  MI: Azure AI User        → Foundry project (private) → researcher · scriptwriter · narrator
   │  MI: Blob Data Reader      → Blob (read) → proxy /audio/<file> → browser
   narrator MI → Azure Speech (Entra) + Blob (upload)
```
Agents are "not exposed" because hosted agents get no standalone public endpoint — access is via the
Foundry project endpoint + RBAC only.

## Key architecture facts
- Hosted agent contract (per microsoft-foundry skill): each agent = one `azure.yaml services.<name>`
  (host: azure.ai.agent) + `src/<name>/agent.yaml` (kind hosted, protocols, code_configuration
  {runtime, entry_point}, env vars) + `.agentignore` + `requirements.txt` + entry point serving the
  `responses` protocol on :8088.
- Runtime injects FOUNDRY_PROJECT_ENDPOINT, AZURE_AI_MODEL_DEPLOYMENT_NAME, and
  APPLICATIONINSIGHTS_CONNECTION_STRING into hosted agents.
- Current code: agent-framework Workflow (parse→research→write_script→narrate) via devui/AG-UI.
  Agents use `FoundryChatClient(AzureCliCredential())`; researcher uses native web_search.
  Narrator = REST to Azure Speech, writes MP3 to `output/`, served via `/audio` static mount (server.py).
- **Credential swap required:** `AzureCliCredential()` is hardcoded in researcher.py, scriptwriter.py,
  narrator.py, and `_resilience.make_foundry_client`. Change to `DefaultAzureCredential()` so managed
  identity works in-cloud while `az login` still works locally.

## Shared code
Hosted deploy bundles only the service dir. Keep `src/podcaster/` (models, config, _resilience,
observability) as an installable package (add `pyproject.toml`) referenced from each service's
`requirements.txt` as a path dependency, so every agent can import the shared contracts.

## Local development (must keep working)
- `DefaultAzureCredential()` falls back to `az login` locally, so no code branching is needed.
- Existing flows stay functional: `make run` (devui :8088), `make cli Q="..."`, `make test`, and the
  FastAPI AG-UI server + Vite frontend for manual UI testing.
- Each hosted agent is runnable standalone with `azd ai agent run` + `azd ai agent invoke --local`.
- Narrator local mode: point `AZURE_STORAGE_ACCOUNT_URL` at a dev container (or Azurite) to exercise
  upload; otherwise it validates script→speech and skips upload.
- Keep the single-Workflow app (main.py/server.py/frontend) alongside the new per-agent services
  during the transition so the local dev experience is unchanged.

## Target layout
```
src/
  researcher/    agent.yaml  main.py  requirements.txt  .agentignore
  scriptwriter/  ...
  narrator/      ... (+ blob upload)
  podcaster/     shared lib (models, config, _resilience, observability) — installable pkg
azure.yaml       3 agent services + 1 containerapp service
infra/           bicep: storage, container app env/app, RBAC, App Insights
Dockerfile       multi-stage: node build SPA → python runtime
pyproject.toml   makes src/podcaster installable
```

## Steps

### Phase 1 — Code: make agents hostable + cloud-ready (*parallel per agent*)
1. Swap `AzureCliCredential()` → `DefaultAzureCredential()` in `_resilience.py`, researcher.py,
   scriptwriter.py, narrator.py (token provider). Verify local flows still run via `az login`.
2. Researcher hosted entry point `src/researcher/main.py`: agent-framework Agent serving `responses`
   (topic → ResearchBrief JSON). Call `setup_observability()`; keep `run_agent_resilient`
   (429 + empty-response retry) and native web_search.
3. Scriptwriter hosted entry point `src/scriptwriter/main.py`: brief → PodcastScript JSON.
   `setup_observability()` + resilience.
4. Narrator hosted entry point `src/narrator/main.py`: script JSON → synth MP3 → **upload to Blob** →
   return { blob name, container }. Add `azure-storage-blob`; use DefaultAzureCredential +
   `AZURE_STORAGE_ACCOUNT_URL` + `AZURE_STORAGE_CONTAINER`. Drop local file write. `setup_observability()`.
5. Add `pyproject.toml` so `src/podcaster/` installs as a package for each service.

### Phase 2 — Backend/UI changes
6. server.py: rework the ResearchExecutor/ScriptExecutor/NarrateExecutor so the backend **invokes the
   deployed hosted agents** (Foundry agents client) using DefaultAzureCredential, instead of building
   FoundryChatClient inline. Keep AG-UI `/podcast` SSE streaming + per-stage events.
7. server.py `/audio/<file>`: replace the StaticFiles mount with a proxy that streams the blob from the
   private container (backend MI: Storage Blob Data Reader). NarrateExecutor returns `/audio/<blob>`.
8. Serve built SPA: `npm run build` → copy `frontend/dist` into image; FastAPI mounts it at `/`.
   `VITE_BACKEND_URL` becomes same-origin (relative), simplifying CORS.
9. Add a multi-stage Dockerfile (node build SPA → python runtime).

### Phase 3 — Scaffold services (azd)
10. Scaffold the 3 agent services via `azd ai agent init` (brownfield: `--src ./src/<name>
    --deploy-mode code --runtime python_3_13 --entry-point main.py --project-id <existing podcaster ARM id>`).
11. Add the backend/UI as an azd `containerapp` service in azure.yaml (Dockerfile path).
12. Sanity-check: researcher/scriptwriter reuse `gpt-5-mini`; narrator needs NO model. No duplicate
    `<name>-2` services. Add AGENTS.md marker line.

### Phase 4 — Infrastructure (bicep under infra/), all in Sweden Central
13. Storage account + **private** blob container (public access disabled).
14. Container Apps environment + Container App (external/public ingress) + Azure Container Registry.
    Tune ingress request/idle timeout to support long SSE streams (Option A).
15. Enable **Easy Auth** (Entra) on the Container App; register/allow the sign-in app.
16. Reuse existing Foundry project + Speech (AI Services) resource; App Insights for the backend.
17. RBAC role assignments:
    - Backend MI → **Azure AI User** on the Foundry project (invoke agents).
    - Backend MI → **Storage Blob Data Reader** on the storage account (proxy read).
    - Narrator agent MI → **Storage Blob Data Contributor** (upload) + **Cognitive Services Speech User**.
    - Researcher/scriptwriter agent MIs → model access (Azure AI User) — usually wired by azd Golden Path.

### Phase 5 — Env wiring
18. Backend env: FOUNDRY_PROJECT_ENDPOINT, agent names/versions to invoke, AZURE_STORAGE_ACCOUNT_URL,
    AZURE_STORAGE_CONTAINER, APPLICATIONINSIGHTS_CONNECTION_STRING, LOG_LEVEL.
19. Narrator agent env: AZURE_SPEECH_ENDPOINT (Sweden Central), AZURE_SPEECH_RESOURCE_ID,
    USE_SPEECH_ENTRA_AUTH=true, AZURE_STORAGE_ACCOUNT_URL, AZURE_STORAGE_CONTAINER.
20. Do NOT put APPLICATIONINSIGHTS_CONNECTION_STRING in agent.yaml env_vars (runtime-injected).

### Phase 6 — Provision, deploy, verify
21. `azd provision` (infra + RBAC) then `azd deploy` (or `azd up`). Deploys 3 agents + container app.
22. Smoke-test each hosted agent with `azd ai agent invoke` and a representative prompt.
23. Open the Container App URL → sign in (Easy Auth) → run a topic end-to-end → verify streaming
    stages, MP3 plays via `/audio` proxy, blob written to the private container.
24. Confirm agents have no public ingress (only the Foundry project endpoint); backend MI can invoke them.
25. Re-verify local dev is intact: `make run`, `make cli`, and `azd ai agent run`/`invoke --local`.

## Out of scope
- VNet / Private Link isolation (chose RBAC-only). Can harden later.
- German/voice-preset changes. Fine-tuning/eval loops. A2A orchestrator agent.
- Multi-region failover (FOUNDRY_*_FALLBACK left optional).
- Cost guardrails / per-user rate limiting (deferred).

## Considerations noted
1. Long runs handled via Option A (SSE + tuned ingress timeout); revisit background+polling only if
   generations exceed Container Apps limits.
2. Verify `gpt-5-mini` capacity and MAI-Voice-2 availability in Sweden Central before deploy.
3. Narrator is a non-LLM code agent hosted as a Foundry agent (per request) — unusual but valid.
