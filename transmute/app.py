"""Application state, rendering, and the background download/enrich pipeline.

Layout lives in layout.py, key bindings in keys.py, slash commands in
commands.py; this module owns the shared state they all act on.

History entries that failed or need a hint are navigable with ↑/↓: failed
entries retry on Enter; low-confidence entries open an inline hint input.
"""

from __future__ import annotations

import shutil
import threading
from collections import deque
from concurrent.futures import Executor, ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.layout.dimension import Dimension

from .commands import HELP_ROWS, Commands
from .config import HISTORY_FILE, MAX_WORKERS, Settings
from .downloader import Job, download_job, extract_urls, is_supported_url
from .enrich import Enricher, TrackTags, apply_tags
from .keys import build_key_bindings
from .layout import build_layout
from .style import HELP_HINT, HISTORY_SHOWN, MESSAGES_SHOWN, PLACEHOLDER, STYLE, TAGLINE
from .widgets import Modal

EntryKind = Literal["info", "ok", "err", "hint"]


@dataclass
class Entry:
    style: str
    line: str
    kind: EntryKind = "info"
    job: Job | None = None
    detail: str | None = None  # indented metadata rendered under `line`


@dataclass
class DirPicker:
    """Interactive output-directory picker driven from /out (no-arg form).

    The input buffer holds only the path relative to ~/ (the "~/" is a fixed,
    uneditable prompt prefix). `stage` is "input" while a path is being typed
    and "confirm" while asking y/n to create a folder that doesn't exist yet.
    """

    stage: str = "input"  # input | confirm
    typed: str = ""  # relative path the create prompt is asking about
    pending: Path | None = None  # absolute path awaiting create confirmation


class App:
    def __init__(
        self,
        *,
        history_file: Path = HISTORY_FILE,
        pool: Executor | None = None,
    ) -> None:
        self.settings = Settings()
        self.enricher = Enricher()
        self.completed: list[Job] = []
        self.failed: list[Job] = []
        self.history: list[Entry] = []
        self.messages: deque[tuple[str, str]] = deque(maxlen=MESSAGES_SHOWN)
        self.pool = pool or ThreadPoolExecutor(max_workers=MAX_WORKERS)
        self.lock = threading.Lock()
        self.active: dict[int, str] = {}
        self.queued = 0
        self.sel: int | None = None
        self.modal: Modal | None = None
        self.dir_picker: DirPicker | None = None
        self.help_open = False
        self.input_notice: tuple[str, str] | None = None
        self._input_notice_id = 0
        self._seq = 0
        self._last_ctrl_c = 0.0
        history_file.parent.mkdir(parents=True, exist_ok=True)
        self.commands = Commands(self)
        layout = build_layout(self, history_file)
        self.app = Application(
            layout=layout,
            key_bindings=build_key_bindings(self),
            style=STYLE,
            full_screen=True,
        )

    # ── rendering ───────────────────────────────────────────────────────

    @staticmethod
    def _cols() -> int:
        from prompt_toolkit.application.current import get_app

        return get_app().output.get_size().columns

    def _render_header(self):
        return [
            ("", "\n"),
            ("class:header.bar", "▌ "),
            ("class:header.title", "Transmute\n"),
            ("class:header.bar", "▌ "),
            ("class:header.desc", TAGLINE + "\n"),
        ]

    def _render_help(self):
        """The /help takeover body: the full command reference, rendered in one
        pass so nothing is clipped by the rolling message log."""
        out = [("class:subsection", "\n  Commands\n")]
        for cmd, desc in HELP_ROWS:
            out.append(("class:dim", f"    {cmd:<34}{desc}\n"))
        return out

    def _build(self):
        """Body fragments, split where the inline hint input goes (below the
        selected low-confidence entry). Returns (above, below)."""
        if self.help_open:
            return self._render_help(), []
        with self.lock:
            active = list(self.active.values())
            queued = self.queued
            hist = list(self.history)
            msgs = list(self.messages)
            sel = self.sel
        cols = self._cols()
        width = max(cols - 8, 20)
        rule = ("class:rule", "  " + "─" * max(cols - 4, 10) + "\n")

        above: list[tuple[str, str]] = []
        below: list[tuple[str, str]] = []
        cur = above

        cur.append(("class:subsection", "\n  Processing\n"))
        if not active and not queued:
            cur.append(("class:dim", "    idle\n"))
        for status in active:
            cur.append(("class:accent", f"    ⠿ {status[:width]}\n"))
        if queued:
            cur.append(("class:dim", f"    ⧗ {queued} queued\n"))

        cur.append(rule)
        cur.append(("class:subsection", "  History\n"))
        if not hist:
            cur.append(("class:dim", "    nothing yet\n"))
        start = max(0, len(hist) - HISTORY_SHOWN)
        if sel is not None and sel < start:
            start = sel
        if start > 0:
            cur.append(("class:dim", f"    … {start} earlier\n"))
        for i in range(start, len(hist)):
            e = hist[i]
            style = f"{e.style} class:selected" if i == sel else e.style
            cur.append((style, f"    {e.line[:width]}\n"))
            if e.detail:
                cur.append(("class:dim", f"      {e.detail[:width]}\n"))
            if i == sel and e.kind == "hint":
                cur = below  # inline hint input renders between above/below

        if msgs:
            cur.append(rule)
            cur.append(("class:subsection", "  Logs\n"))
            for style, line in msgs:
                cur.append((style, f"    {line[:width]}\n"))
        return above, below

    def _render_above(self):
        return self._build()[0]

    def _render_below(self):
        return self._build()[1]

    def _above_height(self):
        return Dimension.exact(sum(text.count("\n") for _, text in self._build()[0]))

    def _toolbar(self):
        with self.lock:
            active = len(self.active)
            queued = self.queued
            settings = self.settings
        parts = [str(settings.out_dir), f"{settings.quality}k"]
        if active:
            parts.append(f"{active} active")
        if queued:
            parts.append(f"{queued} queued")
        return [("class:toolbar", "  " + "  ·  ".join(parts) + " ")]

    def _input_prefix(self):
        """Prompt-line prefix fragments. The picker's "~/" is rendered here
        (not in the buffer) so it can't be edited or deleted."""
        if self.dir_picker and self.dir_picker.stage == "input":
            return [("class:prompt", "❯ "), ("class:accent", "~/")]
        if self.modal:
            return [("class:prompt", self.modal.prefix)]
        return [("class:prompt", "❯ ")]

    def _input_placeholder(self) -> str:
        if self.help_open:
            return ""
        if self.dir_picker:
            if self.dir_picker.stage == "confirm":
                return f"~/{self.dir_picker.typed} doesn't exist — create it? (y/n)"
            return "type a folder under home — tab to complete, enter to set"
        if self.modal:
            return self.modal.placeholder
        return PLACEHOLDER

    def _input_hint(self):
        if self.help_open:
            return [("class:input.hint", "  esc closes help")]
        if self.dir_picker is not None:
            return [("class:input.hint", "  esc to cancel output directory change")]
        with self.lock:
            notice = self.input_notice
            actionable = any(e.kind in ("err", "hint") for e in self.history)
            modal = self.modal
        if notice:
            style, text = notice
        elif modal:
            style, text = "class:input.hint", modal.hint
        elif actionable:
            style, text = (
                "class:input.hint",
                "↑/↓ to select · enter retries · esc deselects",
            )
        else:
            style, text = "class:input.hint", HELP_HINT
        return [(style, f"  {text}")]

    # ── selection ───────────────────────────────────────────────────────

    def _actionable(self) -> list[int]:
        with self.lock:
            return [i for i, e in enumerate(self.history) if e.kind in ("err", "hint")]

    def _sel_kind(self) -> str | None:
        with self.lock:
            if self.sel is None or self.sel >= len(self.history):
                return None
            return self.history[self.sel].kind

    def has_selection(self) -> bool:
        with self.lock:
            return self.sel is not None

    def _move_sel(self, delta: int) -> None:
        with self.lock:
            acts = [
                i for i, entry in enumerate(self.history) if entry.kind in ("err", "hint")
            ]
            if not acts:
                self.sel = None
            elif self.sel is None:
                if delta < 0:
                    self.sel = acts[-1]
            else:
                pos = acts.index(self.sel) if self.sel in acts else len(acts) - 1
                pos += delta
                self.sel = None if pos >= len(acts) else acts[max(pos, 0)]
        self._update_focus()
        self.refresh()

    def _update_focus(self) -> None:
        if not hasattr(self, "app"):
            return
        if self._sel_kind() == "hint":
            self.app.layout.focus(self.hint_buffer)
        else:
            self.app.layout.focus(self.input_buffer)

    # ── state helpers (safe from any thread) ────────────────────────────

    def refresh(self) -> None:
        if hasattr(self, "app"):
            self.app.invalidate()

    def msg(self, style: str, line: str) -> None:
        with self.lock:
            self.messages.append((style, line))
        self.refresh()

    def settings_snapshot(self) -> Settings:
        with self.lock:
            return self.settings

    def set_out_dir(self, out_dir: Path) -> None:
        with self.lock:
            self.settings = replace(self.settings, out_dir=out_dir)
        self.refresh()

    def set_quality(self, quality: str) -> None:
        with self.lock:
            self.settings = replace(self.settings, quality=quality)
        self.refresh()

    def clear_selection(self) -> None:
        with self.lock:
            self.sel = None
        self.hint_buffer.reset()
        self._update_focus()
        self.refresh()

    def show_input_notice(
        self, style: str, line: str, *, duration: float | None = None
    ) -> None:
        with self.lock:
            self._input_notice_id += 1
            notice_id = self._input_notice_id
            self.input_notice = (style, line)
        self.refresh()
        if duration is not None:
            timer = threading.Timer(duration, self._clear_input_notice, (notice_id,))
            timer.daemon = True
            timer.start()

    def clear_input_notice(self) -> None:
        with self.lock:
            self._input_notice_id += 1
            self.input_notice = None
        self.refresh()

    def _clear_input_notice(self, notice_id: int) -> None:
        with self.lock:
            if notice_id != self._input_notice_id:
                return
            self.input_notice = None
        self.refresh()

    def add_entry(self, entry: Entry) -> None:
        with self.lock:
            self.history.append(entry)
        self.refresh()

    def _remove_entry(self, entry: Entry) -> None:
        with self.lock:
            try:
                idx = self.history.index(entry)
            except ValueError:
                return
            del self.history[idx]
            if self.sel is not None and self.sel >= idx:
                self.sel = self.sel - 1 if self.sel > idx else None
        self.refresh()

    # ── output-directory picker ─────────────────────────────────────────

    def open_dir_picker(self) -> None:
        with self.lock:
            self.dir_picker = DirPicker()
            self.sel = None
        self.input_buffer.reset()
        self._update_focus()
        self.refresh()

    def close_dir_picker(self) -> None:
        with self.lock:
            self.dir_picker = None
        self.input_buffer.reset()
        self.refresh()

    def _apply_out_dir(self, path: Path) -> None:
        self.set_out_dir(path)
        self.msg("class:dim", f"output directory: {path}")

    def _accept_dir(self, buff: Buffer) -> None:
        """Enter in the path stage: use the folder if it exists, else offer to
        create it."""
        picker = self.dir_picker
        if picker is None or picker.stage != "input":
            return
        rel = buff.text.strip().lstrip("/")
        target = Path.home() / rel if rel else Path.home()
        if target.is_dir():
            self._apply_out_dir(target)
            self.close_dir_picker()
            return
        picker.stage = "confirm"
        picker.typed = rel
        picker.pending = target
        buff.reset()  # cleared while we ask y/n
        self.refresh()

    def _dir_confirm_create(self) -> None:
        """y in the confirm stage: make the folder, adopt it, and exit."""
        picker = self.dir_picker
        if picker is None or picker.pending is None:
            return
        target = picker.pending
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            self.msg("class:err", f"can't create {target}: {e}")
            picker.stage = "input"
            picker.pending = None
            self.input_buffer.set_document(
                Document(picker.typed, len(picker.typed)), bypass_readonly=True
            )
            self.refresh()
            return
        self.msg("class:ok", f"created {target}")
        self._apply_out_dir(target)
        self.close_dir_picker()

    def _dir_decline_create(self) -> None:
        """n in the confirm stage: return to the path input, restoring the text."""
        picker = self.dir_picker
        if picker is None:
            return
        rel = picker.typed
        picker.stage = "input"
        picker.pending = None
        self.input_buffer.set_document(
            Document(rel, len(rel)), bypass_readonly=True
        )
        self.refresh()

    def open_help(self) -> None:
        self.help_open = True
        self.sel = None
        self.input_buffer.reset()
        self._update_focus()
        self.refresh()

    def close_help(self) -> None:
        self.help_open = False
        self.refresh()

    def open_modal(self, modal: Modal) -> None:
        with self.lock:
            self.modal = modal
        self.input_buffer.set_document(
            Document(modal.initial, len(modal.initial)), bypass_readonly=True
        )
        self.refresh()

    def close_modal(self) -> None:
        with self.lock:
            self.modal = None
        self.input_buffer.reset()
        self.refresh()

    # ── background pipeline ─────────────────────────────────────────────

    @staticmethod
    def _display_name(job: Job) -> str:
        return job.path.name if job.path else (job.title or job.url)

    def _set_active(self, seq: int, text: str) -> None:
        with self.lock:
            self.active[seq] = text
        self.refresh()

    def _note_done(self, job: Job, tags: TrackTags | None) -> None:
        detail = self._detail_line(tags) if tags else None
        self.add_entry(
            Entry("class:ok", f"✔ {self._display_name(job)}", "ok", job, detail=detail)
        )

    def _note_low_confidence(self, job: Job, again: bool = False) -> None:
        tail = (
            "still unconfirmed, ↑ to add another hint"
            if again
            else "artist unconfirmed, ↑ to add a hint"
        )
        self.add_entry(
            Entry("class:warn", f"⚠ “{(job.title or '')[:40]}” — {tail}", "hint", job)
        )

    def _note_failed(self, job: Job) -> None:
        with self.lock:
            if not any(existing is job for existing in self.failed):
                self.failed.append(job)
        if job.retryable:
            line = (
                f"✘ {job.url[:44]}  {job.error or 'unknown error'}"
                " — ↑ + enter to retry"
            )
            kind: EntryKind = "err"
        else:
            line = f"✘ {job.url[:44]}  {job.error or 'unknown error'}"
            kind = "info"
        self.add_entry(
            Entry(
                "class:err",
                line,
                kind,
                job,
            )
        )

    def _record_completed(self, job: Job) -> None:
        with self.lock:
            if not any(existing is job for existing in self.completed):
                self.completed.append(job)

    def submit_urls(self, urls: list[str]) -> None:
        with self.lock:
            self.queued += len(urls)
            settings = self.settings
        self.refresh()
        for url in urls:
            self.pool.submit(self._process, url, settings)

    def _process(self, url: str, settings: Settings) -> None:
        with self.lock:
            self._seq += 1
            seq = self._seq
            self.queued -= 1
            self.active[seq] = f"starting  {url[:50]}"
        self.refresh()
        job = Job(url=url)

        def on_progress(j: Job, frac: float | None) -> None:
            name = (j.title or j.url)[:44]
            phase = "converting  " if frac is None else f"↓ {frac:>4.0%}  "
            self._set_active(seq, phase + name)

        try:
            download_job(job, settings, on_progress)
            if job.status != "done":
                self._note_failed(job)
                return

            tags = None
            if self.enricher.enabled:
                self._set_active(seq, f"enriching  {(job.title or '')[:44]}")
                tags = self._enrich(job)

            self._record_completed(job)
            self._note_done(job, tags)
            if tags is not None and tags.confidence == "low":
                self._note_low_confidence(job)
        except Exception as error:  # noqa: BLE001
            message = self._error_line(error)
            if job.status == "done":
                self.msg("class:warn", f"⚠ post-processing skipped: {message}")
                self._record_completed(job)
                self._note_done(job, None)
            else:
                job.status = "error"
                job.error = message
                self._note_failed(job)
        finally:
            with self.lock:
                self.active.pop(seq, None)
            self.refresh()

    def _enrich(
        self,
        job: Job,
        hint: str | None = None,
        *,
        failure_label: str = "enrichment skipped",
    ) -> TrackTags | None:
        try:
            tags = self.enricher.lookup(
                title=job.title or "",
                uploader=job.uploader,
                duration=job.duration,
                url=job.url,
                description=job.description,
                tags=job.tags,
                hint=hint,
            )
        except Exception as error:  # noqa: BLE001
            self.msg(
                "class:warn",
                f"⚠ {failure_label}: {self._error_line(error)}",
            )
            return None
        if not tags or not job.path:
            error = self.enricher.last_error
            if not job.path and not error:
                error = "download output path unavailable"
            if error:
                self.msg("class:warn", f"⚠ {failure_label}: {error}")
            return None
        try:
            job.path = apply_tags(job.path, tags)
        except Exception as error:  # noqa: BLE001
            self.msg(
                "class:warn",
                f"⚠ tagging skipped: {self._error_line(error)}",
            )
            return None
        job.title = tags.title or job.title
        return tags

    def _rehint(self, job: Job, hint: str, placeholder: Entry) -> None:
        tags = self._enrich(job, hint=hint, failure_label="retry failed")
        self._remove_entry(placeholder)
        if tags is not None:
            self._note_done(job, tags)
            if tags.confidence == "low":
                self._note_low_confidence(job, again=True)

    def _retry_selected(self) -> None:
        with self.lock:
            if self.sel is None or self.sel >= len(self.history):
                return
            entry = self.history[self.sel]
            if entry.kind != "err" or entry.job is None:
                return
            del self.history[self.sel]
            self.failed = [j for j in self.failed if j is not entry.job]
            self.sel = None
        self._update_focus()
        self.submit_urls([entry.job.url])

    @staticmethod
    def _detail_line(tags: TrackTags) -> str | None:
        """The `•`-separated metadata rendered under a completed entry."""
        parts = [tags.artist]
        if tags.album_artist and tags.album_artist != tags.artist:
            parts.append(tags.album_artist)
        parts += [tags.album, tags.year, tags.genre]
        if tags.kind == "derivative" and tags.based_on:
            parts.append(f"derivative of {tags.based_on}")
        joined = " • ".join(p for p in parts if p)
        return joined or None

    @staticmethod
    def _error_line(error: Exception) -> str:
        lines = [line.strip() for line in str(error).splitlines() if line.strip()]
        return (lines[0] if lines else error.__class__.__name__)[:200]

    # ── input handling ──────────────────────────────────────────────────

    def _accept(self, buff: Buffer) -> bool:
        if self.help_open:
            return False
        if self.dir_picker:
            self._accept_dir(buff)
            return False
        if self.modal:
            modal = self.modal
            text = buff.text.strip()
            self.close_modal()
            if text and text != modal.initial.strip():
                modal.on_submit(text)
            return False

        text = buff.text.strip()
        if not text:
            return False
        if text.startswith("/"):
            self.commands.dispatch(text)
            if text.split(maxsplit=1)[0].lower() == "/key":
                buff.text = "/key"  # never append a pasted key to command history
            return False

        urls = extract_urls(text)
        if not urls:
            self.msg(
                "class:dim", "that doesn't look like a link — paste a URL or type /help"
            )
            return False
        # Gate on source: only hosts yt-dlp can extract audio from. To accept a
        # new media source, extend SUPPORTED_HOSTS in downloader.py — not here.
        supported = [u for u in urls if is_supported_url(u)]
        if not supported:
            self.msg(
                "class:err", "only YouTube and SoundCloud links are supported"
            )
            return False
        rejected = len(urls) - len(supported)
        if rejected:
            self.msg(
                "class:warn",
                f"ignored {rejected} unsupported "
                f"link{'s' if rejected != 1 else ''} — "
                "only YouTube and SoundCloud are supported",
            )
        self.submit_urls(supported)
        return False  # False → clear the input line (and append to history)

    def _accept_hint(self, buff: Buffer) -> bool:
        text = buff.text.strip()
        if not text:
            return False
        with self.lock:
            if self.sel is None or self.sel >= len(self.history):
                return False
            entry = self.history[self.sel]
            if entry.kind != "hint" or entry.job is None:
                return False
            placeholder = Entry(
                "class:dim",
                f"⧗ re-checking “{(entry.job.title or '')[:40]}” with your hint…",
                "info",
                entry.job,
            )
            self.history[self.sel] = placeholder
            self.sel = None
        self._update_focus()
        self.pool.submit(self._rehint, entry.job, text, placeholder)
        self.refresh()
        return False

    def run(self) -> None:
        if not shutil.which("ffmpeg"):
            self.msg(
                "class:warn",
                "⚠ ffmpeg not found — install it with: brew install ffmpeg",
            )
        try:
            self.app.run()
        except (EOFError, KeyboardInterrupt):
            pass  # non-tty input ends with EOF; treat as a normal quit

        with self.lock:
            busy = len(self.active) + self.queued
        if busy:
            print(
                f"finishing {busy} job{'s' if busy != 1 else ''}… (ctrl-c to abandon)"
            )
        try:
            self.pool.shutdown(wait=True)
        except KeyboardInterrupt:
            pass
        with self.lock:
            done, failed = len(self.completed), len(self.failed)
            out_dir = self.settings.out_dir
        print(f"{done} converted · {failed} failed · files in {out_dir}")
