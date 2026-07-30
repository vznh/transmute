# Transmute agent guide

Transmute is a local Python REPL that downloads YouTube or SoundCloud audio,
enriches its metadata through a user-provided Claude credential, and writes MP3s.

## Working commands

- Install dependencies: `uv sync`
- Run the app: `uv run transmute`
- Run tests: `uv run pytest`
- Lint: `uv run ruff check .`
- Full check: `just check` when `just` is installed

## Architecture

Keep `downloader.py` and `enrich.py` independent of prompt_toolkit. UI state and
pipeline orchestration belong in `app.py`; window composition belongs in
`layout.py`; key behavior belongs in `keys.py`; slash commands belong in
`commands.py`; reusable prompt pieces belong in `widgets.py`.

`App.lock` protects state read by both the prompt_toolkit event loop and worker
threads. Background workers must update UI state through `App` helpers and call
`refresh()`. Downloads must remain local and playlist expansion must stay disabled.
Claude subscription auth is the default enrichment path; `ANTHROPIC_API_KEY` is an
explicit API-billed override.

## Change discipline

Add state-machine tests for interaction changes and focused service tests for
download/enrichment logic. Never exercise `/logout` in automated or manual checks:
it removes the machine-wide Claude Code credential used by Conductor.
