"""Metadata enrichment: subscription or API web research → proper ID3 tags."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

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
- "derivative" → decide the artist by whether the derivative is its own released \
work. Publishing or re-hosting a track is NOT authorship:
  - A reproduction, edit, flip, bootleg, mashup, sped-up/slowed version, or fan-made \
instrumental remake of an existing song → artist is the ORIGINAL recording artist of \
the underlying song (canonical spelling). The uploader only remade or re-hosted it; \
put their role in the title if catalogued that way (e.g. "Song (reprod. Uploader)") \
and in "based_on", never in the artist field. Example: "2hollis - all of the lights \
(reprod. by me)" uploaded by 3enialis → artist "2hollis", not "3enialis".
  - A distinct work released under the uploader's OWN catalogued artist identity (an \
official remix or a cover credited to them on a real release) → artist is that \
uploader identity, with the source artist and song in "based_on".
Use the derivative's own release info if it was released, else album may be null.
If it's a single or unreleased track, album may be the single name or null.

STEP 3 — If the artist is still unclear (unreleased tracks, leaks, snippets, and \
"if you know you know" uploads often omit the artist entirely): dig into the context. \
Read the page description and tags above for clues (producer tags, "prod.", social \
handles, emoji codes, era names). Fetch the source page itself if useful. Search fan \
communities — Reddit, Genius, leak/tracker databases and wikis — for the title or \
distinctive phrases from the description; these uploads are usually well-documented by \
fans even when unlabeled. Use the community-consensus attribution (e.g. leaked-song \
databases' credited artist and era). Being the uploader or host is not evidence of \
authorship; only as a last resort, when attribution is genuinely unknowable, fall \
back to the uploader name and mark confidence "low".

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

TrackKind = Literal["original", "reupload", "derivative"]
Confidence = Literal["high", "medium", "low"]


@dataclass
class TrackTags:
    artist: str | None = None
    title: str | None = None
    album: str | None = None
    album_artist: str | None = None
    year: str | None = None
    genre: str | None = None
    kind: TrackKind | None = None
    based_on: str | None = None
    confidence: Confidence | None = None


class Enricher:
    """Metadata lookup through subscription CLIs or provider APIs.

    One API key can be active at a time. A key entered in the app overrides
    environment credentials and subscription CLIs. Otherwise, environment API
    keys take priority, followed by Claude and Codex subscription auth.
    """

    def __init__(self) -> None:
        self._error_state = threading.local()
        self._client_lock = threading.RLock()
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
    def backend(self) -> str:
        with self._client_lock:
            return self._backend

    @backend.setter
    def backend(self, value: str) -> None:
        with self._client_lock:
            self._backend = value

    @property
    def enabled(self) -> bool:
        with self._client_lock:
            return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        with self._client_lock:
            self._enabled = value

    @property
    def last_error(self) -> str | None:
        return getattr(self._error_state, "value", None)

    @last_error.setter
    def last_error(self, value: str | None) -> None:
        self._error_state.value = value

    @property
    def has_api_key(self) -> bool:
        with self._client_lock:
            return self._api_key is not None

    @property
    def api_key_source(self) -> str | None:
        with self._client_lock:
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
        with self._client_lock:
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
        with self._client_lock:
            self._api_key = None
            self._api_provider = None
            self._api_key_source = None
            self._anthropic_client = None
            self._openai_client = None
        self._select_default_backend()

    def use_api_key(self) -> bool:
        with self._client_lock:
            provider = self._api_provider
        if not provider:
            self.last_error = "no API key configured — run /key"
            return False
        self.use_backend(provider)
        return True

    def _get_anthropic_client(self):
        with self._client_lock:
            if self._anthropic_client is None:
                import anthropic

                self._anthropic_client = anthropic.Anthropic(api_key=self._api_key)
            return self._anthropic_client

    def _get_openai_client(self):
        with self._client_lock:
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
        try:
            with self._client_lock:
                backend = self._backend
                api_client = (
                    self._get_openai_client()
                    if backend == "openai_api"
                    else (
                        self._get_anthropic_client()
                        if backend == "anthropic_api"
                        else None
                    )
                )
        except Exception as error:  # noqa: BLE001
            self.last_error = self._provider_error(error)
            return None
        if backend == "codex":
            text = self._ask_codex(prompt)
        elif backend == "claude":
            text = self._ask_claude(prompt)
        elif backend == "openai_api":
            text = self._ask_openai_api(prompt, api_client)
        elif backend == "anthropic_api":
            text = self._ask_anthropic_api(prompt, api_client)
        else:
            self.last_error = "no enrichment provider — run /key or /login"
            return None
        if text is None:
            return None

        try:
            return _parse_track_tags(text)
        except ValueError as error:
            self.last_error = str(error)
            return None

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
            self._disable_backend("codex")
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
                self._disable_backend("codex")
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
        except FileNotFoundError:
            self._disable_backend("claude")
            self.last_error = "claude CLI not found — install Claude or choose /enrich codex"
            return None
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
                self._disable_backend("claude")
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

    def _ask_openai_api(self, prompt: str, client=None) -> str | None:
        """Run the lookup through the OpenAI Responses API."""
        import openai

        try:
            client = client or self._get_openai_client()
            response = client.responses.create(
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
            self._disable_backend("openai_api")
            self.last_error = "invalid OpenAI API key — run /key to replace it"
            return None
        except Exception as e:  # noqa: BLE001
            self.last_error = self._provider_error(e)
            return None
        return response.output_text

    def _ask_anthropic_api(self, prompt: str, client=None) -> str | None:
        """Run the lookup through the Anthropic SDK (API key / Console billing)."""
        import anthropic

        client = client or self._get_anthropic_client()
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
                messages = [
                    *messages,
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
            if response.stop_reason == "pause_turn":
                self.last_error = "Anthropic lookup exceeded its continuation limit"
                return None
        except anthropic.AuthenticationError:
            self._disable_backend("anthropic_api")
            self.last_error = "invalid Anthropic API key — run /key to replace it"
            return None
        except Exception as e:  # noqa: BLE001
            if "Could not resolve authentication" in str(e):
                self._disable_backend("anthropic_api")
                self.last_error = "no Anthropic API key found — run /key"
            else:
                self.last_error = self._provider_error(e)
            return None

        return "".join(b.text for b in response.content if b.type == "text")

    def _disable_backend(self, backend: str) -> None:
        with self._client_lock:
            if self._backend == backend:
                self._enabled = False

    def _provider_error(self, error: Exception) -> str:
        lines = [line.strip() for line in str(error).splitlines() if line.strip()]
        message = lines[0] if lines else error.__class__.__name__
        with self._client_lock:
            key = self._api_key
        if key:
            message = message.replace(key, "[redacted]")
        message = re.sub(r"\bsk-[A-Za-z0-9_.-]{8,}", "[redacted]", message)
        return message[:200]


def _parse_track_tags(text: str) -> TrackTags:
    data = _first_json_object(text)
    required = set(OUTPUT_SCHEMA["required"])
    missing = sorted(required - data.keys())
    if missing:
        raise ValueError(f"metadata reply missing fields: {', '.join(missing)}")
    unexpected = sorted(data.keys() - required)
    if unexpected:
        raise ValueError(
            f"metadata reply has unexpected fields: {', '.join(unexpected)}"
        )

    artist = _required_string(data, "artist")
    title = _required_string(data, "title")
    kind = data["kind"]
    if kind not in ("original", "reupload", "derivative"):
        raise ValueError("metadata reply has invalid kind")
    confidence = data["confidence"]
    if confidence not in ("high", "medium", "low"):
        raise ValueError("metadata reply has invalid confidence")

    year = data["year"]
    if year is not None and type(year) not in (str, int):
        raise ValueError("metadata reply has invalid year")

    return TrackTags(
        artist=artist,
        title=title,
        album=_optional_string(data, "album"),
        album_artist=_optional_string(data, "album_artist"),
        year=str(year) if year is not None else None,
        genre=_optional_string(data, "genre"),
        kind=kind,
        based_on=_optional_string(data, "based_on"),
        confidence=confidence,
    )


def _first_json_object(text: str) -> dict:
    decoder = json.JSONDecoder()
    found_start = False
    for match in re.finditer(r"\{", text):
        found_start = True
        try:
            value, _ = decoder.raw_decode(text[match.start() :])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    if found_start:
        raise ValueError("unparseable JSON in model reply")
    raise ValueError("no JSON in model reply")


def _required_string(data: dict, field: str) -> str:
    value = data[field]
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"metadata reply has invalid {field}")
    return value.strip()


def _optional_string(data: dict, field: str) -> str | None:
    value = data[field]
    if value is not None and not isinstance(value, str):
        raise ValueError(f"metadata reply has invalid {field}")
    if value is None:
        return None
    return value.strip() or None


def _safe_filename(name: str) -> str:
    return re.sub(r'[/\\:*?"<>|\x00]', "_", name).strip()


def _unique_path(target: Path) -> Path:
    """Return `target`, or the first `name (n).ext` variant that does not exist.

    The rename target may already hold an unrelated earlier download; never
    overwrite it, but do not fall back to yt-dlp's raw `uploader - title` name
    either, which carries the uploader alias alongside the real artist.
    """
    if not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    counter = 1
    while True:
        candidate = target.with_name(f"{stem} ({counter}){suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def apply_tags(path: Path, tags: TrackTags) -> Path:
    """Update owned ID3 fields and rename without replacing an existing file."""
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
        target = path.with_name(
            _safe_filename(f"{tags.artist} - {tags.title}") + ".mp3"
        )
        if target != path:
            new_path = _unique_path(target)
            path.rename(new_path)
            return new_path
    return path
