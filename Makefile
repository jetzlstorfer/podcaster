.PHONY: install run cli lint clean test

PYTHON  := python
PORT    := 8088
Q       ?= What are the latest breakthroughs in quantum computing?

## Install Python dependencies
install:
	$(PYTHON) -m pip install -q -r requirements.txt

## Start the devui HTTP server + Agent Inspector on port $(PORT)
run:
	$(PYTHON) main.py --server --port $(PORT)

## Run the full pipeline once from the CLI
## Usage: make cli Q="Your research question here"
cli:
	$(PYTHON) main.py --cli --question "$(Q)"

## Lint with ruff (install separately: pip install ruff)
lint:
	ruff check src/ main.py

## Run tests (synthesizes real MP3s into output/ via the Speech API)
test:
	$(PYTHON) -m pytest -v tests/

## Remove caches and generated MP3s
clean:
	find . -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
	rm -f output/*.mp3
