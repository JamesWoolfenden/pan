.PHONY: install run reindex test

install:
	uv sync

run:
	uv run uvicorn backend.app:app --reload --host 0.0.0.0 --port 8000

reindex:
	uv run python -m scripts.reindex

test:
	uv run pytest
