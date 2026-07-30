"""Metadata enrichment: Claude + web search → proper ID3 tags."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

MODEL = "claude-opus-4-8"
MAX_SEARCHES = 4
MAX_CONTINUATIONS = 3

PROMPT = """\
You are a music metadata expert identifying a track uploaded to YouTube/SoundCloud.

Track info from the source site:
- title: {title}
- uploader: {uploader}
- duration: {duration} seconds
- source url: {url}
- page tags: {page_tags}
- page description (verbatim from the upload):
{description}

STEP 1 — Classify the upload. Before anything else, use web search to determine which \
of these this specific recording is:
- "original": the artist's own official recording (uploaded by them or their label)
- "reupload": someone else re-hosting an official recording unchanged
- "derivative": a cover, remix, edit, flip, bootleg, mashup, or sped/slowed version \
made BY the uploader
Evidence to weigh: research the uploader account itself (handles are often aliases of \
known artists — check the display name, music databases like MusicBrainz/RateYourMusic, \
artist pages); compare the duration against the official release; check whether the \
track appears in the uploader's own sets/albums; genre tags like remix/edit/flip/\
nightcore/dariacore.

STEP 2 — Attribute and find release info for THAT recording:
- "original" or "reupload" → artist is the original artist (canonical spelling, never \
the channel name); use the official release's album/year/genre.
- "derivative" → artist is the uploader's artist identity (their catalogued alias); \
keep the original artist out of the artist field (they may appear in the title if \
catalogued that way, e.g. "Song (Artist flip)"); use the derivative's own release \
info if it was released, else album may be null.
If it's a single or unreleased track, album may be the single name or null.

STEP 3 — If the artist is still unclear (unreleased tracks, leaks, snippets, and \
"if you know you know" uploads often omit the artist entirely): dig into the context. \
Read the page description and tags above for clues (producer tags, "prod.", social \
handles, emoji codes, era names). Fetch the source page itself if useful. Search fan \
communities — Reddit, Genius, leak/tracker databases and wikis — for the title or \
distinctive phrases from the description; these uploads are usually well-documented by \
fans even when unlabeled. Use the community-consensus attribution (e.g. leaked-song \
databases' credited artist and era). Only if attribution is genuinely unknowable, \
fall back to the uploader name.

Respond with ONLY a JSON object, no other text:
{{"kind": "original"|"reupload"|"derivative", "based_on": str|null, "artist": str, \
"title": str, "album": str|null, "album_artist": str|null, "year": str|null, \
"genre": str|null, "confidence": "high"|"medium"|"low"}}
("based_on" = the original artist and song a derivative is based on, else null.
"confidence" = how sure you are of the ARTIST attribution specifically: "high" only if \
confirmed by an official source or strong community consensus; "low" if you found no \
corroboration and are guessing from weak signals like the uploader name.)"""

HINT_SUFFIX = """

USER HINT: the user provided this additional context — treat it as the most reliable \
signal and re-verify against it: {hint}"""

JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


@dataclass
class TrackTags:
    artist: str | None = None
    title: str | None = None
    album: str | None = None
    album_artist: str | None = None
    year: str | None = None
    genre: str | None = None
    kind: str | None = None        # original | reupload | derivative
    based_on: str | None = None    # for derivatives: the source artist/song
    confidence: str | None = None  # high | medium | low (artist attribution)


class Enricher:
    """Metadata lookup via Claude.

    Prefers the local `claude` CLI (billed to the user's Claude subscription — the
    same login Claude Code uses). Falls back to the Anthropic SDK only when
    ANTHROPIC_API_KEY is explicitly set. Disables itself on auth failure.
    """

    def __init__(self) -> None:
        self.enabled = True
        self.last_error: str | None = None
        self._client = None
        if os.environ.get("ANTHROPIC_API_KEY"):
            self.backend = "sdk"
        elif shutil.which("claude"):
            self.backend = "subscription"
        else:
            self.backend = "sdk"  # last resort: SDK's own credential resolution

    def _get_client(self):
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic()
        return self._client

    def lookup(
        self, *, title: str, uploader: str | None, duration: int | None, url: str,
        description: str | None = None, tags: list[str] | None = None,
        hint: str | None = None,
    ) -> TrackTags | None:
        """Ask Claude (with web search + fetch) for canonical track metadata."""
        desc = (description or "").strip()
        if len(desc) > 1500:
            desc = desc[:1500] + "…"
        prompt = PROMPT.format(
            title=title, uploader=uploader or "unknown",
            duration=duration or "unknown", url=url,
            page_tags=", ".join(tags) if tags else "none",
            description=desc or "(none)",
        )
        if hint:
            prompt += HINT_SUFFIX.format(hint=hint)
        if self.backend == "subscription":
            text = self._ask_subscription(prompt)
        else:
            text = self._ask_sdk(prompt)
        if text is None:
            return None

        match = None
        for match in JSON_RE.finditer(text):
            pass  # keep the last JSON object in the reply
        if not match:
            self.last_error = "no JSON in model reply"
            return None
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            self.last_error = "unparseable JSON in model reply"
            return None

        return TrackTags(
            artist=data.get("artist"),
            title=data.get("title"),
            album=data.get("album"),
            album_artist=data.get("album_artist"),
            year=str(data["year"]) if data.get("year") else None,
            genre=data.get("genre"),
            kind=data.get("kind"),
            based_on=data.get("based_on"),
            confidence=data.get("confidence"),
        )

    def _ask_subscription(self, prompt: str) -> str | None:
        """Run the lookup through headless Claude Code (`claude -p`) on the user's subscription."""
        import tempfile

        # Isolate the run: a neutral cwd (so no project context is ingested) and no
        # inherited Claude Code session vars (so a run from inside another agent
        # doesn't nest into that session).
        env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}
        try:
            proc = subprocess.run(
                ["claude", "-p", "--output-format", "json", "--allowedTools", "WebSearch", "WebFetch"],
                input=prompt, capture_output=True, text=True, timeout=300,
                cwd=tempfile.gettempdir(), env=env,
            )
        except subprocess.TimeoutExpired:
            self.last_error = "claude CLI timed out"
            return None
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout).strip()
            if "log in" in err.lower() or "authent" in err.lower():
                self.enabled = False
                self.last_error = (
                    "not logged in to Claude — run `claude`, type /login, "
                    "and choose your subscription"
                )
            else:
                self.last_error = err[:200] or "claude CLI failed"
            return None
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            self.last_error = "unparseable claude CLI output"
            return None
        if data.get("is_error"):
            self.last_error = str(data.get("result", "claude CLI error"))[:200]
            return None
        return data.get("result", "")

    def _ask_sdk(self, prompt: str) -> str | None:
        """Run the lookup through the Anthropic SDK (API key / Console billing)."""
        import anthropic

        client = self._get_client()
        messages = [{"role": "user", "content": prompt}]
        tools = [
            {"type": "web_search_20260209", "name": "web_search", "max_uses": MAX_SEARCHES},
            {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": MAX_SEARCHES},
        ]

        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=4000,
                thinking={"type": "adaptive"},
                output_config={"effort": "low"},
                tools=tools,
                messages=messages,
            )
            # Server-side tools may pause the turn; re-send to continue.
            for _ in range(MAX_CONTINUATIONS):
                if response.stop_reason != "pause_turn":
                    break
                messages = messages + [{"role": "assistant", "content": response.content}]
                response = client.messages.create(
                    model=MODEL,
                    max_tokens=4000,
                    thinking={"type": "adaptive"},
                    output_config={"effort": "low"},
                    tools=tools,
                    messages=messages,
                )
        except anthropic.AuthenticationError:
            self.enabled = False
            self.last_error = (
                "no valid Anthropic API key — set ANTHROPIC_API_KEY to enable metadata enrichment"
            )
            return None
        except Exception as e:
            if "Could not resolve authentication" in str(e):
                self.enabled = False
                self.last_error = (
                    "no Anthropic API key found — set ANTHROPIC_API_KEY to enable metadata enrichment"
                )
            else:
                self.last_error = str(e)[:200]
            return None

        return "".join(b.text for b in response.content if b.type == "text")


def _safe_filename(name: str) -> str:
    return re.sub(r'[/\\:*?"<>|\x00]', "_", name).strip()


def apply_tags(path: Path, tags: TrackTags) -> Path:
    """Write ID3 tags into the MP3 (keeps embedded cover art) and rename to Artist - Title.mp3."""
    from mutagen.easyid3 import EasyID3
    from mutagen.id3 import ID3NoHeaderError

    try:
        audio = EasyID3(path)
    except ID3NoHeaderError:
        from mutagen.mp3 import MP3

        mp3 = MP3(path)
        mp3.add_tags()
        mp3.save()
        audio = EasyID3(path)

    if tags.title:
        audio["title"] = tags.title
    if tags.artist:
        audio["artist"] = tags.artist
    if tags.album:
        audio["album"] = tags.album
    if tags.album_artist:
        audio["albumartist"] = tags.album_artist
    if tags.year:
        audio["date"] = tags.year
    if tags.genre:
        audio["genre"] = tags.genre
    audio.save()

    if tags.artist and tags.title:
        new_path = path.with_name(_safe_filename(f"{tags.artist} - {tags.title}") + ".mp3")
        if new_path != path and not new_path.exists():
            path.rename(new_path)
            return new_path
    return path
