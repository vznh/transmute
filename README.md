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
  

## Install

### Homebrew

```sh
brew tap vznh/transmute https://github.com/vznh/transmute
brew trust vznh/transmute
brew install transmute
```

ffmpeg is installed as a dependency, so there is nothing else to set up. Then
run `transmute`.

Three commands rather than one, for two reasons worth knowing once:

- **The tap names a URL** because this repository is its own Homebrew tap.
  Homebrew otherwise derives a tap's location from its name and would look for a
  separate `vznh/homebrew-transmute` repository, which does not exist.
- **The trust step is required**, not optional. Homebrew refuses to load a
  formula from an untrusted third-party tap. Trust is recorded per remote URL,
  so you grant it once.

Both are one-time. Later releases need only `brew upgrade transmute`.

Homebrew builds 104 Python dependencies from source, including native Rust and
C extensions for iPhone sync. Allow about 10–20 minutes for the first install;
download and compiler caches can shorten later builds.

### From a checkout

```sh
uv sync
uv run transmute
```

Run both commands from the repository root. After the first `uv sync`, starting
Transmute only requires `uv run transmute`. ffmpeg is not installed for you here
— see Requirements below.

### From PyPI

`transmute-cli` is published on PyPI. Install the current release with:

```sh
uv tool install "transmute-cli==0.2.5"   # or: pipx install "transmute-cli==0.2.5"
brew install ffmpeg                        # not included on this path
```

### Checking what you have

```sh
transmute --version   # transmute 0.2.5
```

`brew list --versions transmute` reports `0.2.5` for the PyPI-based formula.
The Homebrew formula installs the same PyPI release.

## Requirements

- ffmpeg — installed for you by Homebrew, otherwise `brew install ffmpeg`
- Claude Code signed in with Claude, or Codex CLI signed in with ChatGPT, for
  subscription-backed metadata enrichment
- [uv](https://docs.astral.sh/uv/) — only to run from a checkout or to develop

## Use

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
Both CLI and API enrichment pin OpenAI/Codex to `gpt-6-luna` and Anthropic/Claude
to `claude-haiku-5-5`.

Auth (in order of preference):

1. **Entered API key** — run `/key` to open a masked prompt. It accepts one
   OpenAI `sk-…` or Anthropic `sk-ant-…` key, auto-detects the provider, and
   replaces any previously entered key. The key stays in memory only and is
   never written to Transmute's command history. Use `/key clear` to remove it.
2. **Environment API key** — `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` is selected
   automatically. If both are set, OpenAI takes priority. API usage is billed
   separately by the detected provider.
3. **Claude subscription** (recommended) — when the local `claude` CLI is signed
   in, Transmute uses its Claude subscription auth. Run `/login` or `/login claude` to
   sign in.
4. **ChatGPT subscription** — when Claude is not signed in, Transmute runs an
   ephemeral, read-only `codex exec` lookup with live web search and local shell
   access disabled. Run `/login codex` once to sign in with ChatGPT.

Among subscription CLIs, Transmute picks the first signed-in CLI (Claude, then
Codex). While neither is available, it checks sign-in status every 15 seconds and
enables enrichment when one becomes available.

Choose a provider with `/enrich codex|claude|api`, or toggle the selected provider
with `/enrich on|off`. With no usable credentials, enrichment is skipped
automatically.

## Spotify iPhone sync

Connect and unlock one iPhone over USB, trust the computer if prompted, and
install Spotify on the phone. Run `/sync` (or `/sync spotify`) to scan every MP3
in the current output folder and Spotify's app documents, then review the
read-only preview. Exact matches are marked **Already synced**; possible
duplicates and filename conflicts are dimmed and marked with `?` for review.
Nothing transfers until you select tracks and move to **Continue**.

In the preview, use Up/Down to browse, Enter to select a row, `h` to select
high-confidence items, and `s` to select every transferable item. Enter on
Continue starts the selected copies. Tab or Shift-Tab switches between the
report and URL input without clearing its draft. The report hint says `Tab to
input`; the input hint shows dim `Tab back to Sync`. Press `q` or Esc to close the
report; `/sync close` also works from the input line. While copying, these ask
Transmute to stop between files. Completed copies stay done,
and `/sync retry` reopens the failed or cancelled rows. Sync only adds files; it
does not delete phone or computer files or replace existing files.

If your iPhone does not support safe phone-side publishing, use Finder: select
the connected iPhone, open its **Files** tab, and drag the desired MP3s from the
output folder onto **Spotify**. If Finder reports a filename conflict, keep both
files or skip the copy. Run `/sync` again to compare the folders after copying.

`/sync music` explains the manual Mac workflow: import the output folder into
Music, then use Finder's iPhone Music sync. Copying files to a folder does not
register them in Apple Music, and exporting tracks back from the iPhone Music
library is unsupported.

## Commands

| Command | Description |
| --- | --- |
| `/out [dir]` | show or set the output directory; a bare name or folder-editor entry is under `~/`; with no arg, opens a separate `~/` editor — Tab (or Shift-Tab) switches focus, Enter applies, Esc cancels |
| `/quality [128\|192\|256\|320]` | show or set MP3 bitrate |
| `/enrich [codex\|claude\|api\|on\|off]` | choose or toggle web-search metadata enrichment |
| `/key [clear]` | securely enter one OpenAI or Anthropic API key, or clear it |
| `↑` / `↓` | select failed or low-confidence History entries — Enter retries a failure; low-confidence entries open an inline hint input |
| `/list` | show recent converted and failed tracks across sessions |
| `/retry` | requeue all retryable failed downloads |
| `/sync [spotify\|music\|retry\|close]` | preview additive Spotify sync, show the Mac Music/Finder handoff, reopen failed/cancelled rows, or close the report |
| `/login [codex\|claude]` | log in to a subscription provider (opens browser) |
| `/logout [codex\|claude]` | log out of a subscription provider |
| `/clear` | clear completed and failed activity from the screen and persistent history |
| `/help` | show the full command reference while keeping URL and command input live; Esc closes help |
| `/quit` | exit (also `/exit`, `/q`, Ctrl-D, or double Ctrl-C) |

The main input remains available while `/help`, `/sync`, or `/out` is open.
With the sync report open, press Tab or Shift-Tab to switch focus; its selection
shortcuts only apply while the report has focus. While the folder editor is
open, press Tab (when no completion menu is active) or Shift-Tab to switch
between it and URL input. Directory completion
continues to use Tab in the folder editor. Folder edits take effect only after
Enter confirms them; downloads submitted before then use the currently applied
settings. With help open, scroll over the command reference with the mouse wheel
or trackpad. If `/help` and `/out` are both open, Esc closes the folder editor
first, then closes help on the next press.

## Development

### Browser prototype

The optional [web prototype](web/README.md) provides a basic YouTube/SoundCloud
link-to-MP3 interface without enrichment. A local helper retrieves source audio;
the browser converts it to MP3. It is a checkout-only prototype, not a hosted
service, and does not change the terminal application.

### Checks

```sh
uv run pytest
uv run ruff check .
```

If [`just`](https://just.systems/) is installed, the same workflows are available
as `just run`, `just test`, `just lint`, and `just check`.

Releasing to PyPI and the Homebrew tap is described in
[`packaging/README.md`](packaging/README.md). After a dependency or version
change, regenerate the formula with `just formula`.

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
        ├── sync (preview, selection results, safe local/phone transfer)
        │     └── iphone (USB Spotify Documents adapter)
        └── settings, history (durable local storage)
```

Entry point

- `transmute/main.py` — process startup, `--version`/`--help`, and wiring
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
- `transmute/sync.py` — read-only MP3 comparison plans and explicit transfers
- `transmute/iphone.py` — USB-only adapter for Spotify's vended Documents folder
- `transmute/settings.py` — persistent output directory and bitrate storage
- `transmute/history.py` — persistent download activity storage

Tests

- `tests/test_main.py` — entry-point argument handling
- `tests/test_app.py` — selection, retry, modal, queue, and notice state
- `tests/test_downloader.py` — URL parsing, yt-dlp options, and error classification
- `tests/test_enrich.py` — credentials, provider calls, tagging, and renaming
- `tests/test_settings.py` — settings round-trip, rejection, and fallback
- `tests/test_history.py` — activity storage, retry/hint claiming, session recovery
- `tests/test_packaging.py` — Homebrew resource closure derived from `uv.lock`

## License

Transmute is licensed under the [GNU General Public License v3.0 or later](LICENSE).

Copyright (C) 2026 vznh.

The licence is copyleft because dependencies require it, not by preference:
`transmute/enrich.py` imports [mutagen](https://github.com/quodlibet/mutagen)
to write ID3 frames (GPL-2.0-or-later), and the USB sync adapter uses
[pymobiledevice3](https://github.com/doronz88/pymobiledevice3) (GPL-3.0-or-later).
The project is GPL-3.0-or-later to remain compatible with both. ffmpeg is a
separate program invoked as a subprocess and does not affect this.
