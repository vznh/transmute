"""Application state, rendering, and the background download/enrich pipeline.

Layout lives in layout.py, key bindings in keys.py, slash commands in
commands.py; this module owns the shared state they all act on.

History entries that failed or need a hint are navigable with ↑/↓: failed
entries retry on Enter; low-confidence entries open an inline hint input.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import threading
from collections import deque
from concurrent.futures import Executor, ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.filters import Condition
from prompt_toolkit.layout.dimension import Dimension

from .commands import HELP_ROWS, Commands
from .config import (
    ACTIVITY_FILE,
    HISTORY_FILE,
    MAX_WORKERS,
    SETTINGS_FILE,
    SIGNIN_RECHECK_SECONDS,
    STATE_DIR,
    Settings,
)
from .downloader import Job, download_job, extract_urls, is_supported_url
from .enrich import Enricher, TrackTags, apply_tags
from .history import ActivityStore, HistoryStoreError, StoredJob
from .keys import build_key_bindings
from .layout import build_layout
from .settings import SettingsStore, SettingsStoreError
from .style import HELP_HINT, HISTORY_SHOWN, MESSAGES_SHOWN, PLACEHOLDER, STYLE, TAGLINE
from .widgets import Modal

EntryKind = Literal["info", "ok", "err", "hint"]

if TYPE_CHECKING:
    from .sync import SyncPlan, SyncProgress, SyncResult, SyncService


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

    The directory buffer holds only the path relative to ~/ (the "~/" is a fixed,
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
        activity_store: ActivityStore | None = None,
        settings_store: SettingsStore | None = None,
        sync_service: SyncService | None = None,
    ) -> None:
        self.enricher = Enricher()
        self.completed: list[Job] = []
        self.failed: list[Job] = []
        self.history: list[Entry] = []
        self.messages: deque[tuple[str, str]] = deque(maxlen=MESSAGES_SHOWN)
        self.pool = pool or ThreadPoolExecutor(max_workers=MAX_WORKERS)
        self.sync_service = sync_service
        self.sync_plan: SyncPlan | None = None
        self.sync_results: dict[str, SyncResult] = {}
        self.sync_selected: set[str] = set()
        self.sync_cursor = 0
        self.sync_status = ""
        self.sync_spinner_frame = 0
        self.sync_scan_generation = 0
        self.sync_open = False
        self.sync_focus = False
        self.sync_busy = False
        self.sync_transfer_active = False
        self.sync_cancel_requested = False
        self.sync_pane = None
        self.lock = threading.Lock()
        self.update_available = False
        self.activity_lock = threading.RLock()
        self.activity_store: ActivityStore | None = activity_store
        self.session_id = ""
        self._run_outcomes: dict[str, str] = {}
        self._persistence_warning_shown = False
        self.settings_store = (
            settings_store if settings_store is not None else SettingsStore(SETTINGS_FILE)
        )
        self.settings = self._load_settings()
        self.active: dict[int, str] = {}
        self._active_paths: dict[int, Path | None] = {}
        self._queued_jobs: list[Job] = []
        self.queued = 0
        self.sel: int | None = None
        self.modal: Modal | None = None
        self.dir_picker: DirPicker | None = None
        self.help_open = False
        self.input_notice: tuple[str, str] | None = None
        self._input_notice_id = 0
        self._seq = 0
        self._last_ctrl_c = 0.0
        self._prepare_input_history(history_file)
        self._restore_activity()
        self.commands = Commands(self)
        layout = build_layout(self, history_file)
        self.app = Application(
            layout=layout,
            key_bindings=build_key_bindings(self),
            style=STYLE,
            full_screen=True,
            mouse_support=Condition(lambda: self.help_open or self.sync_open),
        )

    @staticmethod
    def _prepare_input_history(path: Path) -> None:
        """Create the prompt-recall file with private local permissions."""
        path = Path(path).expanduser()
        parent_was_created = not path.parent.exists()
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if parent_was_created or (
            path.parent == STATE_DIR and not path.parent.is_symlink()
        ):
            path.parent.chmod(0o700)
        path.touch(mode=0o600, exist_ok=True)
        path.chmod(0o600)

    def _restore_activity(self) -> None:
        """Restore durable job outcomes before the first UI render."""
        if self.activity_store is None:
            try:
                self.activity_store = ActivityStore(ACTIVITY_FILE)
            except HistoryStoreError as exc:
                self._warn_persistence(exc)
                return

        try:
            self.session_id = self.activity_store.start_session()
            records = self.activity_store.load_jobs()
        except HistoryStoreError as exc:
            self._warn_persistence(exc)
            self.activity_store = None
            self.session_id = ""
            return

        for record in records:
            self._restore_record(record)

    def _restore_record(self, record: StoredJob) -> None:
        job = record.job
        if job.status == "done":
            self.completed.append(job)
            self.history.append(self._done_entry(job, record.detail))
            if (
                record.needs_hint
                and not record.hint_in_progress
                and job.path
                and job.path.is_file()
            ):
                self.history.append(
                    self._hint_entry(job, again=record.hint_attempts > 0)
                )
        elif job.status == "error":
            self.failed.append(job)
            self.history.append(self._failure_entry(job))

    def _warn_persistence(self, error: Exception) -> None:
        with self.lock:
            if self._persistence_warning_shown:
                return
            self._persistence_warning_shown = True
            self.messages.append(
                (
                    "class:warn",
                    f"⚠ activity history unavailable for this run: {str(error)[:100]}",
                )
            )
        self.refresh()

    def _persist(self, method: str, *args, **kwargs):
        store = self.activity_store
        if store is None:
            return None
        try:
            return getattr(store, method)(*args, **kwargs)
        except HistoryStoreError as exc:
            # Avoid repeatedly stalling the UI on the same unavailable store.
            # This run continues in memory; the next launch will try again.
            self.activity_store = None
            self._warn_persistence(exc)
            return None

    def _load_settings(self) -> Settings:
        """Restore saved settings, falling back to defaults if the file is bad."""
        try:
            return self.settings_store.load()
        except SettingsStoreError as exc:
            self.msg("class:warn", f"⚠ saved settings ignored: {str(exc)[:100]}")
            return Settings()

    def _persist_settings(self) -> None:
        """Write the current settings; a failure warns but is not fatal."""
        with self.lock:
            settings = self.settings
        try:
            self.settings_store.save(settings)
        except SettingsStoreError as exc:
            self.msg("class:warn", f"⚠ couldn't save settings: {str(exc)[:100]}")

    # ── rendering ───────────────────────────────────────────────────────

    @staticmethod
    def _cols() -> int:
        from prompt_toolkit.application.current import get_app

        return get_app().output.get_size().columns

    def _render_header(self):
        provider_line, provider_style = self._provider_connection_line()
        with self.lock:
            update_available = self.update_available
        out = [
            ("", "\n"),
            ("class:header.bar", "▌ "),
            ("class:header.title", "Transmute\n"),
            ("class:header.bar", "▌ "),
            ("class:header.desc", TAGLINE + "\n"),
        ]
        if update_available:
            out.append((provider_style, "  " + provider_line))
            out.append(("class:warn", "  Run `brew update; brew upgrade`"))
            out.append(("", "\n"))
        else:
            out.append((provider_style, "  " + provider_line + "\n"))
        return out

    def _check_update(self) -> None:
        """Check Homebrew's cached formula metadata without blocking the UI."""
        try:
            result = subprocess.run(
                ["brew", "outdated", "--formula", "--quiet", "transmute"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
                env={**os.environ, "HOMEBREW_NO_AUTO_UPDATE": "1"},
            )
        except (OSError, subprocess.TimeoutExpired):
            return
        if result.returncode == 0 and result.stdout.strip():
            with self.lock:
                self.update_available = True
            self.refresh()

    def _provider_connection_line(self) -> tuple[str, str]:
        states, backend, has_api_key = self.enricher.connection_snapshot()
        if states.get("codex") == "signed_in":
            return "Codex is connected.", "class:ok"
        if states.get("claude") == "signed_in":
            return "Claude is connected.", "class:ok"
        if has_api_key:
            label = "OpenAI API" if backend == "openai_api" else "Anthropic API"
            return f"{label} is connected.", "class:ok"

        provider = next(
            (name for name in ("codex", "claude") if states.get(name) != "missing"),
            None,
        )
        command = f"/login {provider}" if provider else "/key"
        return (
            f"No provider is connected. Get started with {command}.",
            "class:err",
        )

    def _render_help(self):
        """Render the scrollable command reference above the live prompt."""
        out = [("class:subsection", "\n  Commands\n")]
        for cmd, desc in HELP_ROWS:
            out.append(("class:dim", f"    {cmd:<34}{desc}\n"))
        with self.lock:
            msgs = list(self.messages)
        if msgs:
            out.append(("class:subsection", "\n  Recent feedback\n"))
            out.extend((style, f"    {line[:72]}\n") for style, line in msgs[-4:])
        return out

    def _render_sync(self) -> list[tuple[str, str]]:
        """Format a locked snapshot of the read-only plan and transfer results."""
        with self.lock:
            plan = self.sync_plan
            results = dict(self.sync_results)
            selected = set(self.sync_selected)
            cursor = self.sync_cursor
            status = self.sync_status
            busy = self.sync_busy
            copying = self.sync_transfer_active
            spinner_frame = self.sync_spinner_frame
        out = [("class:subsection", "  Preview\n")]
        if status and not (plan is None and busy):
            out.append(("class:accent", f"  {status[:220]}\n"))
        if plan is None:
            if busy:
                spinner = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"[spinner_frame % 10]
                out.append(("class:accent", f"  {spinner} Scanning\n"))
                out.append(("class:dim", f"    {status or 'Preparing file list…'}\n"))
            else:
                out.append(("class:dim", "  No preview available. Reconnect the iPhone and run /sync.\n"))
            return out
        out.append(("class:ok", f"  Connected {plan.device_name}\n"))
        out.append(("class:dim", f"  This computer: {getattr(plan, 'computer_name', '') or 'This computer'}\n"))
        out.append(("class:dim", f"  {plan.out_dir}\n"))
        items = list(plan.items)
        if any(item.direction == "to_phone" for item in items):
            out.append(("class:dim", "  Phone copies use random names; a concurrent same-name write could be replaced.\n"))
        for index, item in enumerate(items):
            result = results.get(item.id)
            selected_mark = "   " if item.direction == "matched" else "[x]" if item.id in selected else "[ ]"
            if item.direction == "matched":
                label, style = "Already synced", "class:dim"
            elif item.confidence == "low":
                direction = "To iPhone" if item.direction == "to_phone" else "To computer"
                label, style = f"{direction} ?", "class:dim"
            else:
                label = "To iPhone" if item.direction == "to_phone" else "To computer"
                style = "class:dim" if result and result.status == "done" else "class:accent"
            if result:
                result_label = {"done": "Copied", "error": "Failed", "cancelled": "Cancelled"}.get(result.status, label)
                if item.confidence == "low":
                    label = f"{result_label} {direction} ?"
                    style = "class:dim"
                elif item.direction == "matched":
                    style = "class:dim"
                else:
                    label = f"{result_label} {label}"
                    style = "class:ok" if result.status == "done" else "class:err"
            marker = "❯" if index == cursor else " "
            name = item.track.relative_path.rsplit("/", 1)[-1]
            out.append((style, f"{marker} {selected_mark} {label}: {name[:60]}\n"))
            if item.confidence == "low":
                reason = item.reason.strip().rstrip(".")
                prefix = "Possible duplicate based on "
                if reason.startswith(prefix):
                    reason = reason[len(prefix) :]
                    reason = reason.replace("metadata or filename", "metadata and filename")
                elif reason == "Same filename has different content":
                    reason = "filename with different content"
                explanation = f" based on {reason}" if reason else ""
                out.append(("class:dim", f"      * Possible duplicate{explanation[:110]}\n"))
                if result and result.status != "done":
                    out.append(("class:err", f"        {result.status}: {result.message[:80]}\n"))
            else:
                details = []
                if item.reason:
                    details.append(item.reason[:56])
                if result and result.status != "done":
                    details.append(f"{result.status}: {result.message[:80]}")
                if details:
                    detail_style = "class:dim" if item.direction == "matched" else "class:err"
                    out.append((detail_style, f"    {' · '.join(details)}\n"))
        continue_index = len(items)
        marker = "❯" if cursor == continue_index else " "
        enabled = bool(selected) and not busy
        suffix = " (sync running)" if busy else ("" if enabled else " (select tracks first)")
        out.append(("class:accent" if enabled else "class:dim", f"{marker} Continue{suffix}\n"))
        escape_hint = "Esc stops after this file" if copying else "Esc closes"
        out.append(("class:dim", f"  ↑/↓ move · Enter select/continue · h high confidence · s select all · Tab to input · {escape_hint}\n"))
        return out

    def _sync_excluded_paths(self) -> tuple[Path, ...]:
        with self.lock:
            paths = [path for path in self._active_paths.values() if path is not None]
            paths.extend(job.path for job in self._queued_jobs if job.path is not None)
            return tuple(set(paths))

    def start_sync_preview(self) -> None:
        with self.lock:
            self.help_open = False
            self.dir_picker = None
            if self.sync_busy:
                self.sync_open = True
                self.sync_focus = True
                reopen = True
            else:
                reopen = False
                self.sync_open = True
                self.sync_busy = True
                self.sync_plan = None
                self.sync_results = {}
                self.sync_selected = set()
                self.sync_status = ""
                self.sync_spinner_frame = 0
                self.sync_scan_generation += 1
                self.sync_focus = True
                self.sync_pane.vertical_scroll = 0
            generation = self.sync_scan_generation
        self.app.layout.focus(self.sync_control)
        if reopen:
            self.refresh()
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            self.app.create_background_task(self._animate_sync_spinner(generation))
        try:
            self.pool.submit(self._sync_preview_worker, self.settings_snapshot().out_dir, self._sync_excluded_paths())
        except Exception as error:  # noqa: BLE001 - normalize worker submission failures.
            self._publish_sync_error(error)
        self.refresh()

    async def _animate_sync_spinner(self, generation: int) -> None:
        while True:
            await asyncio.sleep(0.1)
            with self.lock:
                if (
                    generation != self.sync_scan_generation
                    or not self.sync_busy
                    or self.sync_plan is not None
                ):
                    return
                self.sync_spinner_frame = (self.sync_spinner_frame + 1) % 10
            self.refresh()

    def _get_sync_service(self) -> SyncService:
        if self.sync_service is None:
            from .sync import SyncService

            self.sync_service = SyncService()
        return self.sync_service

    def _sync_preview_worker(self, out_dir: Path, excluded: tuple[Path, ...]) -> None:
        try:
            plan = self._get_sync_service().preview(out_dir, excluded_paths=excluded, progress=self._sync_progress)
        except Exception as error:  # noqa: BLE001 - terminate sync UI state for filesystem and USB failures.
            self._publish_sync_error(error)
            return
        with self.lock:
            self.sync_plan = plan
            self.sync_busy = False
            to_phone = sum(item.direction == "to_phone" for item in plan.items)
            to_computer = sum(item.direction == "to_computer" for item in plan.items)
            computer_name = getattr(plan, "computer_name", "") or "This computer"
            self.sync_status = (
                f"Ready · {to_phone} to {plan.device_name}, "
                f"{to_computer} to {computer_name}"
            )
            self.sync_cursor = 0
        self.refresh()

    def _sync_progress(self, progress: SyncProgress) -> None:
        filename = getattr(progress, "filename", "")
        phase = getattr(progress, "phase", "Working")
        with self.lock:
            self.sync_status = filename[:120] if phase.startswith("scan") else f"{phase} {filename}".strip()[:120]
        self.refresh()

    def _publish_sync_error(self, error: Exception) -> None:
        with self.lock:
            self.sync_busy = False
            self.sync_transfer_active = False
            self.sync_status = f"Sync unavailable: {self._error_line(error)}"
        self.refresh()

    def sync_move(self, delta: int) -> None:
        with self.lock:
            count = len(self.sync_plan.items) if self.sync_plan else 0
            self.sync_cursor = min(max(self.sync_cursor + delta, 0), count)
        self._scroll_sync_to_cursor()
        self.refresh()

    def _scroll_sync_to_cursor(self) -> None:
        if self.sync_pane is None:
            return
        with self.lock:
            plan = self.sync_plan
            cursor = self.sync_cursor
            results = dict(self.sync_results)
        preceding_rows = 0
        if plan is not None:
            preceding_rows = sum(
                2
                + bool(item.reason.strip())
                + bool(results.get(item.id) and results[item.id].status != "done")
                if item.confidence == "low"
                else 1 + bool(
                    item.reason
                    or results.get(item.id) is not None
                    and results[item.id].status != "done"
                )
                for item in plan.items[:cursor]
            )
        size = self.app.output.get_size()
        content_height = self.sync_pane.content.preferred_height(max(size.columns - 1, 1), self.sync_pane.max_available_height).preferred
        viewport = max(size.rows - 12, 1)
        self.sync_pane.vertical_scroll = min(max(preceding_rows - viewport + 2, 0), max(content_height - viewport, 0))

    def sync_scroll(self, delta: int) -> None:
        if self.sync_pane is None:
            return
        self.sync_pane.vertical_scroll = max(self.sync_pane.vertical_scroll + delta, 0)
        self.refresh()

    def sync_toggle(self) -> None:
        with self.lock:
            if not self.sync_plan or self.sync_cursor >= len(self.sync_plan.items):
                start = bool(self.sync_selected) and not self.sync_busy
            else:
                item = self.sync_plan.items[self.sync_cursor]
                if item.direction == "matched" or self.sync_results.get(item.id, None) and self.sync_results[item.id].status == "done":
                    return
                if item.id in self.sync_selected:
                    self.sync_selected.remove(item.id)
                else:
                    self.sync_selected.add(item.id)
                start = False
        if start:
            self.start_sync_transfer()
        self.refresh()

    def sync_select(self, high_only: bool) -> None:
        with self.lock:
            if not self.sync_plan:
                return
            self.sync_selected = {
                item.id for item in self.sync_plan.items
                if item.direction != "matched"
                and (not high_only or item.confidence == "high")
                and not (self.sync_results.get(item.id) and self.sync_results[item.id].status == "done")
            }
        self.refresh()

    def start_sync_transfer(self) -> None:
        with self.lock:
            if self.sync_busy or self.sync_plan is None or not self.sync_selected:
                return
            selected = set(self.sync_selected)
            plan = self.sync_plan
            self.sync_busy = True
            self.sync_transfer_active = True
            self.sync_cancel_requested = False
            self.sync_status = "Copying selected tracks…"
        try:
            self.pool.submit(self._sync_transfer_worker, plan, selected)
        except RuntimeError as error:
            self._publish_sync_error(error)
        self.refresh()

    def _sync_transfer_worker(self, plan: SyncPlan, selected: set[str]) -> None:
        try:
            results = self._get_sync_service().transfer(
                plan, selected, progress=self._sync_progress, cancelled=self._sync_cancelled
            )
        except Exception as error:  # noqa: BLE001 - normalize provider and filesystem failures.
            self._publish_sync_error(error)
            return
        with self.lock:
            self.sync_results.update({result.item_id: result for result in results})
            self.sync_selected.difference_update(
                result.item_id for result in results if result.status == "done"
            )
            self.sync_busy = False
            self.sync_transfer_active = False
            self.sync_status = "Transfer complete" if not self.sync_cancel_requested else "Stopped after current file"
            self.sync_cancel_requested = False
        self.refresh()

    def _sync_cancelled(self) -> bool:
        with self.lock:
            return self.sync_cancel_requested

    def sync_back(self) -> None:
        closed = False
        with self.lock:
            if self.sync_busy and self.sync_transfer_active:
                self.sync_cancel_requested = True
                self.sync_status = "Stopping after current file…"
            else:
                self.sync_open = False
                self.sync_focus = False
                closed = True
        if closed:
            self.app.layout.focus(self.input_buffer)
        self.refresh()

    def toggle_sync_focus(self) -> None:
        focus_report = not self.app.layout.has_focus(self.sync_control)
        with self.lock:
            self.sync_focus = focus_report
        self.app.layout.focus(self.sync_control if focus_report else self.input_buffer)
        self.refresh()

    def retry_sync(self) -> None:
        no_preview = False
        with self.lock:
            if not self.sync_plan:
                no_preview = True
            elif self.sync_busy:
                return
            else:
                failed = {key for key, result in self.sync_results.items() if result.status in ("error", "cancelled")}
                if not failed:
                    return
                self.help_open = False
                self.dir_picker = None
                self.sync_selected = failed
                self.sync_cursor = 0
                self.sync_open = True
                self.sync_focus = True
        if no_preview:
            self.msg("class:dim", "no Spotify sync preview to retry")
            return
        self.app.layout.focus(self.sync_control)
        self.refresh()

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
        height = sum(text.count("\n") for _, text in self._build()[0])
        return Dimension(min=0, preferred=height, max=height)

    def _toolbar(self):
        with self.lock:
            active = len(self.active)
            queued = self.queued
            settings = self.settings
        parts = [f"out {settings.out_dir}", f"q {settings.quality}k"]
        if active:
            parts.append(f"{active} active")
        if queued:
            parts.append(f"{queued} queued")
        return [("class:toolbar", " " + "  ·  ".join(parts) + " ")]

    def _status_line(self):
        """The bottom row: the contextual input hint, then the status toolbar
        (labeled output directory and bitrate) on the same line. The gap is
        unstyled so the toolbar's background starts at its own text."""
        return [*self._input_hint(), ("", "  "), *self._toolbar()]

    def _input_prefix(self):
        """Main input prefix; the directory editor renders its own prefix."""
        if self.modal:
            return [("class:prompt", self.modal.prefix)]
        return [("class:prompt", "❯ ")]

    def _input_placeholder(self) -> str:
        if self.modal:
            return self.modal.placeholder
        return PLACEHOLDER

    def _dir_prefix(self):
        return [("class:accent", "Output folder ~/ ")]

    def _dir_placeholder(self) -> str:
        if self.dir_picker and self.dir_picker.stage == "confirm":
            return f"~/{self.dir_picker.typed} doesn't exist — create it? (y/n)"
        return "type a folder under home — tab to complete, enter to set"

    def _input_hint(self):
        if self.sync_open:
            if self.app.layout.current_control is self.sync_control:
                with self.lock:
                    copying = self.sync_transfer_active
                escape = "q/Esc stops after this file" if copying else "q/Esc closes"
                return [("class:input.hint", f"  ↑/↓ browse · Enter select/continue · h high confidence · s select all · Tab to input · {escape}")]
            return [
                ("class:input.hint", "  type URLs or commands · "),
                ("class:dim", "Tab back to Sync"),
                ("class:input.hint", " · /sync close exits"),
            ]
        if self.dir_picker is not None:
            if self.dir_buffer_focused:
                if self.dir_picker.stage == "confirm":
                    tip = "y/n confirms · shift-tab to URL input · esc cancels"
                else:
                    tip = "enter applies · shift-tab to URL input · esc cancels"
            else:
                tip = "type URLs or commands · shift-tab to folder · esc cancels"
            return [("class:input.hint", f"  {tip}")]
        if self.help_open:
            return [
                (
                    "class:input.hint",
                    "  scroll over help · type URLs or commands · esc closes",
                )
            ]
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
        elif self.dir_picker is not None and self.dir_buffer_focused:
            self.app.layout.focus(self.dir_buffer)
        else:
            self.app.layout.focus(self.input_buffer)

    @property
    def dir_buffer_focused(self) -> bool:
        return (
            getattr(self, "app", None) is not None
            and self.app.layout.current_buffer is self.dir_buffer
        )

    def toggle_dir_focus(self) -> None:
        if self.dir_picker is None or self.modal:
            return
        if self.app.layout.current_buffer is self.dir_buffer:
            self.app.layout.focus(self.input_buffer)
        else:
            self.app.layout.focus(self.dir_buffer)
        self.refresh()

    def scroll_help(self, delta: int) -> None:
        if not self.help_open:
            return
        size = self.app.output.get_size()
        content_width = max(size.columns - 1, 1)  # reserve the pane scrollbar
        content_height = self.help_pane.content.preferred_height(
            content_width, self.help_pane.max_available_height
        ).preferred
        # Reserve the header, rules, main input, and status rows.
        fixed_rows = 8 + (1 if self.dir_picker is not None else 0)
        viewport_height = max(size.rows - fixed_rows, 1)
        max_scroll = max(content_height - viewport_height, 0)
        self.help_pane.vertical_scroll = min(
            max(self.help_pane.vertical_scroll + delta, 0), max_scroll
        )
        self.refresh()

    # ── state helpers (safe from any thread) ────────────────────────────

    def refresh(self) -> None:
        if hasattr(self, "app"):
            self.app.invalidate()

    def msg(self, style: str, line: str) -> None:
        with self.lock:
            self.messages.append((style, line))
        self.refresh()

    def _recheck_signin(self) -> str | None:
        backend = self.enricher.refresh_subscription()
        if backend is None:
            return None
        name = "Claude" if backend == "claude" else "Codex"
        self.msg("class:ok", f"signed in to {name} — enrichment enabled")
        return backend

    def _startup_enrichment_notice(self) -> None:
        if self.enricher.backend != "none":
            return
        for provider in ("claude", "codex"):
            if self.enricher.cli_states.get(provider) == "signed_out":
                label = "Claude" if provider == "claude" else "Codex"
                self.msg(
                    "class:warn",
                    f"⚠ {label} is installed but not signed in — run /login {provider} "
                    f"(checking every {SIGNIN_RECHECK_SECONDS}s)",
                )
                return

    def _start_signin_recheck(self) -> None:
        async def recheck_loop() -> None:
            while self.enricher.needs_subscription_recheck:
                await asyncio.sleep(SIGNIN_RECHECK_SECONDS)
                try:
                    future = self.pool.submit(self._recheck_signin)
                    backend = await asyncio.wrap_future(future)
                except Exception:  # noqa: BLE001
                    return
                if backend is not None:
                    return

        self.app.create_background_task(recheck_loop())

    def settings_snapshot(self) -> Settings:
        with self.lock:
            return self.settings

    def set_out_dir(self, out_dir: Path) -> None:
        with self.lock:
            self.settings = replace(self.settings, out_dir=out_dir)
        self._persist_settings()
        self.refresh()

    def set_quality(self, quality: str) -> None:
        with self.lock:
            self.settings = replace(self.settings, quality=quality)
        self._persist_settings()
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
            self.sync_open = False
            self.sync_focus = False
        self.dir_buffer.reset()
        self.app.layout.focus(self.input_buffer)
        self._update_focus()
        self.refresh()

    def close_dir_picker(self) -> None:
        with self.lock:
            self.dir_picker = None
        self.dir_buffer.reset()
        self.app.layout.focus(self.input_buffer)
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
            self.dir_buffer.set_document(
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
        self.dir_buffer.set_document(
            Document(rel, len(rel)), bypass_readonly=True
        )
        self.refresh()

    def open_help(self) -> None:
        with self.lock:
            self.help_open = True
            self.sel = None
            self.sync_open = False
            self.sync_focus = False
        self.app.layout.focus(self.input_buffer)
        self._update_focus()
        self.refresh()

    def close_help(self) -> None:
        self.help_open = False
        self.refresh()

    def open_modal(self, modal: Modal) -> None:
        with self.lock:
            self.modal = modal
            self.sync_open = False
            self.sync_focus = False
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

    def _done_entry(self, job: Job, detail: str | None) -> Entry:
        return Entry(
            "class:ok",
            f"✔ {self._display_name(job)}",
            "ok",
            job,
            detail=detail,
        )

    @staticmethod
    def _failure_entry(job: Job) -> Entry:
        # Only retryable failures become selectable. Retrying an unsupported
        # site or deleted video cannot succeed without something else changing.
        if job.retryable:
            line = (
                f"✘ {job.url[:44]}  {job.error or 'unknown error'}"
                " — ↑ + enter to retry"
            )
            kind = "err"
        else:
            line = f"✘ {job.url[:44]}  {job.error or 'unknown error'}"
            kind = "info"
        return Entry("class:err", line, kind, job)

    @staticmethod
    def _hint_entry(job: Job, again: bool = False) -> Entry:
        tail = (
            "still unconfirmed, ↑ to add another hint"
            if again
            else "artist unconfirmed, ↑ to add a hint"
        )
        return Entry(
            "class:warn",
            f"⚠ “{(job.title or '')[:40]}” — {tail}",
            "hint",
            job,
        )

    def _set_active(self, seq: int, text: str, *, path: Path | None = None) -> None:
        with self.lock:
            self.active[seq] = text
            self._active_paths[seq] = path
        self.refresh()

    def _note_done(self, job: Job, tags: TrackTags | None) -> None:
        detail = self._detail_line(tags) if tags else None
        self.add_entry(self._done_entry(job, detail))

    def _note_low_confidence(self, job: Job, again: bool = False) -> None:
        self.add_entry(self._hint_entry(job, again))

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
        self._submit_jobs([Job(url=url) for url in urls])

    @staticmethod
    def _reset_for_retry(job: Job) -> None:
        job.status = "queued"
        job.error = None
        job.error_detail = None
        job.retryable = True

    def _submit_jobs(self, jobs: list[Job]) -> list[Job]:
        if not jobs:
            return []
        for job in jobs:
            self._reset_for_retry(job)
        with self.activity_lock:
            claimed_ids = self._persist("queue_jobs", jobs, self.session_id)
            claimed_jobs = (
                jobs
                if claimed_ids is None
                else [job for job in jobs if job.history_id in claimed_ids]
            )
            skipped = len(jobs) - len(claimed_jobs)
            with self.lock:
                self.queued += len(claimed_jobs)
                self._queued_jobs.extend(claimed_jobs)
                for job in claimed_jobs:
                    self._run_outcomes[job.history_id] = "queued"
        if skipped:
            self.msg(
                "class:dim",
                f"{skipped} job{'s are' if skipped != 1 else ' is'} "
                "already active in another Transmute session",
            )
        with self.lock:
            settings = self.settings
        self.refresh()
        for job in claimed_jobs:
            self.pool.submit(self._process, job, settings)
        return claimed_jobs

    def _record_failed_outcome(self, job: Job) -> None:
        with self.activity_lock:
            self._persist("save_failure", job)
            with self.lock:
                self._run_outcomes[job.history_id] = "error"
            self._note_failed(job)

    def _record_successful_outcome(
        self,
        job: Job,
        tags: TrackTags | None,
    ) -> None:
        detail = self._detail_line(tags) if tags else None
        needs_hint = tags is not None and tags.confidence == "low"
        with self.activity_lock:
            self._persist("save_success", job, detail, needs_hint)
            with self.lock:
                self._run_outcomes[job.history_id] = "done"
            self._record_completed(job)
            self._note_done(job, tags)
            if needs_hint:
                self._note_low_confidence(job)

    def _process(self, job: Job, settings: Settings) -> None:
        with self.lock:
            self._seq += 1
            seq = self._seq
            self.queued -= 1
            self._queued_jobs = [queued for queued in self._queued_jobs if queued is not job]
            self.active[seq] = f"starting  {job.url[:50]}"
            self._active_paths[seq] = job.path
        self.refresh()

        def on_progress(j: Job, frac: float | None) -> None:
            name = (j.title or j.url)[:44]
            phase = "converting  " if frac is None else f"↓ {frac:>4.0%}  "
            self._set_active(seq, phase + name, path=j.path)

        try:
            download_job(job, settings, on_progress)
            with self.lock:
                self._active_paths[seq] = job.path
            if job.status != "done":
                self._record_failed_outcome(job)
                return

            tags = None
            if self.enricher.enabled:
                self._set_active(seq, f"enriching  {(job.title or '')[:44]}", path=job.path)
                tags = self._enrich(job)
                with self.lock:
                    self._active_paths[seq] = job.path

            self._record_successful_outcome(job, tags)
        except Exception as error:  # noqa: BLE001
            message = self._error_line(error)
            if job.status == "done":
                self.msg("class:warn", f"⚠ post-processing skipped: {message}")
                self._record_successful_outcome(job, None)
            else:
                job.status = "error"
                job.error = message
                self._record_failed_outcome(job)
        finally:
            with self.lock:
                self.active.pop(seq, None)
                self._active_paths.pop(seq, None)
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

    def _rehint(
        self,
        job: Job,
        hint: str,
        placeholder: Entry,
        claim_token: str | None,
    ) -> None:
        tags = self._enrich(job, hint=hint, failure_label="retry failed")
        with self.activity_lock:
            self._remove_entry(placeholder)
            if tags is not None:
                needs_hint = tags is not None and tags.confidence == "low"
                persisted = (
                    self._persist(
                        "save_hint_success",
                        job,
                        self._detail_line(tags),
                        needs_hint,
                        claim_token,
                    )
                    if claim_token
                    else None
                )
                if persisted is False:
                    self.msg(
                        "class:dim",
                        "hint result was superseded by another Transmute session",
                    )
                    return
                with self.lock:
                    self.history = [
                        entry
                        for entry in self.history
                        if not (entry.job is job and entry.kind == "ok")
                    ]
                self._record_completed(job)
                self._note_done(job, tags)
                if needs_hint:
                    self._note_low_confidence(job, again=True)
            else:
                released = (
                    self._persist("release_hint", job.history_id, claim_token)
                    if claim_token
                    else None
                )
                if released is False:
                    return
                with self.lock:
                    has_done_entry = any(
                        entry.job is job and entry.kind == "ok"
                        for entry in self.history
                    )
                self._record_completed(job)
                if not has_done_entry:
                    self._note_done(job, None)
                self._note_low_confidence(job, again=True)

    def _retry_selected(self) -> None:
        with self.activity_lock:
            with self.lock:
                if self.sel is None or self.sel >= len(self.history):
                    return
                entry = self.history[self.sel]
                if entry.kind != "err" or entry.job is None:
                    return
            # Queue durably before hiding the old failure, so a process crash
            # cannot lose the only retryable record.
            self._submit_jobs([entry.job])
            with self.lock:
                try:
                    self.history.remove(entry)
                except ValueError:
                    pass
                self.failed = [j for j in self.failed if j is not entry.job]
                self.sel = None
        self._update_focus()

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
        if buff is self.dir_buffer:
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
        with self.activity_lock:
            with self.lock:
                if self.sel is None or self.sel >= len(self.history):
                    return False
                selected = self.sel
                entry = self.history[selected]
                if entry.kind != "hint" or entry.job is None:
                    return False
                job = entry.job

            claim = self._persist("claim_hint", job, self.session_id)
            if claim is False:
                self.msg(
                    "class:dim",
                    "that hint is already being checked in another Transmute session",
                )
                return False
            claim_token = claim if isinstance(claim, str) else None
            placeholder = Entry(
                "class:dim",
                f"⧗ re-checking “{(job.title or '')[:40]}” with your hint…",
                "info",
                job,
            )
            with self.lock:
                if (
                    selected >= len(self.history)
                    or self.history[selected] is not entry
                    or self.sel != selected
                ):
                    replaced = False
                else:
                    self.history[selected] = placeholder
                    self.sel = None
                    replaced = True
            if not replaced:
                if claim_token:
                    self._persist("release_hint", job.history_id, claim_token)
                return False
        self._update_focus()
        try:
            self.pool.submit(
                self._rehint,
                job,
                text,
                placeholder,
                claim_token,
            )
        except RuntimeError as error:
            with self.activity_lock:
                if claim_token:
                    self._persist("release_hint", job.history_id, claim_token)
                with self.lock:
                    try:
                        index = self.history.index(placeholder)
                    except ValueError:
                        pass
                    else:
                        self.history[index] = entry
            self.msg("class:err", f"could not schedule hint lookup: {error}")
        self.refresh()
        return False

    def run(self) -> None:
        if not shutil.which("ffmpeg"):
            self.msg(
                "class:warn",
                "⚠ ffmpeg not found — install it with: brew install ffmpeg",
            )
        self._startup_enrichment_notice()
        self.pool.submit(self._check_update)
        drained = False
        try:
            try:
                self.app.run(pre_run=self._start_signin_recheck)
            except (EOFError, KeyboardInterrupt):
                pass  # non-tty input ends with EOF; treat as a normal quit

            # Back in the normal terminal: drain any in-flight work.
            with self.lock:
                busy = len(self.active) + self.queued
            if busy:
                print(
                    f"finishing {busy} job{'s' if busy != 1 else ''}… "
                    "(ctrl-c to abandon)"
                )
            try:
                self.pool.shutdown(wait=True)
            except KeyboardInterrupt:
                pass
            else:
                drained = True
        finally:
            # A Ctrl-C during executor draining does not actually stop Python's
            # worker threads. Keep this session live until the process exits so
            # another REPL cannot claim and mutate the same file concurrently.
            if self.session_id and drained:
                self._persist("finish_session", self.session_id)

        with self.lock:
            done = sum(state == "done" for state in self._run_outcomes.values())
            failed = sum(state == "error" for state in self._run_outcomes.values())
            out_dir = self.settings.out_dir
        print(f"{done} converted · {failed} failed · files in {out_dir}")
