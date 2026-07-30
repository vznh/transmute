# Transmute

Convert YouTube or SoundCloud links into rich MP3s, from your terminal. We support single or batch operations.

## How it works  
1. Authenticate
   
You can do any of the two options:
- Authenticate with your Claude or Codex account by signing in
- Bring your own API key (only Codex, Claude supported for now)
2. Provide a correct URL
  
We support `youtube.com`, `youtu.be`, and `soundcloud.com` links, including
subdomains like `music.youtube.com`. Anything else is turned away as you paste it,
rather than failing later mid-download.

3. Ensure you output to the right directory using `/out`

   
4. We use Claude / Codex to utilize Web Search to ensure proper metadata is fulfilled
   
- This works especially for underground rap artists. Often times, uploaders are not the name of the author and we do a double-check and sanity check to ensure that the right artist is associated with the song.
- The image is not altered. From the link you provide, we attach that image to it. 
  

## Requirements

- [uv](https://docs.astral.sh/uv/) (manages Python + deps)
- ffmpeg (`brew install ffmpeg`)
- Claude Code signed in with Claude, or Codex CLI signed in with ChatGPT, for
  subscription-backed metadata enrichment

## Run

```sh
uv sync
uv run transmute
```

Run both commands from the repository root. After the first `uv sync`, starting
Transmute only requires `uv run transmute`.

Paste one or more links (even concatenated back-to-back) and hit Enter. Links are
queued and processed in the background (up to 4 at a time) — the input line stays
live at the bottom, so keep pasting while earlier tracks convert. A status bar below
the prompt shows the output dir, bitrate, and active/queued work. MP3s are written
at 320kbps with embedded metadata and cover art, to `~/Downloads` by default.

Unsupported links are rejected as you paste them; if a paste mixes supported and
unsupported links, the supported ones are queued and the rest are reported as
ignored. When a download does fail, the reason is summarized in a line you can act
on — `ffmpeg missing — brew install ffmpeg`, `requires login — needs browser
cookies`, `not available in your region`. Failures that retrying cannot fix, like a
private or removed video, are marked non-retryable and left out of `/retry`.

## History and persistence

Prompt recall and structured download activity persist locally across restarts.
Recent completed and failed tracks are restored when Transmute opens; restored
failures can be retried and restored low-confidence entries can receive hints just
like entries from the current session. `/list` includes recent persisted tracks.

If Transmute is killed mid-download, the next launch notices the interrupted
session and turns whatever was still in flight into a retryable failure
(`interrupted — retry when ready`) instead of losing it. Several instances can run
at once against the same storage: a retry or a hint is claimed by exactly one of
them, and starting one does not disturb work already running in another.

Your output directory and bitrate also persist. `/out` and `/quality` are saved to
`~/.transmute/settings.json` and restored on the next launch. A runtime command in
the current session overrides the saved file, which overrides the built-in defaults
(`~/Downloads`, 320kbps); a missing or unreadable settings file falls back to those
defaults without overwriting it.

Prompt recall is stored in `~/.transmute/history`, and download activity is stored
in `~/.transmute/activity.sqlite3`. URLs and metadata remain local and unencrypted,
with owner-only permissions on the storage directory and files. `/clear` removes
persisted completed and failed activity as well as clearing the current view.
In-flight jobs are not canceled, so they can appear again when they finish.

## Metadata enrichment

After each download, Transmute runs a provider-backed web search to find the track's
canonical metadata — artist, title, album, release year, genre. Tags are written into
the MP3 (cover art is preserved) and the file is renamed to
`Artist - Title.mp3`. If a file with that name already exists, Transmute keeps it
and adds a numeric suffix (`Artist - Title (1).mp3`) rather than overwriting it.

Auth (in order of preference):

1. **Entered API key** — run `/key` to open a masked prompt. It accepts one
   OpenAI `sk-…` or Anthropic `sk-ant-…` key, auto-detects the provider, and
   replaces any previously entered key. The key stays in memory only and is
   never written to Transmute's command history. Use `/key clear` to remove it.
2. **Environment API key** — `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` is selected
   automatically. If both are set, OpenAI takes priority. API usage is billed
   separately by the detected provider.
3. **Claude subscription** (recommended) — if the local `claude` CLI is installed,
   Transmute uses its Claude subscription auth. Run `/login` or `/login claude` to
   sign in.
4. **ChatGPT subscription** — when Claude is unavailable, Transmute runs an
   ephemeral, read-only `codex exec` lookup with live web search and local shell
   access disabled. Run `/login codex` once to sign in with ChatGPT.

Choose a provider with `/enrich codex|claude|api`, or toggle the selected provider
with `/enrich on|off`. With no usable credentials, enrichment is skipped
automatically.

## Commands

| Command | Description |
| --- | --- |
| `/out [dir]` | show the output directory; with no arg, opens an inline prompt (pre-filled with `~/`) to change it — enter/esc keeps it |
| `/quality [128\|192\|256\|320]` | show or set MP3 bitrate |
| `/enrich [codex\|claude\|api\|on\|off]` | choose or toggle web-search metadata enrichment |
| `/key [clear]` | securely enter one OpenAI or Anthropic API key, or clear it |
| `↑` / `↓` | select failed or low-confidence History entries — Enter retries a failure; low-confidence entries open an inline hint input |
| `/list` | show recent converted tracks, including persisted results |
| `/retry` | requeue failed downloads |
| `/login [codex\|claude]` | log in to a subscription provider (opens browser) |
| `/logout [codex\|claude]` | log out of a subscription provider |
| `/clear` | clear completed and failed activity from the screen and persistent history |
| `/quit` | exit (or Ctrl-D / double Ctrl-C) |

## Development

```sh
uv run pytest
uv run ruff check .
```

If [`just`](https://just.systems/) is installed, the same workflows are available
as `just run`, `just test`, `just lint`, and `just check`.

## Project map

`App` owns session state and orchestration. The interface modules read that
state and translate gestures into `App` calls; the services below run without
prompt_toolkit and return data rather than rendering it.

```text
main
  └── App (state + orchestration)
        ├── prompt_toolkit adapters (layout, keys, commands, widgets, style)
        ├── downloader (yt-dlp/ffmpeg)
        ├── enrich (provider calls + normalized tags + ID3 writes)
        └── settings, history (durable local storage)
```

Entry point

- `transmute/main.py` — process startup and wiring
- `transmute/__main__.py` — supports `python -m transmute`

Core

- `transmute/app.py` — session state, job lifecycle, and worker coordination
- `transmute/config.py` — settings model and shared operational constants

Interface

- `transmute/layout.py` — prompt_toolkit window arrangement
- `transmute/keys.py` — keyboard behavior
- `transmute/commands.py` — slash-command dispatch and implementations
- `transmute/widgets.py` — reusable prompt components
- `transmute/style.py` — theme and user-facing UI constants

Services

- `transmute/downloader.py` — local yt-dlp/ffmpeg pipeline
- `transmute/enrich.py` — provider selection, web research, and ID3 tagging
- `transmute/settings.py` — persistent output directory and bitrate storage
- `transmute/history.py` — persistent download activity storage

Tests

- `tests/test_app.py` — selection, retry, modal, queue, and notice state
- `tests/test_downloader.py` — URL parsing, yt-dlp options, and error classification
- `tests/test_enrich.py` — credentials, provider calls, tagging, and renaming
- `tests/test_settings.py` — settings round-trip, rejection, and fallback
- `tests/test_history.py` — activity storage, retry/hint claiming, session recovery
