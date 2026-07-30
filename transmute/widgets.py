"""Reusable prompt_toolkit pieces with no app-state dependencies."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.layout.processors import Processor, Transformation


class HomeDirCompleter(Completer):
    """Suggests existing directories under ~/ for the output-directory picker.

    The picker holds only the path *relative to home* in the input buffer (the
    "~/" is a fixed prompt prefix), so completions resolve against
    ``Path.home()``. `active` gates completion so the shared input buffer stays
    plain when it is being used to paste URLs.
    """

    def __init__(self, active: Callable[[], bool]) -> None:
        self.active = active

    def get_completions(self, document, complete_event):
        if not self.active():
            return
        head, _, tail = document.text_before_cursor.lstrip("/").rpartition("/")
        base = Path.home() / head if head else Path.home()
        try:
            names = sorted(p.name for p in base.iterdir() if p.is_dir())
        except OSError:
            return
        for name in names:
            if name.startswith(tail):
                yield Completion(name + "/", start_position=-len(tail))


@dataclass
class Modal:
    """A temporary takeover of the bottom input line (reusable for any command).

    on_submit receives the entered text; empty or untouched text means
    "do nothing". Esc cancels without calling on_submit.
    """

    prefix: str
    placeholder: str
    on_submit: Callable[[str], None]
    initial: str = ""
    hint: str = "enter applies · esc cancels"


class PlaceholderProcessor(Processor):
    def __init__(self, text: str | Callable[[], str]) -> None:
        self.text = text

    def apply_transformation(self, ti):
        if ti.lineno == 0 and not ti.document.text:
            text = self.text() if callable(self.text) else self.text
            return Transformation([("class:placeholder", text)])
        return Transformation(ti.fragments)
