"""State-machine tests for the App: selection, retry, hints, modal input.

These construct the real App (headless is fine — the Application is built but
never run) and stub out the thread pool so nothing touches the network.
"""

import asyncio
import os
import stat
from pathlib import Path
from subprocess import TimeoutExpired
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from prompt_toolkit.application.current import set_app
from prompt_toolkit.data_structures import Size
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.keys import Keys
from prompt_toolkit.mouse_events import MouseButton, MouseEvent, MouseEventType
from prompt_toolkit.output import DummyOutput

from transmute.app import App, Entry
from transmute.config import Settings
from transmute.downloader import Job
from transmute.enrich import TrackTags
from transmute.history import ActivityStore, HistoryStoreError
from transmute.settings import SettingsStore


class RecordingPool:
    def __init__(self):
        self.calls = []

    def submit(self, fn, *args):
        self.calls.append((fn.__name__, args))

    def shutdown(self, wait=True):
        pass


@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return App(
        activity_store=ActivityStore(tmp_path / "activity.sqlite3"),
        history_file=tmp_path / "input-history",
        settings_store=SettingsStore(tmp_path / "settings.json"),
        pool=RecordingPool(),
    )


def test_display_name_prefers_path(app):
    job = Job(url="u", title="Song", path=Path("/x/Artist - Song.mp3"))
    assert app._display_name(job) == "Artist - Song.mp3"
    assert app._display_name(Job(url="u", title="Song")) == "Song"
    assert app._display_name(Job(url="u")) == "u"


def test_header_shows_codex_before_claude_when_both_connected(app):
    app.enricher.cli_states = {"claude": "signed_in", "codex": "signed_in"}

    fragments = app._render_header()

    assert fragments[-1] == ("class:ok", "  Codex is connected.\n")


def test_header_shows_claude_when_only_claude_is_connected(app):
    app.enricher.cli_states = {"claude": "signed_in", "codex": "missing"}

    assert app._render_header()[-1] == ("class:ok", "  Claude is connected.\n")


def test_homebrew_update_notice_appears_inline(app, monkeypatch):
    app.enricher.cli_states = {"claude": "missing", "codex": "signed_in"}
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout="transmute\n"))
    monkeypatch.setattr("transmute.app.subprocess.run", run)

    app._check_update()

    assert app._render_header()[-3:] == [
        ("class:ok", "  Codex is connected."),
        ("class:warn", "  Run `brew update; brew upgrade`"),
        ("", "\n"),
    ]
    assert run.call_args.kwargs["env"]["HOMEBREW_NO_AUTO_UPDATE"] == "1"
    assert run.call_args.kwargs["timeout"] == 10


@pytest.mark.parametrize("result", [
    SimpleNamespace(returncode=0, stdout=""),
    SimpleNamespace(returncode=1, stdout="transmute\n"),
])
def test_homebrew_update_notice_hidden_without_confirmed_update(app, monkeypatch, result):
    monkeypatch.setattr("transmute.app.subprocess.run", lambda *args, **kwargs: result)
    app._check_update()
    assert not app.update_available


def test_homebrew_update_check_tolerates_missing_or_slow_brew(app, monkeypatch):
    for error in (FileNotFoundError(), TimeoutExpired("brew", 10)):
        monkeypatch.setattr("transmute.app.subprocess.run", Mock(side_effect=error))
        app._check_update()
        assert not app.update_available


def test_header_offers_login_for_installed_cli_or_key_when_none(app):
    app.enricher.cli_states = {"claude": "signed_out", "codex": "signed_out"}
    assert app._render_header()[-1] == (
        "class:err",
        "  No provider is connected. Get started with /login codex.\n",
    )

    app.enricher.cli_states = {"claude": "missing", "codex": "missing"}
    assert app._render_header()[-1] == (
        "class:err",
        "  No provider is connected. Get started with /key.\n",
    )


def test_recheck_signin_posts_one_success_message(app, monkeypatch):
    monkeypatch.setattr(app.enricher, "refresh_subscription", lambda: "claude")
    app.messages.clear()

    assert app._recheck_signin() == "claude"
    assert list(app.messages) == [
        ("class:ok", "signed in to Claude — enrichment enabled")
    ]


def test_recheck_signin_is_silent_when_no_backend_available(app, monkeypatch):
    monkeypatch.setattr(app.enricher, "refresh_subscription", lambda: None)
    app.messages.clear()

    assert app._recheck_signin() is None
    assert not app.messages


def test_startup_notice_names_signed_out_cli(app):
    app.enricher.cli_states = {"claude": "signed_out", "codex": "signed_out"}
    app.enricher.backend = "none"
    app.messages.clear()

    app._startup_enrichment_notice()

    assert list(app.messages) == [
        (
            "class:warn",
            "⚠ Claude is installed but not signed in — run /login claude (checking every 15s)",
        )
    ]


def test_enrich_off_blocks_recheck_until_enrich_on(app, monkeypatch):
    monkeypatch.setattr("transmute.enrich._probe_claude", lambda: "missing")
    monkeypatch.setattr("transmute.enrich._probe_codex", lambda: "missing")
    app.enricher.backend = "none"
    app.commands.cmd_enrich("off")
    monkeypatch.setattr("transmute.enrich._probe_codex", lambda: "signed_in")

    assert app.enricher.refresh_subscription() is None
    app.commands.cmd_enrich("on")
    assert app.enricher.refresh_subscription() == "codex"


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_existing_custom_prompt_history_parent_permissions_are_unchanged(
    tmp_path,
):
    custom_parent = tmp_path / "shared"
    custom_parent.mkdir(mode=0o755)
    custom_parent.chmod(0o755)
    history_file = custom_parent / "prompt-history"

    App._prepare_input_history(history_file)

    assert stat.S_IMODE(custom_parent.stat().st_mode) == 0o755
    assert stat.S_IMODE(history_file.stat().st_mode) == 0o600


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


def test_done_entry_renders_indented_metadata_detail(app):
    job = Job(url="u", path=Path("/x/2hollis - bride.mp3"))
    tags = TrackTags(
        artist="2hollis",
        title="bride",
        album="Boy",
        year="2025",
        genre="Hyperpop",
    )
    app._note_done(job, tags)

    entry = app.history[-1]
    assert entry.line == "✔ 2hollis - bride.mp3"
    assert entry.detail == "2hollis • Boy • 2025 • Hyperpop"

    above, _ = app._build()
    assert any(entry.detail in text for _, text in above)


def test_done_entry_detail_notes_derivative_and_omits_empty_fields(app):
    job = Job(url="u", path=Path("/x/3enialis - flip.mp3"))
    tags = TrackTags(
        artist="3enialis",
        title="flip",
        genre="Hyperpop",
        kind="derivative",
        based_on="2hollis - bride",
    )
    app._note_done(job, tags)

    assert app.history[-1].detail == "3enialis • Hyperpop • derivative of 2hollis - bride"


def test_done_entry_without_tags_has_no_detail(app):
    app._note_done(Job(url="u", path=Path("/x/raw.mp3")), None)
    assert app.history[-1].detail is None


def test_enter_retries_selected_failure(app):
    submitted = []
    app._submit_jobs = lambda jobs: submitted.append([job.url for job in jobs])
    app.add_entry(Entry("class:err", "failed", "err", Job(url="u2")))
    app.sel = 0
    app._retry_selected()
    assert submitted == [["u2"]]
    assert app.history == [] and app.sel is None


def test_activity_restores_as_actionable_jobs_across_app_instances(tmp_path):
    db = tmp_path / "activity.sqlite3"
    track_path = tmp_path / "Artist - Song.mp3"
    track_path.touch()
    store = ActivityStore(db)
    session_id = store.start_session()

    done = Job(
        url="https://youtu.be/done",
        status="done",
        title="Song",
        uploader="Artist",
        duration=123,
        description="release notes",
        tags=["house"],
        path=track_path,
    )
    store.queue_job(done, session_id)
    done.status = "done"
    store.save_success(done, "Artist — Song", True)

    retryable = Job(
        url="https://youtu.be/retry",
        status="error",
        error="network error",
    )
    store.queue_job(retryable, session_id)
    retryable.status = "error"
    store.save_failure(retryable)

    permanent = Job(
        url="https://youtu.be/gone",
        status="error",
        error="video unavailable",
        retryable=False,
    )
    store.queue_job(permanent, session_id)
    permanent.status = "error"
    permanent.retryable = False
    store.save_failure(permanent)
    store.finish_session(session_id)

    restored = App(
        activity_store=ActivityStore(db),
        history_file=tmp_path / "prompt-history",
        settings_store=SettingsStore(tmp_path / "settings.json"),
        pool=RecordingPool(),
    )

    assert [entry.kind for entry in restored.history] == [
        "ok",
        "hint",
        "err",
        "info",
    ]
    assert restored.history[0].job is restored.completed[0]
    assert restored.history[1].job is restored.completed[0]
    assert restored.history[2].job is restored.failed[0]
    assert restored.history[0].line == "✔ Artist - Song.mp3"
    assert restored.history[0].detail == "Artist — Song"
    assert restored.completed[0].description == "release notes"
    assert restored._actionable() == [1, 2]
    assert restored.active == {} and restored.queued == 0 and restored.sel is None


def test_clear_stays_cleared_but_keeps_in_flight_jobs(tmp_path):
    db = tmp_path / "activity.sqlite3"
    first = App(
        activity_store=ActivityStore(db),
        history_file=tmp_path / "prompt-history-1",
        settings_store=SettingsStore(tmp_path / "settings.json"),
        pool=RecordingPool(),
    )

    old = Job(url="https://youtu.be/old", status="done", title="Old")
    first.activity_store.queue_job(old, first.session_id)
    old.status = "done"
    first.activity_store.save_success(old, None, False)
    first.completed.append(old)
    first.history.append(first._done_entry(old, None))

    in_flight = Job(url="https://youtu.be/new")
    first.activity_store.queue_job(in_flight, first.session_id)
    first.commands.cmd_clear("")
    assert first.history == [] and first.completed == [] and first.failed == []

    in_flight.status = "done"
    in_flight.title = "New"
    first.activity_store.save_success(in_flight, None, False)

    reopened = App(
        activity_store=ActivityStore(db),
        history_file=tmp_path / "prompt-history-2",
        settings_store=SettingsStore(tmp_path / "settings.json"),
        pool=RecordingPool(),
    )
    assert [job.title for job in reopened.completed] == ["New"]
    assert all("Old" not in entry.line for entry in reopened.history)


def test_clear_keeps_persistence_failure_visible(app, monkeypatch):
    app.msg("class:dim", "old message")
    monkeypatch.setattr(
        app.activity_store,
        "clear",
        Mock(side_effect=HistoryStoreError("database is locked")),
    )

    app.commands.cmd_clear("")

    assert all("old message" not in line for _, line in app.messages)
    assert any("history unavailable" in line for _, line in app.messages)


def test_missing_file_does_not_restore_actionable_hint(tmp_path):
    db = tmp_path / "activity.sqlite3"
    store = ActivityStore(db)
    session_id = store.start_session()
    job = Job(
        url="https://youtu.be/missing",
        status="done",
        title="Missing",
        path=tmp_path / "missing.mp3",
    )
    store.queue_job(job, session_id)
    job.status = "done"
    store.save_success(job, None, True)
    store.finish_session(session_id)

    restored = App(
        activity_store=ActivityStore(db),
        history_file=tmp_path / "prompt-history",
        settings_store=SettingsStore(tmp_path / "settings.json"),
        pool=RecordingPool(),
    )

    assert [entry.kind for entry in restored.history] == ["ok"]
    assert restored._actionable() == []


def test_hint_claimed_by_another_session_is_not_restored_as_actionable(tmp_path):
    db = tmp_path / "activity.sqlite3"
    track_path = tmp_path / "track.mp3"
    track_path.touch()
    seed = ActivityStore(db)
    seed_session = seed.start_session()
    job = Job(
        url="https://youtu.be/claimed-hint",
        title="Track",
        path=track_path,
    )
    seed.queue_job(job, seed_session)
    seed.save_success(job, "Artist", needs_hint=True)
    seed.finish_session(seed_session)

    owner = ActivityStore(db)
    owner_session = owner.start_session()
    restored_job = owner.load_jobs()[0].job
    assert isinstance(owner.claim_hint(restored_job, owner_session), str)

    observer = App(
        activity_store=ActivityStore(db),
        history_file=tmp_path / "prompt-history",
        settings_store=SettingsStore(tmp_path / "settings.json"),
        pool=RecordingPool(),
    )

    assert [entry.kind for entry in observer.history] == ["ok"]
    assert observer._actionable() == []
    owner.finish_session(owner_session)


def test_restored_activity_does_not_affect_current_run_summary(
    tmp_path, monkeypatch, capsys
):
    db = tmp_path / "activity.sqlite3"
    store = ActivityStore(db)
    session_id = store.start_session()
    job = Job(url="https://youtu.be/old", status="error", error="network error")
    store.queue_job(job, session_id)
    job.status = "error"
    store.save_failure(job)
    store.finish_session(session_id)

    restored = App(
        activity_store=ActivityStore(db),
        history_file=tmp_path / "prompt-history",
        settings_store=SettingsStore(tmp_path / "settings.json"),
        pool=RecordingPool(),
    )
    monkeypatch.setattr(restored.app, "run", Mock())
    restored.run()

    assert "0 converted · 0 failed" in capsys.readouterr().out


def test_interrupted_executor_drain_keeps_session_claims_live(app, monkeypatch):
    finish_session = Mock(wraps=app.activity_store.finish_session)
    monkeypatch.setattr(app.activity_store, "finish_session", finish_session)
    monkeypatch.setattr(app.app, "run", Mock())
    app.pool.shutdown = Mock(side_effect=KeyboardInterrupt)

    app.run()

    finish_session.assert_not_called()


def test_retry_all_skips_nonretryable_failures(app):
    retryable = Job(url="https://youtu.be/retry", status="error")
    permanent = Job(
        url="https://youtu.be/gone",
        status="error",
        retryable=False,
    )
    app.failed = [retryable, permanent]
    app.history = [
        app._failure_entry(retryable),
        app._failure_entry(permanent),
    ]
    submitted = []
    app._submit_jobs = lambda jobs: submitted.extend(jobs)

    app.commands.cmd_retry("")

    assert submitted == [retryable]
    assert app.failed == [permanent]
    assert [entry.job for entry in app.history] == [permanent]


def test_two_apps_cannot_both_retry_the_same_failure(tmp_path):
    db = tmp_path / "activity.sqlite3"
    seed = ActivityStore(db)
    session_id = seed.start_session()
    job = Job(url="https://youtu.be/retry", status="error", error="network error")
    seed.queue_job(job, session_id)
    seed.save_failure(job)
    seed.finish_session(session_id)

    first = App(
        activity_store=ActivityStore(db),
        history_file=tmp_path / "prompt-history-1",
        settings_store=SettingsStore(tmp_path / "settings.json"),
        pool=RecordingPool(),
    )
    second = App(
        activity_store=ActivityStore(db),
        history_file=tmp_path / "prompt-history-2",
        settings_store=SettingsStore(tmp_path / "settings.json"),
        pool=RecordingPool(),
    )
    first.sel = first._actionable()[0]
    second.sel = second._actionable()[0]

    first._retry_selected()
    second._retry_selected()

    assert len(first.pool.calls) == 1
    assert second.pool.calls == []
    assert second.queued == 0
    assert any("already active" in line for _, line in second.messages)


def test_hint_submission_dispatches_rehint(app):
    job = Job(url="u", title="t")
    app.activity_store.queue_job(job, app.session_id)
    app.activity_store.save_success(job, None, needs_hint=True)
    app.add_entry(Entry("class:warn", "hint me", "hint", job))
    app.sel = 0
    app.hint_buffer.text = "actually by X"
    app._accept_hint(app.hint_buffer)
    assert app.pool.calls[-1][0] == "_rehint"
    assert isinstance(app.pool.calls[-1][1][3], str)
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
    assert app.dir_buffer.text == ""  # "~/" is a fixed prefix
    assert app.input_buffer.text == ""
    assert app._dir_prefix() == [("class:accent", "Output folder ~/ ")]


def test_out_picker_existing_path_sets_dir_and_closes(app):
    app.commands.cmd_out("")
    app.dir_buffer.text = ""  # empty relative path → ~/ itself, which exists
    app._accept(app.dir_buffer)
    assert app.dir_picker is None
    assert app.settings.out_dir == Path.home()


def test_out_picker_missing_path_prompts_to_create(app):
    app.commands.cmd_out("")
    app.dir_buffer.text = "no-such-folder-xyz/deep"
    app._accept(app.dir_buffer)
    picker = app.dir_picker
    assert picker is not None and picker.stage == "confirm"
    assert picker.typed == "no-such-folder-xyz/deep"
    assert picker.pending == Path.home() / "no-such-folder-xyz/deep"
    assert app.dir_buffer.text == ""  # cleared while asking y/n
    assert "(y/n)" in app._dir_placeholder()


def test_out_picker_decline_returns_to_path_input(app):
    app.commands.cmd_out("")
    app.dir_buffer.text = "no-such-folder-xyz"
    app._accept(app.dir_buffer)
    app._dir_decline_create()  # 'n'
    assert app.dir_picker.stage == "input"
    assert app.dir_buffer.text == "no-such-folder-xyz"  # restored for editing
    assert app.dir_picker.pending is None


def test_out_picker_confirm_creates_folder_and_sets_dir(app, tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    app.commands.cmd_out("")
    app.dir_buffer.text = "fresh/nested"
    app._accept(app.dir_buffer)
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
    assert app.dir_picker is None and app.dir_buffer.text == ""
    assert app.settings.out_dir == before


def test_main_and_directory_drafts_survive_focus_switching(app):
    app.input_buffer.text = "https://youtu.be/draft"
    app.commands.cmd_out("")
    app.dir_buffer.text = "Music"

    app.toggle_dir_focus()
    assert app.app.layout.current_buffer is app.dir_buffer
    app.toggle_dir_focus()
    assert app.app.layout.current_buffer is app.input_buffer
    assert app.input_buffer.text == "https://youtu.be/draft"
    assert app.dir_buffer.text == "Music"


@pytest.mark.parametrize("folder_focused", [False, True])
def test_escape_closes_folder_before_help_and_preserves_main_draft(app, folder_focused):
    app.input_buffer.text = "https://youtu.be/draft"
    app.open_help()
    app.commands.cmd_out("")
    if folder_focused:
        app.toggle_dir_focus()
    escape = app.app.key_bindings.get_bindings_for_keys((Keys.Escape,))[-1]

    escape.handler(SimpleNamespace())

    assert app.dir_picker is None
    assert app.help_open
    assert app.input_buffer.text == "https://youtu.be/draft"

    escape.handler(SimpleNamespace())
    assert not app.help_open


def test_directory_edit_does_not_block_main_url_submission(app):
    submitted = []
    original_settings = app.settings_snapshot()
    app.submit_urls = lambda urls: submitted.append((urls, app.settings_snapshot()))
    app.input_buffer.text = "https://youtu.be/live"
    app.commands.cmd_out("")
    app.dir_buffer.text = "unapplied"

    app._accept(app.input_buffer)

    assert submitted == [(["https://youtu.be/live"], original_settings)]
    assert app.dir_picker is not None
    assert app.dir_buffer.text == "unapplied"


def test_folder_confirmation_bindings_require_directory_focus(app, tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    app.commands.cmd_out("")
    app.dir_buffer.text = "fresh"
    app._accept(app.dir_buffer)
    y_binding = app.app.key_bindings.get_bindings_for_keys(("y",))[-1]
    n_binding = app.app.key_bindings.get_bindings_for_keys(("n",))[-1]

    app.app.layout.focus(app.input_buffer)
    with set_app(app.app):
        assert not y_binding.filter()
        assert not n_binding.filter()

    app.app.layout.focus(app.dir_buffer)
    with set_app(app.app):
        assert y_binding.filter()
        assert n_binding.filter()


def test_status_line_shows_labeled_settings_after_hint(app, tmp_path):
    app.set_out_dir(tmp_path / "Music")
    app.set_quality("192")

    assert app._status_line() == [*app._input_hint(), ("", "  "), *app._toolbar()]
    toolbar_text = "".join(chunk for _, chunk in app._toolbar())
    assert f"out {tmp_path / 'Music'}" in toolbar_text
    assert "q 192k" in toolbar_text


def test_toolbar_highlight_does_not_pad_before_its_text(app):
    """The gap between the hint and the toolbar must be unstyled, so the
    toolbar background doesn't extend two blank cells before "out"."""
    highlighted = [text for style, text in app._status_line() if "toolbar" in style]

    assert highlighted == [app._toolbar()[0][1]]
    assert not highlighted[0].startswith("  ")


def test_settings_persist_across_app_instances(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    settings_file = tmp_path / "settings.json"
    out_dir = tmp_path / "Music"
    out_dir.mkdir()

    first = App(
        activity_store=ActivityStore(tmp_path / "activity.sqlite3"),
        history_file=tmp_path / "prompt-history-1",
        settings_store=SettingsStore(settings_file),
        pool=RecordingPool(),
    )
    first.set_out_dir(out_dir)
    first.set_quality("192")

    reopened = App(
        activity_store=ActivityStore(tmp_path / "activity.sqlite3"),
        history_file=tmp_path / "prompt-history-2",
        settings_store=SettingsStore(settings_file),
        pool=RecordingPool(),
    )
    assert reopened.settings.out_dir == out_dir
    assert reopened.settings.quality == "192"


def test_corrupt_settings_fall_back_to_defaults_with_warning(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    settings_file = tmp_path / "settings.json"
    settings_file.write_text("{ not json", encoding="utf-8")

    app = App(
        activity_store=ActivityStore(tmp_path / "activity.sqlite3"),
        history_file=tmp_path / "prompt-history",
        settings_store=SettingsStore(settings_file),
        pool=RecordingPool(),
    )

    assert app.settings == Settings()
    assert any("saved settings ignored" in line for _, line in app.messages)


def test_help_opens_takeover_with_full_command_reference(app):
    app.commands.cmd_help("")
    assert app.help_open is True
    body = "".join(text for _, text in app._render_help())
    # /out is the first row — the one the old message-log render clipped.
    for cmd in ("/out", "/quality", "/enrich", "/quit"):
        assert cmd in body
    assert "esc closes" in app._input_hint()[0][1]


def test_help_body_replaces_processing_and_history(app):
    app.add_entry(Entry("class:ok", "done", "ok", Job(url="u1")))
    app.open_help()
    above, below = app._build()
    text = "".join(t for _, t in above)
    assert "Commands" in text
    assert "  Processing\n" not in text  # normal body sections are gone
    assert "  History\n" not in text
    assert below == []


def test_help_escape_closes_and_preserves_live_input(app):
    app.input_buffer.text = "https://youtu.be/draft"
    app.open_help()
    assert app.help_open is True
    assert app.input_buffer.text == "https://youtu.be/draft"
    app.close_help()
    assert app.help_open is False
    assert app.input_buffer.text == "https://youtu.be/draft"


def test_help_accepts_live_urls_and_keeps_help_open(app):
    submitted = []
    app.submit_urls = lambda urls: submitted.append(urls)
    app.open_help()
    app.input_buffer.text = "https://youtu.be/live"

    app._accept(app.input_buffer)

    assert submitted == [["https://youtu.be/live"]]
    assert app.help_open
    app.close_help()  # esc
    assert app.help_open is False
    assert app.input_buffer.text == "https://youtu.be/live"


@pytest.mark.parametrize("rows, columns", [(24, 40), (24, 80), (35, 100)])
def test_help_and_both_inputs_fit_small_terminals(app, rows, columns):
    app.open_help()
    app.commands.cmd_out("")
    rendered_screens = []
    app.app.after_render += lambda _app: rendered_screens.append(
        _app.renderer.last_rendered_screen
    )

    async def render_once():
        class SizedDummyOutput(DummyOutput):
            def get_size(self):
                return Size(rows=rows, columns=columns)

        with create_pipe_input() as pipe:
            pipe.send_text("\x04")
            app.app.input = pipe
            output = SizedDummyOutput()
            app.app.output = output
            app.app.renderer.output = output
            await app.app.run_async()

    asyncio.run(render_once())
    screen = rendered_screens[0]
    rendered = "\n".join(
        "".join(cell.char for _, cell in sorted(row.items())).rstrip()
        for _, row in sorted(screen.data_buffer.items())
    )

    assert "Output folder ~/" in rendered
    assert "❯ Paste a YouTube or SoundCloud link" in rendered


def test_help_mouse_wheel_scrolls_text_and_padding_without_changing_drafts(app):
    app.input_buffer.text = "https://youtu.be/main-draft"
    app.commands.cmd_out("")
    app.dir_buffer.text = "folder-draft"
    app.open_help()
    class SizedDummyOutput(DummyOutput):
        def get_size(self):
            return Size(rows=24, columns=80)

    app.app.output = SizedDummyOutput()
    assert app.app.key_bindings.get_bindings_for_keys((Keys.PageUp,)) == []
    assert app.app.key_bindings.get_bindings_for_keys((Keys.PageDown,)) == []
    assert app.app.mouse_support()
    scroll_positions = []
    wheel_phase = 0

    from prompt_toolkit.data_structures import Point

    def wheel(event_type, x, y):
        event = MouseEvent(
            position=Point(x=x, y=y),
            event_type=event_type,
            button=MouseButton.NONE,
            modifiers=frozenset(),
        )
        return app.app.renderer.mouse_handlers.mouse_handlers[y][x](event)

    def after_render(_app):
        nonlocal wheel_phase
        if wheel_phase == 0:
            wheel_phase = 1
            wheel(MouseEventType.SCROLL_DOWN, 74, 6)
            scroll_positions.append(app.help_pane.vertical_scroll)
            wheel(MouseEventType.SCROLL_DOWN, 74, 18)
            for _ in range(10):
                wheel(MouseEventType.SCROLL_DOWN, 74, 6)
            scroll_positions.append(app.help_pane.vertical_scroll)
        elif wheel_phase == 1:
            wheel_phase = 2
            _app.renderer.render(_app, _app.layout)
            bottom_screen = _app.renderer._last_screen
            bottom = "\n".join(
                "".join(cell.char for _, cell in sorted(row.items())).rstrip()
                for _, row in sorted(bottom_screen.data_buffer.items())
            )
            assert "/quit" in bottom
            for _ in range(12):
                wheel(MouseEventType.SCROLL_UP, 74, 6)
            _app.renderer.render(_app, _app.layout)

    app.app.after_render += after_render

    async def render_once():
        with create_pipe_input() as pipe:
            pipe.send_text("\x04")
            app.app.input = pipe
            await app.app.run_async()

    asyncio.run(render_once())
    assert scroll_positions[0] == 3
    assert scroll_positions[1] > 3
    assert app.help_pane.vertical_scroll == 0
    assert app.app.layout.current_buffer is app.input_buffer
    assert app.input_buffer.text == "https://youtu.be/main-draft"
    assert app.dir_buffer.text == "folder-draft"


def test_unknown_command_reports_error(app):
    app.commands.dispatch("/nope")
    assert any("unknown command" in line for _, line in app.messages)


def _sync_item(item_id, direction="to_phone", confidence="high", name=None):
    track = SimpleNamespace(relative_path=name or f"Artist/{item_id}.mp3")
    return SimpleNamespace(
        id=item_id,
        direction=direction,
        confidence=confidence,
        reason="close title match" if confidence == "low" else "",
        track=track,
        target_name=f"{item_id}.mp3",
    )


def _sync_plan(*items):
    return SimpleNamespace(device_name="Test iPhone", out_dir=Path("/music"), items=items)


def test_sync_preview_is_read_only_until_continue_and_matched_rows_are_dim(app):
    from transmute.sync import SyncResult

    class Service:
        def __init__(self):
            self.transfers = []

        def preview(self, out_dir, **kwargs):
            return _sync_plan(
                _sync_item("same", "matched"), _sync_item("new"),
                _sync_item("maybe", confidence="low"),
            )

        def transfer(self, plan, selected_ids, **kwargs):
            self.transfers.append(selected_ids)
            return (SyncResult("new", "done"), SyncResult("maybe", "done"))

    service = Service()
    app.sync_service = service
    app.start_sync_preview()
    assert app.pool.calls[-1][0] == "_sync_preview_worker"
    _, args = app.pool.calls.pop()
    app._sync_preview_worker(*args)

    assert app.sync_selected == set()
    assert service.transfers == []
    fragments = app._render_sync()
    text = "".join(value for _, value in fragments)
    assert "Already synced" in text and "* Possible duplicate" in text
    assert "Based on close title match" in text
    assert any(style == "class:dim" and "Already synced" in value for style, value in fragments)

    app.sync_select(False)
    app.sync_cursor = 3
    app.sync_toggle()
    assert service.transfers == []
    worker, args = app.pool.calls.pop()
    assert worker == "_sync_transfer_worker"
    app._sync_transfer_worker(*args)
    assert service.transfers == [{"new", "maybe"}]
    assert app.sync_selected == set()


def test_sync_high_and_all_selection_and_focus_preserve_input_draft(app):
    app.sync_plan = _sync_plan(
        _sync_item("high"), _sync_item("low", confidence="low"),
        _sync_item("matched", "matched"),
    )
    app.input_buffer.text = "https://youtu.be/draft"
    app.sync_open = True
    app.sync_focus = True
    app.app.layout.focus(app.sync_control)
    app.sync_select(True)
    assert app.sync_selected == {"high"}
    app.sync_select(False)
    assert app.sync_selected == {"high", "low"}
    app.toggle_sync_focus()
    assert app.app.layout.current_buffer is app.input_buffer
    assert app.input_buffer.text == "https://youtu.be/draft"


def test_sync_cancel_waits_for_worker_and_retry_keeps_successes(app):
    from transmute.sync import SyncResult

    app.sync_plan = _sync_plan(_sync_item("done"), _sync_item("failed"))
    app.sync_results = {
        "done": SyncResult("done", "done"),
        "failed": SyncResult("failed", "error", "phone disconnected"),
    }
    app.retry_sync()
    assert app.sync_selected == {"failed"}
    assert app.sync_open and app.sync_focus
    app.sync_busy = True
    app.sync_transfer_active = True
    app.sync_back()
    assert app.sync_open and app.sync_cancel_requested
    assert "stops after this file" in "".join(text for _, text in app._render_sync())


def test_sync_command_music_is_truthful_and_help_documents_shortcuts(app):
    app.commands.cmd_sync("music")
    note = app.messages[-1][1]
    assert "Finder" in note and "unsupported" in note
    app.commands.cmd_help("")
    body = "".join(text for _, text in app._render_help())
    assert "/sync" in body and "h high confidence" in body


def test_sync_shortcuts_are_scoped_to_report_focus(app):
    app.sync_open = True
    app.sync_focus = False
    app.app.layout.focus(app.input_buffer)
    select_binding = app.app.key_bindings.get_bindings_for_keys(("h",))[-1]
    select_all_binding = app.app.key_bindings.get_bindings_for_keys(("s",))[-1]
    enter_bindings = app.app.key_bindings.get_bindings_for_keys((Keys.Enter,))
    with set_app(app.app):
        assert not select_binding.filter()
        assert not select_all_binding.filter()
        assert not any(binding.filter() for binding in enter_bindings)
    app.sync_focus = True
    app.app.layout.focus(app.sync_control)
    with set_app(app.app):
        assert select_binding.filter()
        assert select_all_binding.filter()
        assert any(binding.filter() for binding in enter_bindings)


@pytest.mark.parametrize("rows, columns", [(24, 40), (24, 80), (35, 100)])
def test_sync_report_renders_at_small_terminal_sizes(app, rows, columns):
    app.sync_plan = _sync_plan(_sync_item("one"), _sync_item("two", confidence="low"))
    app.sync_open = True
    app.sync_focus = True
    app.app.layout.focus(app.sync_control)
    screens = []
    app.app.after_render += lambda _app: screens.append(_app.renderer._last_screen)

    async def render_once():
        class SizedDummyOutput(DummyOutput):
            def get_size(self):
                return Size(rows=rows, columns=columns)

        with create_pipe_input() as pipe:
            pipe.send_text("\x04")
            app.app.input = pipe
            output = SizedDummyOutput()
            app.app.output = output
            app.app.renderer.output = output
            await app.app.run_async()

    asyncio.run(render_once())
    rendered = "\n".join(
        "".join(cell.char for _, cell in sorted(row.items())).rstrip()
        for _, row in sorted(screens[0].data_buffer.items())
    )
    assert "Preview" in rendered
    assert "Connected Test iPhone" in rendered
    assert "Continue" in rendered
    assert "two.mp3" in rendered


def test_sync_excludes_only_queued_and_active_paths(app, tmp_path):
    completed = Job(url="done", status="done", path=tmp_path / "done.mp3")
    failed = Job(url="failed", status="error", path=tmp_path / "failed.mp3")
    queued = Job(url="queued", path=tmp_path / "queued.mp3")
    active_path = tmp_path / "active.mp3"
    app.completed.append(completed)
    app.failed.append(failed)
    app._queued_jobs.append(queued)
    app._active_paths[1] = active_path

    assert set(app._sync_excluded_paths()) == {queued.path, active_path}


def test_sync_preview_filesystem_error_ends_busy_state(app):
    class Service:
        def preview(self, *_args, **_kwargs):
            raise OSError("folder disappeared")

    app.sync_service = Service()
    app.start_sync_preview()
    _, args = app.pool.calls.pop()
    app._sync_preview_worker(*args)

    assert not app.sync_busy
    text = "".join(value for _, value in app._render_sync())
    assert "Sync unavailable" in text
    assert "Scanning output folder" not in text


def test_sync_error_reason_is_visible_and_retry_notice_does_not_deadlock(app):
    from transmute.sync import SyncResult

    app.sync_plan = _sync_plan(_sync_item("bad"))
    app.sync_results["bad"] = SyncResult("bad", "error", "phone storage full")
    rendered = "".join(value for _, value in app._render_sync())
    assert "phone storage full" in rendered

    app.sync_plan = None
    app.retry_sync()
    assert "no Spotify sync preview" in app.messages[-1][1]


def test_pipe_input_keeps_draft_and_scopes_select_all_to_sync_report(app):
    app.sync_plan = _sync_plan(_sync_item("one"), _sync_item("maybe", confidence="low"))
    app.sync_open = True
    app.input_buffer.text = "draft"

    async def run_keys():
        with create_pipe_input() as pipe:
            pipe.send_text("h\th s\r\x04")
            app.app.input = pipe
            app.app.output = DummyOutput()
            await app.app.run_async()

    asyncio.run(run_keys())

    assert app.sync_selected == {"maybe"}
    assert app.input_buffer.text == "hdraft"
    assert not app.sync_busy


def test_sync_close_command_closes_an_unavailable_phone_preview(app):
    app.sync_open = True
    app.sync_busy = True
    app.sync_focus = True
    app.app.layout.focus(app.sync_control)

    app.commands.cmd_sync("close")

    assert not app.sync_open
    assert app.sync_busy  # Closing the report does not pretend the scan finished.
    assert app.app.layout.current_buffer is app.input_buffer


def test_q_closes_sync_report_without_exiting_until_explicit_exit(app):
    app.sync_open = True
    app.sync_busy = True
    app.sync_focus = True
    app.app.layout.focus(app.sync_control)

    async def run_keys():
        with create_pipe_input() as pipe:
            pipe.send_text("q\x04")
            app.app.input = pipe
            app.app.output = DummyOutput()
            await app.app.run_async()

    asyncio.run(run_keys())

    assert not app.sync_open
    assert app.sync_busy


def test_sync_scroll_keeps_continue_visible_after_navigating_long_report(app):
    app.sync_plan = _sync_plan(*(_sync_item(f"track-{index}") for index in range(50)))
    app.sync_open = True
    app.sync_focus = True
    app.sync_selected = {item.id for item in app.sync_plan.items}
    app.app.layout.focus(app.sync_control)

    async def navigate():
        class SizedDummyOutput(DummyOutput):
            def get_size(self):
                return Size(rows=24, columns=80)

        with create_pipe_input() as pipe:
            pipe.send_text("\x1b[B" * 50 + "\x04")
            app.app.input = pipe
            output = SizedDummyOutput()
            app.app.output = output
            app.app.renderer.output = output
            await app.app.run_async()

    asyncio.run(navigate())
    assert app.sync_cursor == 50
    assert app.sync_open
    assert app.sync_pane.vertical_scroll > 0
    app.app.renderer.render(app.app, app.app.layout)
    screen = app.app.renderer._last_screen
    rendered = "\n".join(
        "".join(cell.char for _, cell in sorted(row.items())).rstrip()
        for _, row in sorted(screen.data_buffer.items())
    )
    assert len(screen.data_buffer) <= 24
    assert "track-49.mp3" in rendered
    assert "Continue" in rendered


def test_help_and_directory_picker_replace_sync_panel_without_losing_draft(app):
    app.input_buffer.text = "saved URL draft"
    app.sync_open = True
    app.sync_focus = True

    app.open_help()
    assert app.help_open and not app.sync_open
    assert app.input_buffer.text == "saved URL draft"

    app.close_help()
    app.sync_open = True
    app.open_dir_picker()
    assert app.dir_picker is not None and not app.sync_open
    assert app.input_buffer.text == "saved URL draft"


def test_sync_preview_replaces_help_and_directory_picker_without_losing_draft(app):
    from transmute.sync import SyncResult

    app.input_buffer.text = "saved URL draft"
    app.open_help()
    app.commands.cmd_sync("")
    assert app.sync_open and not app.help_open and app.dir_picker is None
    assert app.input_buffer.text == "saved URL draft"

    app.sync_busy = False
    app.sync_plan = _sync_plan(_sync_item("retry"))
    app.sync_results = {"retry": SyncResult("retry", "error", "try again")}
    app.open_help()
    app.retry_sync()
    assert app.sync_open and not app.help_open and app.dir_picker is None
    assert app.input_buffer.text == "saved URL draft"

    app.sync_busy = False
    app.sync_open = False
    app.commands.cmd_out("")
    assert app.dir_picker is not None
    app.commands.cmd_sync("")
    assert app.sync_open and not app.help_open and app.dir_picker is None
    assert app.input_buffer.text == "saved URL draft"


def test_enrich_selects_provider_and_toggles(app):
    app.commands.cmd_enrich("codex")
    assert app.enricher.backend == "codex"
    assert app.enricher.enabled
    assert "Codex (ChatGPT subscription)" in app.messages[-1][1]

    app.commands.cmd_enrich("off")
    assert not app.enricher.enabled
    app.commands.cmd_enrich("on")
    assert app.enricher.enabled


def test_enrich_rejects_unknown_provider(app):
    before = (app.enricher.backend, app.enricher.enabled)
    app.commands.cmd_enrich("other")
    assert (app.enricher.backend, app.enricher.enabled) == before
    assert "must be codex, claude, api, on, or off" in app.messages[-1][1]


def test_key_auto_detects_provider_and_never_echoes_secret(app):
    anthropic_key = "sk-ant-api03-secret"
    openai_key = "sk-proj-secret"

    app.commands._apply_key(anthropic_key)
    assert app.enricher.backend == "anthropic_api"
    app.commands._apply_key(openai_key)
    assert app.enricher.backend == "openai_api"
    assert app.enricher.api_key_source == "entered"
    assert all(
        anthropic_key not in line and openai_key not in line
        for _, line in app.messages
    )


def test_key_argument_is_rejected_to_keep_secret_out_of_command_history(app):
    history = InMemoryHistory()
    app.input_buffer.history = history
    app.input_buffer.text = "/key sk-proj-should-not-be-used"
    app.input_buffer.validate_and_handle()

    assert not app.enricher.has_api_key
    assert "use the masked prompt" in app.messages[-1][1]
    assert "sk-proj-should-not-be-used" not in app.messages[-1][1]
    assert history.get_strings() == ["/key"]


def test_enrich_api_requires_then_uses_active_key(app):
    app.enricher.clear_api_key()
    app.commands.cmd_enrich("api")
    assert "no API key configured" in app.messages[-1][1]

    app.commands._apply_key("sk-proj-test")
    app.commands.cmd_enrich("codex")
    app.commands.cmd_enrich("api")
    assert app.enricher.backend == "openai_api"


def test_login_defaults_to_claude_without_replacing_api_key(app, monkeypatch):
    async def run_now(blocking):
        return blocking()

    login = Mock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr("prompt_toolkit.application.run_in_terminal", run_now)
    monkeypatch.setattr("transmute.commands.subprocess.run", login)
    monkeypatch.setattr(
        app.app,
        "create_background_task",
        lambda coroutine: asyncio.run(coroutine),
    )
    app.enricher.set_api_key("sk-proj-test")

    app.commands.cmd_login("")

    login.assert_called_once_with(
        ["claude", "auth", "login", "--claudeai"],
        timeout=300,
        check=False,
    )
    assert app.enricher.backend == "openai_api"
    assert "remains prioritized" in app.messages[-1][1]


def test_login_timeout_is_actionable(app, monkeypatch):
    async def run_now(blocking):
        return blocking()

    monkeypatch.setattr("prompt_toolkit.application.run_in_terminal", run_now)
    monkeypatch.setattr(
        "transmute.commands.subprocess.run",
        Mock(side_effect=TimeoutExpired(cmd=["claude"], timeout=300)),
    )
    monkeypatch.setattr(
        app.app,
        "create_background_task",
        lambda coroutine: asyncio.run(coroutine),
    )

    app.commands.cmd_login("claude")

    assert app.messages[-1] == ("class:err", "claude login timed out")


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


def test_ctrl_c_warns_then_exits(app):
    event = SimpleNamespace(app=SimpleNamespace(exit=Mock()))
    binding = app.app.key_bindings.get_bindings_for_keys((Keys.ControlC,))[-1]

    binding.handler(event)
    assert "press CTRL+C again to exit" in app._input_hint()[0][1]
    event.app.exit.assert_not_called()

    binding.handler(event)
    event.app.exit.assert_called_once()


def test_url_paste_submits(app):
    submitted = []
    app.submit_urls = lambda urls: submitted.append(urls)
    app.input_buffer.text = "https://youtu.be/1https://soundcloud.com/a/2"
    app._accept(app.input_buffer)
    assert submitted == [["https://youtu.be/1", "https://soundcloud.com/a/2"]]


def test_unsupported_url_denied(app):
    submitted = []
    app.submit_urls = lambda urls: submitted.append(urls)
    app.input_buffer.text = "https://vimeo.com/123"
    app._accept(app.input_buffer)
    assert submitted == []


def test_submit_uses_immutable_settings_snapshot(app):
    original = app.settings_snapshot()

    app.submit_urls(["https://example.com/song"])
    app.set_quality("128")

    name, args = app.pool.calls[-1]
    assert name == "_process"
    assert isinstance(args[0], Job)
    assert args[0].url == "https://example.com/song"
    assert args[1] == original
    assert args[1].quality == "320"
    assert app.settings_snapshot().quality == "128"


def test_unexpected_download_error_becomes_failed_job(app, monkeypatch):
    def fail_download(_job, _settings, _on_progress):
        raise OSError("disk unavailable\nextra details")

    monkeypatch.setattr("transmute.app.download_job", fail_download)
    job = Job(url="https://example.com/song")
    app.activity_store.queue_job(job, app.session_id)
    app.queued = 1

    app._process(job, Settings())

    assert app.queued == 0
    assert app.active == {}
    assert app.completed == []
    assert len(app.failed) == 1
    assert app.failed[0].status == "error"
    assert app.failed[0].error == "disk unavailable"
    assert app.history[-1].kind == "err"


def test_tagging_error_keeps_successful_download(app, monkeypatch, tmp_path):
    output = tmp_path / "Source - Song.mp3"
    output.touch()

    def complete_download(job, _settings, _on_progress):
        job.status = "done"
        job.title = "Song"
        job.path = output
        return job

    def fail_tagging(_path, _tags):
        raise OSError("cannot write tags")

    monkeypatch.setattr("transmute.app.download_job", complete_download)
    monkeypatch.setattr("transmute.app.apply_tags", fail_tagging)
    monkeypatch.setattr(
        app.enricher,
        "lookup",
        lambda **_kwargs: TrackTags(
            artist="Artist",
            title="Song",
            kind="original",
            confidence="high",
        ),
    )
    app.enricher.enabled = True
    job = Job(url="https://example.com/song")
    app.activity_store.queue_job(job, app.session_id)
    app.queued = 1

    app._process(job, Settings(out_dir=tmp_path))

    assert app.active == {}
    assert app.failed == []
    assert len(app.completed) == 1
    assert app.completed[0].path == output
    assert app.history[-1].kind == "ok"
    assert any("tagging skipped" in line for _, line in app.messages)


def test_sync_exact_matches_have_no_checkbox_and_cannot_be_selected(app):
    app.sync_plan = _sync_plan(_sync_item("new"), _sync_item("matched", "matched"))
    app.sync_cursor = 1

    app.sync_toggle()
    assert app.sync_selected == set()
    assert "[ ] Already synced" not in "".join(value for _, value in app._render_sync())
    app.sync_select(True)
    assert app.sync_selected == {"new"}
    app.sync_select(False)
    assert app.sync_selected == {"new"}
    app.sync_toggle()
    assert app.sync_selected == {"new"}
