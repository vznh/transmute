"""Slash commands. Methods named cmd_<name> are dispatched from /<name>."""

from __future__ import annotations

import getpass
import os
import subprocess
from pathlib import Path

from .config import QUALITIES

# The command reference shown by /help. Rendered as a full-screen takeover
# (see App.open_help) rather than streamed through the transient message log,
# so no rows get clipped.
HELP_ROWS = [
    ("/out [dir]", "set output dir — arg is quick; no arg opens the picker"),
    ("/quality [128|192|256|320]", "show or set MP3 bitrate (kbps)"),
    ("/enrich [codex|claude|api|on|off]", "choose or toggle metadata enrichment"),
    ("/key [clear]", "securely set or clear one API key"),
    ("/list", "show all tracks from this session"),
    ("/retry", "requeue all failed downloads"),
    ("/login [codex|claude]", "log in to a subscription provider"),
    ("/logout [codex|claude]", "log out of a subscription provider"),
    ("/clear", "clear history and messages"),
    ("/help", "show this command reference"),
    ("/quit", "exit (or ctrl-d / double ctrl-c)"),
    ("↑/↓", "select failed or low-confidence entries in History"),
]


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
        self.app.open_help()

    def _set_out_dir(self, text: str) -> None:
        path = Path(text).expanduser()
        if not path.is_absolute():
            path = Path.home() / text  # bare names are home-relative
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            self.app.msg("class:err", f"can't use {path}: {e}")
            return
        self.app.set_out_dir(path)
        self.app.msg("class:dim", f"output directory: {path}")

    def cmd_out(self, arg: str) -> None:
        """`/out <dir>` sets the directory in one shot (resolve, auto-create,
        set). `/out` with no arg opens the interactive picker: the prompt is
        rooted at ~/ (uneditable), completes real folder names as you type, and
        offers to create a missing folder. Esc cancels."""
        if arg:
            self._set_out_dir(arg)
            return
        settings = self.app.settings_snapshot()
        self.app.msg("class:dim", f"output directory: {settings.out_dir}")
        self.app.open_dir_picker()

    def cmd_quality(self, arg: str) -> None:
        if arg:
            if arg not in QUALITIES:
                self.app.msg(
                    "class:err", f"quality must be one of {', '.join(QUALITIES)}"
                )
                return
            self.app.set_quality(arg)
        quality = self.app.settings_snapshot().quality
        self.app.msg("class:dim", f"quality: {quality}k")

    def cmd_enrich(self, arg: str) -> None:
        if arg == "off":
            self.app.enricher.enabled = False
        elif arg == "on":
            self.app.enricher.enabled = True
        elif arg == "api":
            if not self.app.enricher.use_api_key():
                self.app.msg("class:err", self.app.enricher.last_error)
                return
        elif arg in ("codex", "claude"):
            self.app.enricher.use_backend(arg)
        elif arg:
            self.app.msg(
                "class:err",
                "enrichment must be codex, claude, api, on, or off",
            )
            return
        state = "on" if self.app.enricher.enabled else "off"
        self.app.msg(
            "class:dim",
            f"metadata enrichment: {state} · {self.app.enricher.backend_label}",
        )

    def _apply_key(self, key: str) -> None:
        try:
            self.app.enricher.set_api_key(key)
        except ValueError as e:
            self.app.msg("class:err", str(e))
            return
        self.app.msg(
            "class:ok",
            f"{self.app.enricher.backend_label} key active — API enrichment prioritized",
        )

    def cmd_key(self, arg: str) -> None:
        if arg == "clear":
            self.app.enricher.clear_api_key()
            source = (
                " from environment"
                if self.app.enricher.api_key_source == "environment"
                else ""
            )
            self.app.msg(
                "class:dim",
                f"entered API key cleared · "
                f"{self.app.enricher.backend_label}{source}",
            )
            return
        if arg:
            self.app.msg(
                "class:err",
                "for security, run /key with no argument and use the masked prompt",
            )
            return

        from prompt_toolkit.application import run_in_terminal

        app = self.app

        async def task():
            try:
                key = await run_in_terminal(
                    lambda: getpass.getpass(
                        "API key (OpenAI sk-… or Anthropic sk-ant-…): "
                    )
                )
            except (EOFError, KeyboardInterrupt):
                key = ""
            if key.strip():
                self._apply_key(key)
            else:
                app.msg("class:dim", "API key entry cancelled")

        app.app.create_background_task(task())

    def cmd_login(self, arg: str) -> None:
        from prompt_toolkit.application import run_in_terminal

        app = self.app
        provider = arg or "claude"
        if provider not in ("codex", "claude"):
            app.msg("class:err", "login provider must be codex or claude")
            return
        label = "Codex / ChatGPT" if provider == "codex" else "Claude"
        command = (
            ["codex", "login"]
            if provider == "codex"
            else ["claude", "auth", "login", "--claudeai"]
        )

        async def task():
            def blocking() -> int:
                print(f"Launching {label} login (browser will open)…\n")
                try:
                    return subprocess.run(
                        command,
                        timeout=300,
                        check=False,
                    ).returncode
                except FileNotFoundError:
                    return -1
                except subprocess.TimeoutExpired:
                    return -2

            rc = await run_in_terminal(blocking)
            if rc == 0:
                if app.enricher.has_api_key:
                    app.msg(
                        "class:ok",
                        f"logged in to {label} · "
                        f"{app.enricher.backend_label} remains prioritized",
                    )
                else:
                    app.enricher.use_backend(provider)
                    app.msg(
                        "class:ok",
                        f"logged in to {app.enricher.backend_label} "
                        "— enrichment enabled",
                    )
            elif rc == -1:
                app.msg(
                    "class:err",
                    f"{provider} CLI not found — install it first",
                )
            elif rc == -2:
                app.msg("class:err", f"{provider} login timed out")
            else:
                app.msg("class:err", "login failed or was cancelled")

        app.app.create_background_task(task())

    def cmd_logout(self, arg: str) -> None:
        app = self.app
        provider = arg or "claude"
        if provider not in ("codex", "claude"):
            app.msg("class:err", "logout provider must be codex or claude")
            return
        command = (
            ["codex", "logout"] if provider == "codex" else ["claude", "auth", "logout"]
        )
        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except FileNotFoundError:
            app.msg("class:err", f"{provider} CLI not found — nothing to log out of")
            return
        except subprocess.TimeoutExpired:
            app.msg("class:err", f"{provider} logout timed out")
            return
        if proc.returncode == 0:
            if app.enricher.backend == provider:
                app.enricher.enabled = False
            label = "Codex / ChatGPT" if provider == "codex" else "Claude"
            app.msg(
                "class:dim",
                f"logged out of {label}",
            )
        else:
            app.msg(
                "class:err",
                (proc.stderr or proc.stdout).strip()[:120] or "logout failed",
            )
        if provider == "claude" and os.environ.get("ANTHROPIC_API_KEY"):
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
            app.messages.clear()
            app.sel = None
        app.refresh()
