"""State-machine tests for the App: selection, retry, hints, modal input.

These construct the real App (headless is fine — the Application is built but
never run) and stub out the thread pool so nothing touches the network.
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from prompt_toolkit.keys import Keys

from transmute.app import App, Entry
from transmute.downloader import Job


class RecordingPool:
    def __init__(self):
        self.calls = []

    def submit(self, fn, *args):
        self.calls.append((fn.__name__, args))


@pytest.fixture
def app():
    a = App()
    a.pool = RecordingPool()
    return a


def test_display_name_prefers_path(app):
    job = Job(url="u", title="Song", path=Path("/x/Artist - Song.mp3"))
    assert app._display_name(job) == "Artist - Song.mp3"
    assert app._display_name(Job(url="u", title="Song")) == "Song"
    assert app._display_name(Job(url="u")) == "u"


def test_selection_walks_actionable_entries_only(app):
    app.add_entry(Entry("class:ok", "done", "ok", Job(url="u1")))
    app.add_entry(Entry("class:err", "failed", "err", Job(url="u2")))
    app.add_entry(Entry("class:warn", "hint me", "hint", Job(url="u3", title="t")))

    assert app._actionable() == [1, 2]
    app._move_sel(-1)
    assert app.sel == 2 and app._sel_kind() == "hint"
    app._move_sel(-1)
    assert app.sel == 1 and app._sel_kind() == "err"
    app._move_sel(1)
    assert app.sel == 2
    app._move_sel(1)
    assert app.sel is None  # down past newest deselects


def test_selected_entry_highlighted_and_hint_splits_body(app):
    app.add_entry(Entry("class:warn", "hint me", "hint", Job(url="u", title="t")))
    app.add_entry(Entry("class:err", "failed", "err", Job(url="u2")))
    app.sel = 0
    above, below = app._build()
    assert any("class:selected" in style for style, _ in above)
    assert any("failed" in text for _, text in below)


def test_enter_retries_selected_failure(app):
    submitted = []
    app.submit_urls = lambda urls: submitted.append(urls)
    app.add_entry(Entry("class:err", "failed", "err", Job(url="u2")))
    app.sel = 0
    app._retry_selected()
    assert submitted == [["u2"]]
    assert app.history == [] and app.sel is None


def test_hint_submission_dispatches_rehint(app):
    app.add_entry(Entry("class:warn", "hint me", "hint", Job(url="u", title="t")))
    app.sel = 0
    app.hint_buffer.text = "actually by X"
    app._accept_hint(app.hint_buffer)
    assert app.pool.calls[-1][0] == "_rehint"
    assert app.history[0].kind == "info" and app.sel is None


def test_out_arg_sets_dir_in_one_shot(app, tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    app.commands.cmd_out("Music")  # bare name is home-relative, auto-created
    target = tmp_path / "Music"
    assert target.is_dir()
    assert app.settings.out_dir == target
    assert app.dir_picker is None  # arg form never opens the picker


def test_out_no_arg_opens_picker_with_locked_home_prefix(app):
    app.commands.cmd_out("")
    assert app.dir_picker is not None and app.dir_picker.stage == "input"
    assert app.input_buffer.text == ""  # "~/" is a prefix, not editable buffer text
    assert app._input_prefix() == [("class:prompt", "❯ "), ("class:accent", "~/")]
    assert app._input_hint() == [
        ("class:input.hint", "  esc to cancel output directory change")
    ]


def test_out_picker_existing_path_sets_dir_and_closes(app):
    app.commands.cmd_out("")
    app.input_buffer.text = ""  # empty relative path → ~/ itself, which exists
    app._accept(app.input_buffer)
    assert app.dir_picker is None
    assert app.settings.out_dir == Path.home()


def test_out_picker_missing_path_prompts_to_create(app):
    app.commands.cmd_out("")
    app.input_buffer.text = "no-such-folder-xyz/deep"
    app._accept(app.input_buffer)
    picker = app.dir_picker
    assert picker is not None and picker.stage == "confirm"
    assert picker.typed == "no-such-folder-xyz/deep"
    assert picker.pending == Path.home() / "no-such-folder-xyz/deep"
    assert app.input_buffer.text == ""  # cleared while asking y/n
    assert "(y/n)" in app._input_placeholder()


def test_out_picker_decline_returns_to_path_input(app):
    app.commands.cmd_out("")
    app.input_buffer.text = "no-such-folder-xyz"
    app._accept(app.input_buffer)
    app._dir_decline_create()  # 'n'
    assert app.dir_picker.stage == "input"
    assert app.input_buffer.text == "no-such-folder-xyz"  # restored for editing
    assert app.dir_picker.pending is None


def test_out_picker_confirm_creates_folder_and_sets_dir(app, tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    app.commands.cmd_out("")
    app.input_buffer.text = "fresh/nested"
    app._accept(app.input_buffer)
    app._dir_confirm_create()  # 'y'
    target = tmp_path / "fresh/nested"
    assert target.is_dir()
    assert app.settings.out_dir == target
    assert app.dir_picker is None
    assert any("created" in line for _, line in app.messages)


def test_out_picker_escape_cancels_without_changing_dir(app):
    before = app.settings.out_dir
    app.commands.cmd_out("")
    app.close_dir_picker()  # esc
    assert app.dir_picker is None and app.input_buffer.text == ""
    assert app.settings.out_dir == before


def test_unknown_command_reports_error(app):
    app.commands.dispatch("/nope")
    assert any("unknown command" in line for _, line in app.messages)


def test_default_input_hint_is_below_prompt_help(app):
    assert app._input_hint() == [("class:input.hint", "  /help for more commands")]


def test_input_notice_overrides_help_hint(app):
    app.show_input_notice("class:input.warn", "press ctrl-c again to exit")
    assert app._input_hint() == [("class:input.warn", "  press ctrl-c again to exit")]
    app.clear_input_notice()
    assert "/help for more commands" in app._input_hint()[0][1]


def test_modal_uses_contextual_input_hint(app):
    from transmute.widgets import Modal

    app.open_modal(Modal(prefix="x ❯ ", placeholder="p", on_submit=lambda _t: None))
    assert "enter applies · esc cancels" in app._input_hint()[0][1]


def test_ctrl_c_warns_below_input_then_exits(app):
    event = SimpleNamespace(app=SimpleNamespace(exit=Mock()))
    binding = app.app.key_bindings.get_bindings_for_keys((Keys.ControlC,))[-1]

    binding.handler(event)
    assert "press ctrl-c again to exit" in app._input_hint()[0][1]
    event.app.exit.assert_not_called()

    binding.handler(event)
    event.app.exit.assert_called_once()


def test_url_paste_submits(app):
    submitted = []
    app.submit_urls = lambda urls: submitted.append(urls)
    app.input_buffer.text = "https://a.com/1https://b.com/2"
    app._accept(app.input_buffer)
    assert submitted == [["https://a.com/1", "https://b.com/2"]]
