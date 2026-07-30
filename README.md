# Transmute

Convert YouTube or SoundCloud links into rich MP3s, from an interactive REPL.

## Requirements

- [uv](https://docs.astral.sh/uv/) (manages Python + deps)
- ffmpeg (`brew install ffmpeg`)

## Run

```sh
cd cli
uv run transmute
```

Paste one or more links (even concatenated back-to-back) and hit Enter. Links are
queued and processed in the background (up to 4 at a time) — the input line stays
live at the bottom, so keep pasting while earlier tracks convert. A status bar below
the prompt shows the output dir, bitrate, and active/queued work. MP3s are written
at 320kbps with embedded metadata and cover art, to `~/Downloads` by default.

## Metadata enrichment

After each download, Transmute runs a Claude web search to find the track's canonical
metadata — artist, title, album, release year, genre. Tags are written into the MP3
(cover art is preserved) and the file is renamed to `Artist - Title.mp3`.

Auth (in order of preference):

1. **Claude subscription** (recommended) — if the `claude` CLI (Claude Code) is
   installed and logged in, enrichment runs through it headlessly and bills your
   Pro/Max subscription. To log in: run `claude`, type `/login`, and choose
   "Claude account with subscription". No API key needed.
2. **API key** — if `ANTHROPIC_API_KEY` is set, the Anthropic SDK is used instead
   (billed as API usage). An exported key takes precedence over the subscription.

Toggle with `/enrich on|off`; with no credentials, enrichment is skipped automatically.

## Commands

| Command | Description |
| --- | --- |
| `/out [dir]` | show the output directory; with no arg, opens an inline prompt (pre-filled with `~/`) to change it — enter/esc keeps it |
| `/quality [128\|192\|256\|320]` | show or set MP3 bitrate |
| `/enrich [on\|off]` | toggle web-search metadata enrichment |
| `↑` / `↓` | select failed or low-confidence History entries — Enter retries a failure; low-confidence entries open an inline hint input |
| `/list` | show tracks converted this session |
| `/retry` | requeue failed downloads |
| `/login` | log in to Claude (subscription — opens browser) |
| `/logout` | log out of Claude (disables enrichment) |
| `/clear` | clear the screen |
| `/quit` | exit (or ctrl-d) |
