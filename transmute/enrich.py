"""Metadata enrichment: subscription or API web research → proper ID3 tags."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

ANTHROPIC_MODEL = "claude-opus-4-8"
OPENAI_MODEL = "gpt-5.6-terra"
MAX_SEARCHES = 4
MAX_CONTINUATIONS = 3
BACKEND_LABELS = {
    "codex": "Codex (ChatGPT subscription)",
    "claude": "Claude subscription",
    "openai_api": "OpenAI API",
    "anthropic_api": "Anthropic API",
    "none": "no provider",
}

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["original", "reupload", "derivative"]},
        "based_on": {"type": ["string", "null"]},
        "artist": {"type": "string"},
        "title": {"type": "string"},
        "album": {"type": ["string", "null"]},
        "album_artist": {"type": ["string", "null"]},
        "year": {"type": ["string", "null"]},
        "genre": {"type": ["string", "null"]},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
    "required": [
        "kind",
        "based_on",
        "artist",
        "title",
        "album",
        "album_artist",
        "year",
        "genre",
        "confidence",
    ],
    "additionalProperties": False,
}

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
    kind: str | None = None  # original | reupload | derivative
    based_on: str | None = None  # for derivatives: the source artist/song
    confidence: str | None = None  # high | medium | low (artist attribution)


class Enricher:
    """Metadata lookup through subscription CLIs or provider APIs.

    One API key can be active at a time. A key entered in the app overrides
    environment credentials and subscription CLIs. Otherwise, environment API
    keys take priority, followed by Claude and Codex subscription auth.
    """

    def __init__(self) -> None:
        self.enabled = True
        self.last_error: str | None = None
        self._api_key: str | None = None
        self._api_provider: str | None = None
        self._api_key_source: str | None = None
        self._anthropic_client = None
        self._openai_client = None
        self._select_default_backend()

    def _select_default_backend(self) -> None:
        self.enabled = True
        self.last_error = None
        if os.environ.get("OPENAI_API_KEY"):
            self._set_api_key(
                os.environ["OPENAI_API_KEY"], "openai_api", source="environment"
            )
        elif os.environ.get("ANTHROPIC_API_KEY"):
            self._set_api_key(
                os.environ["ANTHROPIC_API_KEY"],
                "anthropic_api",
                source="environment",
            )
        elif shutil.which("claude"):
            self.backend = "claude"
        elif shutil.which("codex"):
            self.backend = "codex"
        else:
            self.backend = "none"
            self.enabled = False

    @property
    def backend_label(self) -> str:
        return BACKEND_LABELS[self.backend]

    @property
    def has_api_key(self) -> bool:
        return self._api_key is not None

    @property
    def api_key_source(self) -> str | None:
        return self._api_key_source

    def use_backend(self, backend: str) -> None:
        self.backend = backend
        self.enabled = True
        self.last_error = None

    @staticmethod
    def detect_api_provider(key: str) -> str:
        key = key.strip()
        if key.startswith("sk-ant-"):
            return "anthropic_api"
        if key.startswith("sk-"):
            return "openai_api"
        raise ValueError("key must start with sk- (OpenAI) or sk-ant- (Anthropic)")

    def _set_api_key(self, key: str, provider: str, *, source: str) -> None:
        self._api_key = key.strip()
        self._api_provider = provider
        self._api_key_source = source
        self._anthropic_client = None
        self._openai_client = None
        self.use_backend(provider)

    def set_api_key(self, key: str) -> None:
        """Replace the active in-memory key and select its detected provider."""
        provider = self.detect_api_provider(key)
        self._set_api_key(key, provider, source="entered")

    def clear_api_key(self) -> None:
        self._api_key = None
        self._api_provider = None
        self._api_key_source = None
        self._anthropic_client = None
        self._openai_client = None
        self._select_default_backend()

    def use_api_key(self) -> bool:
        if not self._api_provider:
            self.last_error = "no API key configured — run /key"
            return False
        self.use_backend(self._api_provider)
        return True

    def _get_anthropic_client(self):
        if self._anthropic_client is None:
            import anthropic

            self._anthropic_client = anthropic.Anthropic(api_key=self._api_key)
        return self._anthropic_client

    def _get_openai_client(self):
        if self._openai_client is None:
            from openai import OpenAI

            self._openai_client = OpenAI(api_key=self._api_key)
        return self._openai_client

    def lookup(
        self,
        *,
        title: str,
        uploader: str | None,
        duration: int | None,
        url: str,
        description: str | None = None,
        tags: list[str] | None = None,
        hint: str | None = None,
    ) -> TrackTags | None:
        """Research canonical track metadata using the selected backend."""
        self.last_error = None
        desc = (description or "").strip()
        if len(desc) > 1500:
            desc = desc[:1500] + "…"
        prompt = PROMPT.format(
            title=title,
            uploader=uploader or "unknown",
            duration=duration or "unknown",
            url=url,
            page_tags=", ".join(tags) if tags else "none",
            description=desc or "(none)",
        )
        if hint:
            prompt += HINT_SUFFIX.format(hint=hint)
        if self.backend == "codex":
            text = self._ask_codex(prompt)
        elif self.backend == "claude":
            text = self._ask_claude(prompt)
        elif self.backend == "openai_api":
            text = self._ask_openai_api(prompt)
        elif self.backend == "anthropic_api":
            text = self._ask_anthropic_api(prompt)
        else:
            self.last_error = "no enrichment provider — run /key or /login"
            return None
        if text is None:
            return None

        match = JSON_RE.search(text)
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

    def _ask_codex(self, prompt: str) -> str | None:
        """Run a read-only web lookup through Codex with ChatGPT subscription auth."""
        import tempfile

        # Reuse CODEX_HOME/auth when configured, but do not inherit markers that
        # would attach this lookup to the parent Codex/Conductor agent session.
        keep_codex = {"CODEX_HOME", "CODEX_ACCESS_TOKEN", "CODEX_API_KEY"}
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("CODEX_") or key in keep_codex
        }
        try:
            with tempfile.TemporaryDirectory(prefix="transmute-codex-") as run_dir:
                schema_path = Path(run_dir) / "track-tags.schema.json"
                schema_path.write_text(json.dumps(OUTPUT_SCHEMA), encoding="utf-8")
                proc = subprocess.run(
                    [
                        "codex",
                        "--search",
                        "--ask-for-approval",
                        "never",
                        "--disable",
                        "shell_tool",
                        "exec",
                        "--ephemeral",
                        "--ignore-user-config",
                        "--ignore-rules",
                        "--sandbox",
                        "read-only",
                        "--skip-git-repo-check",
                        "--cd",
                        run_dir,
                        "--output-schema",
                        str(schema_path),
                        prompt,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=300,
                    cwd=run_dir,
                    env=env,
                    check=False,
                )
        except FileNotFoundError:
            self.enabled = False
            self.last_error = "codex CLI not found — install Codex or choose /enrich claude"
            return None
        except subprocess.TimeoutExpired:
            self.last_error = "codex CLI timed out"
            return None

        if proc.returncode != 0:
            err = self._last_error_line(proc.stderr or proc.stdout)
            err_lower = err.lower()
            if any(
                marker in err_lower
                for marker in ("log in", "logged in", "authent", "unauthorized")
            ):
                self.enabled = False
                self.last_error = (
                    "not logged in to Codex — run /login codex "
                    "and choose your ChatGPT account"
                )
            else:
                self.last_error = err[:200] or "codex CLI failed"
            return None
        return proc.stdout.strip()

    @staticmethod
    def _last_error_line(output: str) -> str:
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        markers = (
            "error:",
            "not logged in",
            "unauthorized",
            "authentication",
            "rate limit",
            "usage limit",
            "session limit",
            "quota",
        )
        for line in reversed(lines):
            if any(marker in line.lower() for marker in markers):
                return line
        return lines[-1] if lines else ""

    def _ask_claude(self, prompt: str) -> str | None:
        """Run the lookup through headless Claude Code (`claude -p`) on the user's subscription."""
        import tempfile

        # Isolate the run: a neutral cwd (so no project context is ingested) and no
        # inherited Claude Code session vars (so a run from inside another agent
        # doesn't nest into that session).
        env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}
        try:
            proc = subprocess.run(
                [
                    "claude",
                    "-p",
                    "--output-format",
                    "json",
                    "--allowedTools",
                    "WebSearch",
                    "WebFetch",
                ],
                input=prompt,
                capture_output=True,
                text=True,
                timeout=300,
                cwd=tempfile.gettempdir(),
                env=env,
                check=False,
            )
        except subprocess.TimeoutExpired:
            self.last_error = "claude CLI timed out"
            return None

        data = None
        if proc.stdout:
            try:
                data = json.loads(proc.stdout)
            except json.JSONDecodeError:
                pass

        if proc.returncode != 0:
            result = data.get("result") if isinstance(data, dict) else None
            err = str(result or proc.stderr or proc.stdout).strip()
            err_lower = err.lower()
            if any(
                marker in err_lower
                for marker in ("log in", "logged in", "authent", "invalid api key")
            ):
                self.enabled = False
                self.last_error = (
                    "not logged in to Claude — run `claude`, type /login, "
                    "and choose your subscription"
                )
            else:
                self.last_error = err[:200] or "claude CLI failed"
            return None

        if data is None:
            self.last_error = "unparseable claude CLI output"
            return None
        if data.get("is_error"):
            self.last_error = str(data.get("result", "claude CLI error"))[:200]
            return None
        return data.get("result", "")

    def _ask_openai_api(self, prompt: str) -> str | None:
        """Run the lookup through the OpenAI Responses API."""
        import openai

        try:
            response = self._get_openai_client().responses.create(
                model=OPENAI_MODEL,
                input=prompt,
                tools=[{"type": "web_search"}],
                reasoning={"effort": "low"},
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "track_tags",
                        "strict": True,
                        "schema": OUTPUT_SCHEMA,
                    }
                },
                store=False,
            )
        except openai.AuthenticationError:
            self.enabled = False
            self.last_error = "invalid OpenAI API key — run /key to replace it"
            return None
        except Exception as e:  # noqa: BLE001
            self.last_error = str(e)[:200]
            return None
        return response.output_text

    def _ask_anthropic_api(self, prompt: str) -> str | None:
        """Run the lookup through the Anthropic SDK (API key / Console billing)."""
        import anthropic

        client = self._get_anthropic_client()
        messages = [{"role": "user", "content": prompt}]
        tools = [
            {
                "type": "web_search_20260209",
                "name": "web_search",
                "max_uses": MAX_SEARCHES,
            },
            {
                "type": "web_fetch_20260209",
                "name": "web_fetch",
                "max_uses": MAX_SEARCHES,
            },
        ]

        try:
            response = client.messages.create(
                model=ANTHROPIC_MODEL,
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
                messages = messages + [
                    {"role": "assistant", "content": response.content}
                ]
                response = client.messages.create(
                    model=ANTHROPIC_MODEL,
                    max_tokens=4000,
                    thinking={"type": "adaptive"},
                    output_config={"effort": "low"},
                    tools=tools,
                    messages=messages,
                )
        except anthropic.AuthenticationError:
            self.enabled = False
            self.last_error = "invalid Anthropic API key — run /key to replace it"
            return None
        except Exception as e:  # noqa: BLE001
            if "Could not resolve authentication" in str(e):
                self.enabled = False
                self.last_error = "no Anthropic API key found — run /key"
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
        new_path = path.with_name(
            _safe_filename(f"{tags.artist} - {tags.title}") + ".mp3"
        )
        if new_path != path and not new_path.exists():
            path.rename(new_path)
            return new_path
    return path
