PY   ?= python
LANG ?= zh-TW
SRC  ?= docs/guide.md

help:            ## show this help
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | sed 's/:.*##/\t/' | column -t -s "$$(printf '\t')"

install:         ## editable install with dev tools
	$(PY) -m pip install -e ".[dev]"

test:            ## run the test suite
	$(PY) -m pytest -q

lint:            ## static checks
	$(PY) -m ruff check src tests

run:             ## full pipeline for SRC/LANG
	$(PY) -m scriptorium run $(SRC) --lang $(LANG)

check:           ## validate SRC/LANG
	$(PY) -m scriptorium check $(SRC) --lang $(LANG)

web:             ## open the review workbench
	$(PY) -m scriptorium web

stats:           ## coverage across tracked documents
	$(PY) -m scriptorium stats

.PHONY: help install test lint run check web stats
