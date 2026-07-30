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
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.keys import Keys

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
    assert app._input_hint() == [("class:input.hint", "  esc closes help")]


def test_help_body_replaces_processing_and_history(app):
    app.add_entry(Entry("class:ok", "done", "ok", Job(url="u1")))
    app.open_help()
    above, below = app._build()
    text = "".join(t for _, t in above)
    assert "Commands" in text
    assert "  Processing\n" not in text  # normal body sections are gone
    assert "  History\n" not in text
    assert below == []


def test_help_escape_closes_and_input_stays_inert(app):
    app.open_help()
    assert app.help_open is True
    app._accept(app.input_buffer)  # enter is a no-op while help is open
    app.close_help()  # esc
    assert app.help_open is False


def test_unknown_command_reports_error(app):
    app.commands.dispatch("/nope")
    assert any("unknown command" in line for _, line in app.messages)


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
