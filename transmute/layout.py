"""Window arrangement. Takes the App for state/render callables; owns no state."""

from __future__ import annotations

from pathlib import Path

from prompt_toolkit.buffer import Buffer
from prompt_toolkit.filters import Condition
from prompt_toolkit.history import FileHistory
from prompt_toolkit.layout import (
    ConditionalContainer,
    Float,
    FloatContainer,
    HSplit,
    Layout,
    ScrollablePane,
    Window,
)
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.menus import CompletionsMenu

from .style import HINT_PLACEHOLDER
from .widgets import HomeDirCompleter, PlaceholderProcessor, ScrollableHelpControl


def build_layout(app, history_file: Path) -> Layout:
    """Create buffers + windows on `app` and return the assembled Layout."""

    app.input_buffer = Buffer(
        history=FileHistory(str(history_file)),
        multiline=False,
        accept_handler=app._accept,
        enable_history_search=True,
    )
    app.dir_buffer = Buffer(
        multiline=False,
        accept_handler=app._accept,
        completer=HomeDirCompleter(
            lambda: app.dir_picker is not None and app.dir_picker.stage == "input"
        ),
        complete_while_typing=True,
    )
    app.hint_buffer = Buffer(multiline=False, accept_handler=app._accept_hint)

    input_window = Window(
        BufferControl(
            app.input_buffer,
            input_processors=[PlaceholderProcessor(app._input_placeholder)],
        ),
        height=1,
        get_line_prefix=lambda _line_no, _wrap_count: app._input_prefix(),
    )
    hint_window = Window(
        BufferControl(
            app.hint_buffer,
            input_processors=[PlaceholderProcessor(HINT_PLACEHOLDER)],
        ),
        height=1,
        get_line_prefix=lambda _line_no, _wrap_count: [("class:prompt", "      ↳ ")],
    )
    dir_window = Window(
        BufferControl(
            app.dir_buffer,
            input_processors=[PlaceholderProcessor(app._dir_placeholder)],
        ),
        height=1,
        get_line_prefix=lambda _line_no, _wrap_count: app._dir_prefix(),
    )
    app.help_pane = ScrollablePane(
        Window(
            ScrollableHelpControl(app._render_help, app.scroll_help), wrap_lines=True
        )
    )
    app.sync_control = ScrollableHelpControl(app._render_sync, app.sync_scroll, focusable=True)
    app.sync_pane = ScrollablePane(
        Window(app.sync_control, wrap_lines=False), keep_cursor_visible=False
    )
    normal_content = Condition(lambda: not app.help_open and not app.sync_open)

    root = HSplit(
        [
            Window(FormattedTextControl(app._render_header), height=4),
            Window(height=1, char="─", style="class:rule"),
            ConditionalContainer(
                Window(
                    FormattedTextControl(app._render_above),
                    height=app._above_height,
                    wrap_lines=False,
                ),
                filter=normal_content,
            ),
            ConditionalContainer(
                hint_window,
                filter=normal_content & Condition(lambda: app._sel_kind() == "hint"),
            ),
            ConditionalContainer(
                Window(
                    FormattedTextControl(app._render_below),
                    height=Dimension(weight=1),
                    wrap_lines=False,
                ),
                filter=normal_content,
            ),
            ConditionalContainer(app.help_pane, filter=Condition(lambda: app.help_open)),
            ConditionalContainer(app.sync_pane, filter=Condition(lambda: app.sync_open)),
            Window(height=1, char="─", style="class:rule"),
            ConditionalContainer(
                dir_window, filter=Condition(lambda: app.dir_picker is not None)
            ),
            input_window,
            Window(FormattedTextControl(app._status_line), height=1),
        ]
    )
    root = FloatContainer(
        root,
        floats=[
            Float(
                xcursor=True,
                ycursor=True,
                content=CompletionsMenu(max_height=8, scroll_offset=1),
            )
        ],
    )
    return Layout(root, focused_element=input_window)
