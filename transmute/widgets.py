"""Reusable prompt_toolkit pieces with no app-state dependencies."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from prompt_toolkit.layout.processors import Processor, Transformation


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
