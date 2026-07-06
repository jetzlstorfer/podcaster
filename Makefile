.PHONY: install run web ui ui-build cli lint clean test agent-wheels

PYTHON  := python
PORT    := 8088
WEB_PORT := 8089
Q       ?= What are the latest breakthroughs in quantum computing?
SCRIPT  ?=

## Install Python dependencies
install:
	$(PYTHON) -m pip install -q -r requirements.txt

## Start the devui HTTP server + Agent Inspector on port $(PORT)
run:
	$(PYTHON) main.py --server --port $(PORT)

## Start the FastAPI AG-UI server (backend for the web UI) on port $(WEB_PORT)
web:
	WEB_PORT=$(WEB_PORT) $(PYTHON) -m uvicorn server:app --host 127.0.0.1 --port $(WEB_PORT)

## Start the Vite dev server for the web UI (http://127.0.0.1:5173)
ui:
	cd frontend && npm run dev

## Build the SPA into frontend/dist so `make web` serves API + UI on one origin
ui-build:
	cd frontend && npm install && npm run build

## Run the full pipeline once from the CLI
## Usage: make cli Q="Your research question here"
##    or: make cli SCRIPT=output/My_Title.json   (narrate a saved script only)
cli:
	$(PYTHON) main.py --cli $(if $(SCRIPT),--script "$(SCRIPT)",--question "$(Q)")

## Lint with ruff (install separately: pip install ruff)
lint:
	ruff check podcaster/ src/ main.py server.py

## Build the shared `podcaster` wheel and vendor it into each hosted agent
## service dir (required before `azd ai agent run` / deploy so the bundle can
## install the shared package). Pinned to podcaster 0.1.0 (see pyproject.toml).
agent-wheels:
	$(PYTHON) -m pip wheel . -w dist --no-deps
	@for svc in researcher scriptwriter narrator; do \
		cp dist/podcaster-0.1.0-py3-none-any.whl src/$$svc/ && \
		echo "vendored wheel -> src/$$svc/"; \
	done

## Run tests (synthesizes real MP3s into output/ via the Speech API)
test:
	$(PYTHON) -m pytest -v tests/

## Remove caches and generated artifacts (audio, cover art, scripts, builds)
clean:
	find . -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
	rm -f output/*.mp3 output/*.png output/*.json
	rm -f src/*/podcaster-*.whl
	rm -rf dist build frontend/dist
