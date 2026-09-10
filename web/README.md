# Transmute web prototype

One YouTube or SoundCloud track → source audio → browser-encoded MP3.
There is no enrichment, provider login, tagging, or artwork embedding. The source
track title is used only for the download filename, with invalid characters removed.
Encoding is 320 kbps; this does not improve the source's original audio quality.

## Run from this checkout

Run these commands from the repository root (Node 22.13+ and uv required):

```sh
uv sync
npm --prefix web ci
npm --prefix web run build
uv run python -m web.helper
```

Open http://127.0.0.1:8766. Stop the helper with Ctrl-C.
For interface development, keep the helper running and start
`npm --prefix web run dev` in a second terminal; open http://127.0.0.1:5173.
The development server proxies `/api` to the loopback helper.
`TRANSMUTE_PREVIEW_HOST` can allow a specific authenticated preview hostname.

## Boundaries

- The Python helper retrieves source audio with the existing yt-dlp dependency;
  it does not encode audio or instantiate the Transmute application or Enricher.
- The browser encodes to MP3 with ffmpeg.wasm in a worker, then provides a Save MP3
  link. It strips metadata rather than researching or rewriting it.
- One track per request, at most 20 minutes and 50 MiB of source audio. Playlists,
  profiles, streams, unknown duration and non-HTTPS links are rejected. Retrieval
  has a 150-second deadline, encoding has a 180-second deadline, and concurrency
  uses Transmute's `MAX_WORKERS`. A disconnect does not immediately cancel an
  extraction; it remains bounded by its deadline.
- Source files live in request-owned temporary directories and are removed after
  transfer or failure. Browser buffers are released after conversion. No source
  URLs or media are persisted to Transmute history.
- This is a local prototype, not a deployed public converter. Public hosting needs
  a separately operated extraction service with authentication, rate limits,
  outbound-network isolation and a decision about remote media handling. Sites'
  Cloudflare Workers runtime cannot directly run this Python/yt-dlp subprocess.
  Deploying the static page alone would not provide working link conversion.
- Desktop padding is 30% on each side (40% content width). At 1000px and below it
  becomes 15%; at 600px and below it becomes 24px to keep the form usable.

## Dependencies and licensing

The Sites scaffold supplies the interface framework and controls. `@ffmpeg/ffmpeg`
(MIT) supplies the browser worker wrapper. `@ffmpeg/core` (GPL-2.0-or-later) supplies
FFmpeg and the MP3 encoder; it is compatible with this repository's GPL-3.0-or-later
license. The build copies its unmodified JS/WASM files into ignored static output.
Source and build instructions for the core: https://github.com/ffmpegwasm/ffmpeg.wasm/tree/v0.12.10
Retain applicable notices and provide corresponding source when distributing it.
No Python runtime dependencies or CLI behavior change.

## Validation

```sh
uv run pytest tests/test_web.py
uv run pytest
uv run ruff check .
npm --prefix web exec -- tsc --noEmit -p web/tsconfig.json
npm --prefix web test
npm --prefix web run build
```

Automated tests stub retrieval and subprocesses. Live source checks are manual,
not part of pytest: source availability, bot checks and regional restrictions can
change independently of Transmute. Browser interaction and mobile performance
still require manual validation on the intended devices.
The web test generates a small WAV offline, loads the shipped ESM/WASM converter,
encodes a 320 kbps MP3 without ID3 tags, and decodes it back to PCM under Node.

An optional, feature-detected WebMCP `get_conversion_status` tool reports the same
visible status and ready download link. It does not start downloads or invoke AI.
No supported WebMCP test context was available during prototype validation.
