"""Key bindings. Takes the App for state access; owns no state."""

from __future__ import annotations

import time

from prompt_toolkit.filters import Condition, has_completions, has_focus
from prompt_toolkit.key_binding import KeyBindings


def _exit_shortcut() -> str:
    return "CTRL+C"


def build_key_bindings(app) -> KeyBindings:
    kb = KeyBindings()
    # Main input actions remain live while help and the folder editor are shown.
    no_overlay = Condition(
        lambda: app.modal is None and app.dir_picker is None and not app.help_open
    )
    has_actionable = Condition(lambda: bool(app._actionable())) & no_overlay
    has_selection = Condition(app.has_selection)
    sel_is_err = (
        Condition(lambda: app._sel_kind() == "err" and not app.input_buffer.text)
        & no_overlay
    )
    picker_confirm = Condition(
        lambda: app.dir_picker is not None and app.dir_picker.stage == "confirm"
    )
    dir_focused = has_focus(app.dir_buffer)

    @kb.add("up", filter=has_actionable)
    def _(event):
        app._move_sel(-1)

    @kb.add("down", filter=has_actionable & has_selection)
    def _(event):
        app._move_sel(1)

    @kb.add("y", filter=picker_confirm & dir_focused)
    def _(event):
        app._dir_confirm_create()

    @kb.add("n", filter=picker_confirm & dir_focused)
    def _(event):
        app._dir_decline_create()

    @kb.add("escape", eager=True)
    def _(event):
        if app.dir_picker is not None:
            app.close_dir_picker()
            return
        if app.help_open:
            app.close_help()
            return
        if app.modal:
            app.close_modal()
            return
        app.clear_selection()

    @kb.add("enter", filter=sel_is_err & has_focus(app.input_buffer))
    def _(event):
        app._retry_selected()

    @kb.add("c-c")
    def _(event):
        now = time.monotonic()
        if app.dir_picker is not None:
            app.close_dir_picker()
            return
        if app.help_open:
            app.close_help()
            return
        if app.has_selection():
            app.clear_selection()
            app.clear_input_notice()
            app._last_ctrl_c = now
            return
        if app.input_buffer.text:
            app.input_buffer.reset()
            app.clear_input_notice()
            app._last_ctrl_c = now
            return
        if now - app._last_ctrl_c <= 2.0:
            event.app.exit()
        else:
            app._last_ctrl_c = now
            with app.lock:
                busy = len(app.active) + app.queued
            note = (
                f" ({busy} job{'s' if busy != 1 else ''} still running)" if busy else ""
            )
            app.show_input_notice(
                "class:input.warn",
                f"press {_exit_shortcut()} again to exit{note}",
                duration=2.0,
            )

    @kb.add("c-d")
    def _(event):
        event.app.exit()

    @kb.add(
        "tab",
        filter=Condition(lambda: app.dir_picker is not None) & ~has_completions,
    )
    def _(event):
        app.toggle_dir_focus()

    @kb.add("s-tab", filter=Condition(lambda: app.dir_picker is not None))
    def _(event):
        app.toggle_dir_focus()

    return kb
