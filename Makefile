.PHONY: install run web ui cli lint clean test

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

## Run the full pipeline once from the CLI
## Usage: make cli Q="Your research question here"
##    or: make cli SCRIPT=output/My_Title.json   (narrate a saved script only)
cli:
	$(PYTHON) main.py --cli $(if $(SCRIPT),--script "$(SCRIPT)",--question "$(Q)")

## Lint with ruff (install separately: pip install ruff)
lint:
	ruff check src/ main.py server.py

## Run tests (synthesizes real MP3s into output/ via the Speech API)
test:
	$(PYTHON) -m pytest -v tests/

## Remove caches and generated MP3s
clean:
	find . -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
	rm -f output/*.mp3
