"""Transmute — interactive REPL for converting YouTube/SoundCloud links to MP3s.

Full-screen layout: header, Processing + History panels, a message area, and an
always-live input line pinned at the bottom. Work runs on a background thread
pool; the UI re-renders as state changes.

History entries that failed or need a hint are navigable with ↑/↓: failed
entries retry on Enter; low-confidence entries open an inline hint input.
"""
from __future__ import annotations

import shutil
import sys
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.filters import Condition, has_focus
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import ConditionalContainer, HSplit, Layout, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.processors import Processor, Transformation
from prompt_toolkit.styles import Style

from .downloader import MAX_WORKERS, Job, Settings, download_job, extract_urls
from .enrich import Enricher, TrackTags, apply_tags

HISTORY_FILE = Path.home() / ".transmute" / "history"

STYLE = Style.from_dict(
    {
        "header.bar": "#22d3ee",
        "header.title": "#22d3ee bold",
        "header.desc": "#bbbbbb",
        "subsection": "#7dd3fc",
        "dim": "#777777",
        "ok": "#4ade80",
        "err": "#f87171",
        "warn": "#facc15",
        "accent": "#22d3ee",
        "prompt": "#888888",
        "placeholder": "#666666 italic",
        "rule": "#333333",
        "toolbar": "#7dd3fc bg:#16303a",
        "selected": "bg:#1b3a46",
    }
)

PLACEHOLDER = 'Paste a YouTube or SoundCloud link, or type /help'
HINT_PLACEHOLDER = "type a hint (low confidence)"
TAGLINE = "Convert YouTube or SoundCloud links into rich MP3s. Bring your own provider."
QUALITIES = ("128", "192", "256", "320")
HISTORY_SHOWN = 14
MESSAGES_SHOWN = 8


@dataclass
class Entry:
    style: str
    line: str
    kind: str = "info"  # info | ok | err | hint
    job: Job | None = None


@dataclass
class Modal:
    """A temporary takeover of the bottom input line (reusable for any command).

    on_submit receives the entered text; empty text means "do nothing".
    Esc cancels without calling on_submit.
    """

    prefix: str
    placeholder: str
    on_submit: Callable[[str], None]
    initial: str = ""


class PlaceholderProcessor(Processor):
    def __init__(self, text: str | Callable[[], str]) -> None:
        self.text = text

    def apply_transformation(self, ti):
        if ti.lineno == 0 and not ti.document.text:
            text = self.text() if callable(self.text) else self.text
            return Transformation([("class:placeholder", text)])
        return Transformation(ti.fragments)


class App:
    def __init__(self) -> None:
        self.settings = Settings()
        self.enricher = Enricher()
        self.completed: list[Job] = []
        self.failed: list[Job] = []
        self.history: list[Entry] = []
        self.messages: deque[tuple[str, str]] = deque(maxlen=MESSAGES_SHOWN)
        self.pool = ThreadPoolExecutor(max_workers=MAX_WORKERS)
        self.lock = threading.Lock()
        self.active: dict[int, str] = {}  # seq -> live status line
        self.queued = 0
        self.sel: int | None = None  # selected history index (actionable entries only)
        self.modal: Modal | None = None
        self._seq = 0
        self._last_ctrl_c = 0.0
        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        self.app = self._build_app()

    # ── rendering ───────────────────────────────────────────────────────

    @staticmethod
    def _cols() -> int:
        from prompt_toolkit.application.current import get_app

        try:
            return get_app().output.get_size().columns
        except Exception:
            return 80

    def _render_header(self):
        return [
            ("", "\n"),
            ("class:header.bar", "▌ "),
            ("class:header.title", "Transmute\n"),
            ("class:header.bar", "▌ "),
            ("class:header.desc", TAGLINE + "\n"),
        ]

    def _build(self):
        """Body fragments, split where the inline hint input goes (below the
        selected low-confidence entry). Returns (above, below)."""
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
            if i == sel and e.kind == "hint":
                cur = below  # inline hint input renders between above/below

        if msgs:
            cur.append(rule)
            for style, line in msgs:
                cur.append((style, f"  {line[:width]}\n"))
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
            hints = sum(1 for e in self.history if e.kind == "hint")
            errs = sum(1 for e in self.history if e.kind == "err")
        parts = [str(self.settings.out_dir), f"{self.settings.quality}k"]
        if active:
            parts.append(f"{active} active")
        if queued:
            parts.append(f"{queued} queued")
        if self.modal:
            parts.append("enter applies · esc cancels")
        elif hints or errs:
            parts.append("↑/↓ to select · enter retries · esc deselects")
        return [("class:toolbar", "  " + "  ·  ".join(parts) + " ")]

    # ── selection ───────────────────────────────────────────────────────

    def _actionable(self) -> list[int]:
        with self.lock:
            return [i for i, e in enumerate(self.history) if e.kind in ("err", "hint")]

    def _sel_kind(self) -> str | None:
        with self.lock:
            if self.sel is None or self.sel >= len(self.history):
                return None
            return self.history[self.sel].kind

    def _move_sel(self, delta: int) -> None:
        acts = self._actionable()
        if not acts:
            self.sel = None
        elif self.sel is None:
            if delta < 0:
                self.sel = acts[-1]
        else:
            pos = acts.index(self.sel) if self.sel in acts else len(acts) - 1
            pos += delta
            if pos >= len(acts):
                self.sel = None  # down past the newest → back to plain input
            else:
                self.sel = acts[max(pos, 0)]
        self._update_focus()
        self.refresh()

    def _update_focus(self) -> None:
        try:
            if self._sel_kind() == "hint":
                self.app.layout.focus(self.hint_buffer)
            else:
                self.app.layout.focus(self.input_buffer)
        except Exception:
            pass

    # ── layout ──────────────────────────────────────────────────────────

    def _build_app(self) -> Application:
        self.input_buffer = Buffer(
            history=FileHistory(str(HISTORY_FILE)),
            multiline=False,
            accept_handler=self._accept,
            enable_history_search=True,
        )
        self.hint_buffer = Buffer(multiline=False, accept_handler=self._accept_hint)

        input_window = Window(
            BufferControl(
                self.input_buffer,
                input_processors=[
                    PlaceholderProcessor(
                        lambda: self.modal.placeholder if self.modal else PLACEHOLDER
                    )
                ],
            ),
            height=1,
            get_line_prefix=lambda line_no, wrap_count: [
                ("class:prompt", self.modal.prefix if self.modal else "❯ ")
            ],
        )
        hint_window = Window(
            BufferControl(
                self.hint_buffer,
                input_processors=[PlaceholderProcessor(HINT_PLACEHOLDER)],
            ),
            height=1,
            get_line_prefix=lambda line_no, wrap_count: [("class:prompt", "      ↳ ")],
        )

        kb = KeyBindings()
        no_modal = Condition(lambda: self.modal is None)
        has_actionable = Condition(lambda: bool(self._actionable())) & no_modal
        sel_is_err = Condition(
            lambda: self._sel_kind() == "err" and not self.input_buffer.text
        ) & no_modal

        @kb.add("up", filter=has_actionable)
        def _(event):
            self._move_sel(-1)

        @kb.add("down", filter=has_actionable & Condition(lambda: self.sel is not None))
        def _(event):
            self._move_sel(1)

        @kb.add("escape", eager=True)
        def _(event):
            if self.modal:
                self.close_modal()
                return
            self.sel = None
            self.hint_buffer.reset()
            self._update_focus()
            self.refresh()

        @kb.add("enter", filter=sel_is_err & has_focus(self.input_buffer))
        def _(event):
            self._retry_selected()

        @kb.add("c-c")
        def _(event):
            now = time.monotonic()
            if self.sel is not None:
                self.sel = None
                self.hint_buffer.reset()
                self._update_focus()
                self.refresh()
                self._last_ctrl_c = now
                return
            if self.input_buffer.text:
                self.input_buffer.reset()
                self._last_ctrl_c = now
                return
            if now - self._last_ctrl_c <= 2.0:
                event.app.exit()
            else:
                self._last_ctrl_c = now
                with self.lock:
                    busy = len(self.active) + self.queued
                note = f" ({busy} job{'s' if busy != 1 else ''} still running)" if busy else ""
                self.msg("class:dim", f"press ctrl-c again to exit{note}")

        @kb.add("c-d")
        def _(event):
            event.app.exit()

        root = HSplit(
            [
                Window(FormattedTextControl(self._render_header), height=3),
                Window(height=1, char="─", style="class:rule"),
                Window(
                    FormattedTextControl(self._render_above),
                    height=self._above_height,
                    wrap_lines=False,
                ),
                ConditionalContainer(
                    hint_window,
                    filter=Condition(lambda: self._sel_kind() == "hint"),
                ),
                Window(
                    FormattedTextControl(self._render_below),
                    height=Dimension(weight=1),
                    wrap_lines=False,
                ),
                Window(height=1, char="─", style="class:rule"),
                input_window,
                Window(FormattedTextControl(self._toolbar), height=1),
            ]
        )
        return Application(
            layout=Layout(root, focused_element=input_window),
            key_bindings=kb,
            style=STYLE,
            full_screen=True,
        )

    # ── state helpers (safe from any thread) ────────────────────────────

    def refresh(self) -> None:
        try:
            self.app.invalidate()
        except Exception:
            pass

    def msg(self, style: str, line: str) -> None:
        self.messages.append((style, line))
        self.refresh()

    # ── modal input (reusable bottom-line takeover) ─────────────────────

    def open_modal(self, modal: Modal) -> None:
        from prompt_toolkit.document import Document

        self.modal = modal
        self.input_buffer.set_document(
            Document(modal.initial, len(modal.initial)), bypass_readonly=True
        )
        self.refresh()

    def close_modal(self) -> None:
        self.modal = None
        self.input_buffer.reset()
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

    # ── background pipeline ─────────────────────────────────────────────

    @staticmethod
    def _display_name(job: Job) -> str:
        return job.path.name if job.path else (job.title or job.url)

    def _set_active(self, seq: int, text: str) -> None:
        with self.lock:
            self.active[seq] = text
        self.refresh()

    def _note_done(self, job: Job, desc: str | None) -> None:
        line = f"✔ {self._display_name(job)}"
        if desc:
            line += f"   ♪ {desc}"
        self.add_entry(Entry("class:ok", line, "ok", job))

    def _note_low_confidence(self, job: Job, again: bool = False) -> None:
        tail = "still unconfirmed, ↑ to add another hint" if again else "artist unconfirmed, ↑ to add a hint"
        self.add_entry(Entry("class:warn", f"⚠ “{(job.title or '')[:40]}” — {tail}", "hint", job))

    def submit_urls(self, urls: list[str]) -> None:
        with self.lock:
            self.queued += len(urls)
        self.refresh()
        for url in urls:
            self.pool.submit(self._process, url)

    def _process(self, url: str) -> None:
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
            download_job(job, self.settings, on_progress)
            if job.status != "done":
                with self.lock:
                    self.failed.append(job)
                self.add_entry(Entry(
                    "class:err",
                    f"✘ {job.url[:44]}  {job.error or 'unknown error'} — ↑ + enter to retry",
                    "err", job,
                ))
                return

            desc = tags = None
            if self.enricher.enabled:
                self._set_active(seq, f"enriching  {(job.title or '')[:44]}")
                desc, tags = self._enrich(job)

            with self.lock:
                self.completed.append(job)
            self._note_done(job, desc)
            if tags is not None and tags.confidence == "low":
                self._note_low_confidence(job)
        finally:
            with self.lock:
                self.active.pop(seq, None)
            self.refresh()

    def _enrich(self, job: Job, hint: str | None = None):
        tags = self.enricher.lookup(
            title=job.title or "", uploader=job.uploader,
            duration=job.duration, url=job.url,
            description=job.description, tags=job.tags,
            hint=hint,
        )
        if not tags or not job.path:
            if self.enricher.last_error:
                self.msg("class:warn", f"⚠ enrichment skipped: {self.enricher.last_error}")
            return None, None
        job.path = apply_tags(job.path, tags)
        job.title = tags.title or job.title
        return self._describe(tags), tags

    def _rehint(self, job: Job, hint: str, placeholder: Entry) -> None:
        desc, tags = self._enrich(job, hint=hint)
        self._remove_entry(placeholder)
        if desc:
            self._note_done(job, desc)
            if tags is not None and tags.confidence == "low":
                self._note_low_confidence(job, again=True)
        else:
            self.msg("class:warn", f"⚠ retry failed: {self.enricher.last_error}")

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
    def _describe(tags: TrackTags) -> str:
        parts = [p for p in (tags.album, tags.year, tags.genre) if p]
        if tags.kind == "derivative" and tags.based_on:
            parts.append(f"derivative of {tags.based_on}")
        detail = f"  ({' · '.join(parts)})" if parts else ""
        return f"{tags.artist} — {tags.title}{detail}"

    # ── commands ────────────────────────────────────────────────────────

    def cmd_help(self, _arg: str) -> None:
        rows = [
            ("/out [dir]", "show or set the output directory"),
            ("/quality [128|192|256|320]", "show or set MP3 bitrate (kbps)"),
            ("/enrich [on|off]", "toggle web-search metadata enrichment"),
            ("/list", "show all tracks from this session"),
            ("/retry", "requeue all failed downloads"),
            ("/login", "log in to Claude (subscription — opens browser)"),
            ("/logout", "log out of Claude (disables enrichment)"),
            ("/clear", "clear history and messages"),
            ("/quit", "exit (or ctrl-d / double ctrl-c)"),
            ("↑/↓", "select failed or low-confidence entries in History"),
        ]
        for cmd, desc in rows:
            self.msg("class:dim", f"{cmd:<28}{desc}")

    def _set_out_dir(self, text: str) -> None:
        path = Path(text).expanduser()
        if not path.is_absolute():
            path = Path.home() / text  # bare names are home-relative
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            self.msg("class:err", f"can't use {path}: {e}")
            return
        self.settings.out_dir = path
        self.msg("class:dim", f"output directory: {self.settings.out_dir}")

    def cmd_out(self, arg: str) -> None:
        if arg:
            self._set_out_dir(arg)
            return
        self.msg("class:dim", f"output directory: {self.settings.out_dir}")
        self.open_modal(Modal(
            prefix="out ❯ ",
            placeholder="type anything to change directory — enter or esc to keep",
            on_submit=self._set_out_dir,
            initial="~/",
        ))

    def cmd_quality(self, arg: str) -> None:
        if arg:
            if arg not in QUALITIES:
                self.msg("class:err", f"quality must be one of {', '.join(QUALITIES)}")
                return
            self.settings.quality = arg
        self.msg("class:dim", f"quality: {self.settings.quality}k")

    def cmd_enrich(self, arg: str) -> None:
        if arg in ("on", "off"):
            self.enricher.enabled = arg == "on"
        self.msg("class:dim", f"metadata enrichment: {'on' if self.enricher.enabled else 'off'}")

    def cmd_login(self, _arg: str) -> None:
        import subprocess

        from prompt_toolkit.application import run_in_terminal

        async def task():
            def blocking() -> int:
                print("Launching Claude login (browser will open)…\n")
                try:
                    return subprocess.run(["claude", "auth", "login", "--claudeai"]).returncode
                except FileNotFoundError:
                    return -1

            rc = await run_in_terminal(blocking)
            if rc == 0:
                self.enricher.enabled = True
                self.enricher.last_error = None
                self.msg("class:ok", "logged in to Claude — enrichment enabled")
            elif rc == -1:
                self.msg("class:err", "claude CLI not found — install Claude Code first")
            else:
                self.msg("class:err", "login failed or was cancelled")

        self.app.create_background_task(task())

    def cmd_logout(self, _arg: str) -> None:
        import os
        import subprocess

        try:
            proc = subprocess.run(
                ["claude", "auth", "logout"], capture_output=True, text=True, timeout=30,
            )
        except FileNotFoundError:
            self.msg("class:err", "claude CLI not found — nothing to log out of")
            return
        except subprocess.TimeoutExpired:
            self.msg("class:err", "claude auth logout timed out")
            return
        if proc.returncode == 0:
            self.enricher.enabled = False
            self.msg("class:dim", "logged out of Claude — enrichment disabled (/login to log back in)")
        else:
            self.msg("class:err", (proc.stderr or proc.stdout).strip()[:120] or "logout failed")
        if os.environ.get("ANTHROPIC_API_KEY"):
            self.msg("class:warn", "⚠ ANTHROPIC_API_KEY is still set in your shell — "
                                   "enrichment can still bill the API via that key")

    def cmd_list(self, _arg: str) -> None:
        with self.lock:
            completed, failed = list(self.completed), list(self.failed)
        if not completed and not failed:
            self.msg("class:dim", "nothing converted yet")
            return
        for job in completed:
            self.msg("class:ok", f"✔ {job.title or job.url}")
        for job in failed:
            self.msg("class:err", f"✘ {job.url}")

    def cmd_retry(self, _arg: str) -> None:
        with self.lock:
            failed, self.failed = self.failed, []
            self.history = [e for e in self.history if e.kind != "err"]
            self.sel = None
        if not failed:
            self.msg("class:dim", "no failed downloads to retry")
            return
        self.submit_urls([j.url for j in failed])

    def cmd_clear(self, _arg: str) -> None:
        with self.lock:
            self.history.clear()
            self.sel = None
        self.messages.clear()
        self.refresh()

    # ── input handling ──────────────────────────────────────────────────

    def _accept(self, buff: Buffer) -> bool:
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
            cmd, _, arg = text.partition(" ")
            if cmd in ("/quit", "/exit", "/q"):
                self.app.exit()
                return False
            handler = {
                "/help": self.cmd_help,
                "/out": self.cmd_out,
                "/quality": self.cmd_quality,
                "/enrich": self.cmd_enrich,
                "/list": self.cmd_list,
                "/retry": self.cmd_retry,
                "/login": self.cmd_login,
                "/logout": self.cmd_logout,
                "/clear": self.cmd_clear,
            }.get(cmd)
            if handler:
                handler(arg.strip())
            else:
                self.msg("class:err", f"unknown command: {cmd}  (/help for commands)")
            return False

        urls = extract_urls(text)
        if not urls:
            self.msg("class:dim", "that doesn't look like a link — paste a URL or type /help")
            return False

        self.submit_urls(urls)
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
                "info", entry.job,
            )
            self.history[self.sel] = placeholder
            self.sel = None
        self._update_focus()
        self.pool.submit(self._rehint, entry.job, text, placeholder)
        self.refresh()
        return False

    def run(self) -> None:
        if not shutil.which("ffmpeg"):
            self.msg("class:warn", "⚠ ffmpeg not found — install it with: brew install ffmpeg")
        try:
            self.app.run()
        except (EOFError, KeyboardInterrupt):
            pass  # non-tty input ends with EOF; treat as a normal quit

        # Back in the normal terminal: drain any in-flight work.
        with self.lock:
            busy = len(self.active) + self.queued
        if busy:
            print(f"finishing {busy} job{'s' if busy != 1 else ''}… (ctrl-c to abandon)")
            try:
                self.pool.shutdown(wait=True)
            except KeyboardInterrupt:
                pass
        with self.lock:
            done, failed = len(self.completed), len(self.failed)
        print(f"{done} converted · {failed} failed · files in {self.settings.out_dir}")


def main() -> None:
    sys.stdout.write("\x1b]0;Transmute\x07")  # terminal tab title
    sys.stdout.flush()
    App().run()


if __name__ == "__main__":
    main()
