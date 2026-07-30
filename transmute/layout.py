"""Window arrangement. Takes the App for state/render callables; owns no state."""

from __future__ import annotations

from prompt_toolkit.buffer import Buffer
from prompt_toolkit.filters import Condition
from prompt_toolkit.history import FileHistory
from prompt_toolkit.layout import ConditionalContainer, HSplit, Layout, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension

from .config import HISTORY_FILE
from .style import HINT_PLACEHOLDER, PLACEHOLDER
from .widgets import PlaceholderProcessor


def build_layout(app) -> Layout:
    """Create buffers + windows on `app` and return the assembled Layout."""
    app.input_buffer = Buffer(
        history=FileHistory(str(HISTORY_FILE)),
        multiline=False,
        accept_handler=app._accept,
        enable_history_search=True,
    )
    app.hint_buffer = Buffer(multiline=False, accept_handler=app._accept_hint)

    input_window = Window(
        BufferControl(
            app.input_buffer,
            input_processors=[
                PlaceholderProcessor(
                    lambda: app.modal.placeholder if app.modal else PLACEHOLDER
                )
            ],
        ),
        height=1,
        get_line_prefix=lambda _line_no, _wrap_count: [
            ("class:prompt", app.modal.prefix if app.modal else "❯ ")
        ],
    )
    hint_window = Window(
        BufferControl(
            app.hint_buffer,
            input_processors=[PlaceholderProcessor(HINT_PLACEHOLDER)],
        ),
        height=1,
        get_line_prefix=lambda _line_no, _wrap_count: [("class:prompt", "      ↳ ")],
    )

    root = HSplit(
        [
            Window(FormattedTextControl(app._render_header), height=3),
            Window(height=1, char="─", style="class:rule"),
            Window(
                FormattedTextControl(app._render_above),
                height=app._above_height,
                wrap_lines=False,
            ),
            ConditionalContainer(
                hint_window,
                filter=Condition(lambda: app._sel_kind() == "hint"),
            ),
            Window(
                FormattedTextControl(app._render_below),
                height=Dimension(weight=1),
                wrap_lines=False,
            ),
            Window(height=1, char="─", style="class:rule"),
            input_window,
            Window(FormattedTextControl(app._input_hint), height=1),
            Window(FormattedTextControl(app._toolbar), height=1),
        ]
    )
    return Layout(root, focused_element=input_window)
