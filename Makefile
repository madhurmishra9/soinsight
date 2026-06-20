# SOInsight — convenience targets. All delegate to the cross-platform launcher.
# Usage:  make setup   |   make dev   |   make start
PYTHON ?= python3

.PHONY: setup dev start help

help:
	@echo "make setup   - install backend + frontend dependencies"
	@echo "make dev     - run backend (:8000, hot-reload) + frontend (:5173) together"
	@echo "make start   - build UI and serve everything from one process (:8000)"

setup:
	$(PYTHON) run.py --setup

dev:
	$(PYTHON) run.py

start:
	$(PYTHON) run.py --prod
