.PHONY: format test

PYTHON ?= python
RUFF ?= ruff

format:
	$(RUFF) format reviewbot tests

test:
	$(PYTHON) -m pytest tests/
