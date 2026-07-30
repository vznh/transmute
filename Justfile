default:
    @just --list

sync:
    uv sync

run:
    uv run transmute

test:
    uv run pytest

lint:
    uv run ruff check .

check: test lint
