TRIBUNAL ?= tjsp
CLASSE ?=
MAX_DOCS ?= 5000

.PHONY: setup ingest transform pipeline test lint

setup:
	python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"

ingest:
	datajud ingest --tribunal $(TRIBUNAL) --max-docs $(MAX_DOCS) $(if $(CLASSE),--classe $(CLASSE),)

transform:
	datajud transform

pipeline: ingest transform

test:
	pytest -q

lint:
	ruff check src tests
