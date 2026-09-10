.PHONY: test test-slow sync

sync:
	uv sync --extra audio --extra server --extra dev

test:
	uv run pytest tests/unit tests/audio -m "not slow"

test-slow:
	uv run pytest -m slow
