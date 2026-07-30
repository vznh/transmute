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

# Regenerate the Homebrew formula after a dependency or version change.
formula:
    uv run python packaging/homebrew/generate_formula.py --write
