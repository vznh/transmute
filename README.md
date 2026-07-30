# Transmute

Convert YouTube or SoundCloud links into rich MP3s, from your terminal. We support single or batch operations.

## How it works  
1. Authenticate
   
You can do any of the two options:
- Authenticate with your Claude or Codex account by signing in
- Bring your own API key (only Codex, Claude supported for now)
2. Provide a correct URL
  
We support only https://[youtube.com, soundcloud.com] links.  

3. Ensure you output to the right directory

   
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
| `/list` | show tracks converted this session |
| `/retry` | requeue failed downloads |
| `/login [codex\|claude]` | log in to a subscription provider (opens browser) |
| `/logout [codex\|claude]` | log out of a subscription provider |
| `/clear` | clear the screen |
| `/quit` | exit (or Ctrl-D / double Ctrl-C) |

## Development

```sh
uv run pytest
uv run ruff check .
```

If [`just`](https://just.systems/) is installed, the same workflows are available
as `just run`, `just test`, `just lint`, and `just check`.

## Project map

- `transmute/app.py` — application state and the download/enrichment pipeline
- `transmute/layout.py` — prompt_toolkit window arrangement
- `transmute/keys.py` — keyboard behavior
- `transmute/commands.py` — slash-command dispatch and implementations
- `transmute/widgets.py` — reusable prompt components
- `transmute/style.py` — theme and user-facing UI constants
- `transmute/config.py` — settings and shared operational constants
- `transmute/downloader.py` — local yt-dlp/ffmpeg service
- `transmute/enrich.py` — provider selection, web research, and ID3 tagging
- `tests/` — state-machine and service tests
