"""Slash commands. Methods named cmd_<name> are dispatched from /<name>."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .config import QUALITIES


class Commands:
    def __init__(self, app) -> None:
        self.app = app

    def dispatch(self, text: str) -> None:
        """Handle a /command line. /quit and friends exit the app."""
        name, _, arg = text.lstrip("/").partition(" ")
        if name in ("quit", "exit", "q"):
            self.app.app.exit()
            return
        handler = getattr(self, f"cmd_{name}", None)
        if handler:
            handler(arg.strip())
        else:
            self.app.msg("class:err", f"unknown command: /{name}  (/help for commands)")

    def cmd_help(self, _arg: str) -> None:
        rows = [
            ("/out [dir]", "set output dir — arg is quick; no arg opens the picker"),
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
            self.app.msg("class:dim", f"{cmd:<28}{desc}")

    def _set_out_dir(self, text: str) -> None:
        path = Path(text).expanduser()
        if not path.is_absolute():
            path = Path.home() / text  # bare names are home-relative
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            self.app.msg("class:err", f"can't use {path}: {e}")
            return
        self.app.settings.out_dir = path
        self.app.msg("class:dim", f"output directory: {self.app.settings.out_dir}")

    def cmd_out(self, arg: str) -> None:
        """`/out <dir>` sets the directory in one shot (resolve, auto-create,
        set). `/out` with no arg opens the interactive picker: the prompt is
        rooted at ~/ (uneditable), completes real folder names as you type, and
        offers to create a missing folder. Esc cancels."""
        if arg:
            self._set_out_dir(arg)
            return
        self.app.msg("class:dim", f"output directory: {self.app.settings.out_dir}")
        self.app.open_dir_picker()

    def cmd_quality(self, arg: str) -> None:
        if arg:
            if arg not in QUALITIES:
                self.app.msg(
                    "class:err", f"quality must be one of {', '.join(QUALITIES)}"
                )
                return
            self.app.settings.quality = arg
        self.app.msg("class:dim", f"quality: {self.app.settings.quality}k")

    def cmd_enrich(self, arg: str) -> None:
        if arg in ("on", "off"):
            self.app.enricher.enabled = arg == "on"
        state = "on" if self.app.enricher.enabled else "off"
        self.app.msg("class:dim", f"metadata enrichment: {state}")

    def cmd_login(self, _arg: str) -> None:
        from prompt_toolkit.application import run_in_terminal

        app = self.app

        async def task():
            def blocking() -> int:
                print("Launching Claude login (browser will open)…\n")
                try:
                    return subprocess.run(
                        ["claude", "auth", "login", "--claudeai"],
                        check=False,
                    ).returncode
                except FileNotFoundError:
                    return -1

            rc = await run_in_terminal(blocking)
            if rc == 0:
                app.enricher.enabled = True
                app.enricher.last_error = None
                app.msg("class:ok", "logged in to Claude — enrichment enabled")
            elif rc == -1:
                app.msg("class:err", "claude CLI not found — install Claude Code first")
            else:
                app.msg("class:err", "login failed or was cancelled")

        app.app.create_background_task(task())

    def cmd_logout(self, _arg: str) -> None:
        app = self.app
        try:
            proc = subprocess.run(
                ["claude", "auth", "logout"],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except FileNotFoundError:
            app.msg("class:err", "claude CLI not found — nothing to log out of")
            return
        except subprocess.TimeoutExpired:
            app.msg("class:err", "claude auth logout timed out")
            return
        if proc.returncode == 0:
            app.enricher.enabled = False
            app.msg(
                "class:dim",
                "logged out of Claude — enrichment disabled (/login to log back in)",
            )
        else:
            app.msg(
                "class:err",
                (proc.stderr or proc.stdout).strip()[:120] or "logout failed",
            )
        if os.environ.get("ANTHROPIC_API_KEY"):
            app.msg(
                "class:warn",
                "⚠ ANTHROPIC_API_KEY is still set in your shell — "
                "enrichment can still bill the API via that key",
            )

    def cmd_list(self, _arg: str) -> None:
        app = self.app
        with app.lock:
            completed, failed = list(app.completed), list(app.failed)
        if not completed and not failed:
            app.msg("class:dim", "nothing converted yet")
            return
        for job in completed:
            app.msg("class:ok", f"✔ {job.title or job.url}")
        for job in failed:
            app.msg("class:err", f"✘ {job.url}")

    def cmd_retry(self, _arg: str) -> None:
        app = self.app
        with app.lock:
            failed, app.failed = app.failed, []
            app.history = [e for e in app.history if e.kind != "err"]
            app.sel = None
        if not failed:
            app.msg("class:dim", "no failed downloads to retry")
            return
        app.submit_urls([j.url for j in failed])

    def cmd_clear(self, _arg: str) -> None:
        app = self.app
        with app.lock:
            app.history.clear()
            app.sel = None
        app.messages.clear()
        app.refresh()
