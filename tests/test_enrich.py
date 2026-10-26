import json
from pathlib import Path
from subprocess import CompletedProcess
from threading import Barrier, Thread
from types import SimpleNamespace
from unittest.mock import Mock, patch

from transmute.enrich import (
    ANTHROPIC_MODEL,
    OPENAI_MODEL,
    OUTPUT_SCHEMA,
    Enricher,
    TrackTags,
    _safe_filename,
    apply_tags,
)
from transmute.enrich import _probe_claude as real_probe_claude
from transmute.enrich import _probe_codex as real_probe_codex


def test_claude_probe_parses_status_and_handles_bad_json(monkeypatch):
    monkeypatch.setattr("transmute.enrich.shutil.which", lambda _name: "/bin/claude")
    monkeypatch.setattr(
        "transmute.enrich.subprocess.run",
        lambda *args, **kwargs: CompletedProcess(args[0], 0, '{"loggedIn": true}', ""),
    )
    assert real_probe_claude() == "signed_in"
    monkeypatch.setattr(
        "transmute.enrich.subprocess.run",
        lambda *args, **kwargs: CompletedProcess(args[0], 1, '{"loggedIn": false}', ""),
    )
    assert real_probe_claude() == "signed_out"
    monkeypatch.setattr(
        "transmute.enrich.subprocess.run",
        lambda *args, **kwargs: CompletedProcess(args[0], 1, "private@example.org", ""),
    )
    assert real_probe_claude() == "unknown"


def test_cli_probes_missing_timeout_codex_status_and_security_args(monkeypatch):
    import tempfile
    from subprocess import TimeoutExpired

    monkeypatch.setattr("transmute.enrich.shutil.which", lambda _name: None)
    assert real_probe_claude() == real_probe_codex() == "missing"
    monkeypatch.setattr("transmute.enrich.shutil.which", lambda _name: "/bin/cli")
    monkeypatch.setattr(
        "transmute.enrich.subprocess.run",
        Mock(side_effect=TimeoutExpired("claude", 10)),
    )
    assert real_probe_claude() == "unknown"
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return CompletedProcess(args, 1, "Not logged in", "WARNING: proceeding")

    monkeypatch.setattr("transmute.enrich.subprocess.run", run)
    monkeypatch.setenv("CLAUDECODE", "parent")
    monkeypatch.setenv("CODEX_SESSION", "parent")
    monkeypatch.setenv("CODEX_HOME", "/tmp/codex-home")
    assert real_probe_codex() == "signed_out"
    args, kwargs = calls[0]
    assert args == ["codex", "login", "status"]
    assert kwargs["timeout"] == 10 and kwargs["check"] is False
    assert kwargs["cwd"] == tempfile.gettempdir()
    assert "CODEX_SESSION" not in kwargs["env"]
    assert kwargs["env"]["CODEX_HOME"] == "/tmp/codex-home"

    def claude_run(args, **kwargs):
        calls.append((args, kwargs))
        return CompletedProcess(args, 0, '{"loggedIn": true}', "")

    monkeypatch.setattr("transmute.enrich.subprocess.run", claude_run)
    monkeypatch.setenv("CLAUDECODE", "parent")
    assert real_probe_claude() == "signed_in"
    args, kwargs = calls[-1]
    assert args == ["claude", "auth", "status", "--json"]
    assert kwargs["timeout"] == 10 and kwargs["check"] is False
    assert "CLAUDECODE" not in kwargs["env"]

    monkeypatch.setattr(
        "transmute.enrich.subprocess.run",
        lambda args, **kwargs: CompletedProcess(args, 0, "", ""),
    )
    assert real_probe_codex() == "signed_in"
    monkeypatch.setattr(
        "transmute.enrich.subprocess.run",
        lambda args, **kwargs: CompletedProcess(args, 1, "Temporary outage", ""),
    )
    assert real_probe_codex() == "unknown"


def test_selection_prefers_signed_in_codex_and_unknown_fallback(monkeypatch):
    monkeypatch.setattr("transmute.enrich._probe_claude", lambda: "signed_out")
    monkeypatch.setattr("transmute.enrich._probe_codex", lambda: "signed_in")
    enricher = Enricher()
    assert enricher.backend == "codex"
    assert enricher.cli_states == {"claude": "signed_out", "codex": "signed_in"}
    monkeypatch.setattr("transmute.enrich._probe_claude", lambda: "unknown")
    monkeypatch.setattr("transmute.enrich._probe_codex", lambda: "signed_out")
    assert Enricher().backend == "claude"


def test_subscription_probe_is_lazy_and_refresh_obeys_opt_out_and_keys(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    codex = Mock(return_value="signed_in")
    monkeypatch.setattr("transmute.enrich._probe_claude", lambda: "signed_in")
    monkeypatch.setattr("transmute.enrich._probe_codex", codex)
    enricher = Enricher()
    codex.assert_not_called()
    assert enricher.backend == "claude"

    monkeypatch.setattr("transmute.enrich._probe_claude", lambda: "missing")
    monkeypatch.setattr("transmute.enrich._probe_codex", lambda: "missing")
    enricher = Enricher()
    monkeypatch.setattr("transmute.enrich._probe_codex", lambda: "signed_in")
    assert enricher.refresh_subscription() == "codex"
    assert enricher.backend == "codex"

    none = Enricher()
    none.set_api_key("sk-proj-test")
    assert none.refresh_subscription() is None
    none = Enricher()
    none.use_backend("codex")
    assert none.refresh_subscription() is None
    none = Enricher()
    none.set_enrichment_enabled(False)
    assert none.refresh_subscription() is None


def test_refresh_keeps_rechecking_when_status_is_unknown(monkeypatch):
    enricher = Enricher()
    monkeypatch.setattr("transmute.enrich._probe_claude", lambda: "unknown")

    assert enricher.refresh_subscription() is None
    assert enricher.backend == "none"
    assert enricher.needs_subscription_recheck


def test_safe_filename_strips_reserved_chars():
    assert _safe_filename('a/b\\c:d*e?f"g<h>i|j') == "a_b_c_d_e_f_g_h_i_j"


def test_safe_filename_plain():
    assert _safe_filename("Artist - Title") == "Artist - Title"


def test_tracktags_defaults():
    t = TrackTags(artist="X", title="Y")
    assert t.album is None and t.confidence is None and t.kind is None


def test_apply_tags_preserves_unowned_fields_and_disambiguates_collision(
    monkeypatch, tmp_path
):
    source = tmp_path / "Source - Upload.mp3"
    target = tmp_path / "Artist - Song.mp3"
    source.touch()
    target.touch()

    class FakeAudio(dict):
        def save(self):
            self.saved = True

    audio = FakeAudio(composer=["Existing Composer"])
    monkeypatch.setattr("mutagen.easyid3.EasyID3", lambda _path: audio)

    result = apply_tags(
        source,
        TrackTags(
            artist="Artist",
            title="Song",
            album="Album",
            year="2026",
            genre="Electronic",
        ),
    )

    # The clean target already exists, so we neither overwrite it nor keep the
    # raw `uploader - title` upload name; we disambiguate with a numeric suffix.
    assert result == tmp_path / "Artist - Song (1).mp3"
    assert result.exists()
    assert not source.exists()
    assert target.exists()
    assert audio["composer"] == ["Existing Composer"]
    assert audio["artist"] == "Artist"
    assert audio["title"] == "Song"
    assert audio.saved is True


def test_apply_tags_collision_drops_uploader_prefixed_name(monkeypatch, tmp_path):
    # Regression: an upload of "2hollis - bride" by uploader "3enialis" lands on
    # disk as "3enialis - 2hollis - bride.mp3" (yt-dlp's uploader - title). When
    # the user already has the canonical "2hollis - bride.mp3", the file must not
    # keep the misleading two-artist name.
    source = tmp_path / "3enialis - 2hollis - bride.mp3"
    canonical = tmp_path / "2hollis - bride.mp3"
    source.touch()
    canonical.touch()

    class FakeAudio(dict):
        def save(self):
            pass

    monkeypatch.setattr("mutagen.easyid3.EasyID3", lambda _path: FakeAudio())

    result = apply_tags(source, TrackTags(artist="2hollis", title="bride"))

    assert result == tmp_path / "2hollis - bride (1).mp3"
    assert result.exists()
    assert not source.exists()
    assert canonical.exists()


def test_apply_tags_renames_to_sanitized_metadata_name(monkeypatch, tmp_path):
    source = tmp_path / "Source - Upload.mp3"
    source.touch()

    class FakeAudio(dict):
        def save(self):
            pass

    monkeypatch.setattr("mutagen.easyid3.EasyID3", lambda _path: FakeAudio())

    result = apply_tags(
        source,
        TrackTags(artist="Artist/Name", title="Song: Edit"),
    )

    assert result == tmp_path / "Artist_Name - Song_ Edit.mp3"
    assert result.exists()
    assert not source.exists()


def test_backend_selection_prefers_openai_then_anthropic_env_keys(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr("transmute.enrich.shutil.which", lambda _name: "/bin/tool")

    enricher = Enricher()
    assert enricher.backend == "openai_api"
    assert enricher.api_key_source == "environment"

    monkeypatch.delenv("OPENAI_API_KEY")
    enricher = Enricher()
    assert enricher.backend == "anthropic_api"
    assert enricher.api_key_source == "environment"


def test_backend_selection_prefers_claude_then_codex(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("transmute.enrich._probe_claude", lambda: "signed_in")
    monkeypatch.setattr("transmute.enrich._probe_codex", lambda: "signed_in")
    assert Enricher().backend == "claude"

    monkeypatch.setattr("transmute.enrich._probe_claude", lambda: "missing")
    assert Enricher().backend == "codex"


def test_entered_key_auto_detects_and_replaces_active_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    enricher = Enricher()

    enricher.set_api_key("sk-ant-api03-test")
    assert enricher.backend == "anthropic_api"
    assert enricher.api_key_source == "entered"

    enricher.set_api_key("sk-proj-test")
    assert enricher.backend == "openai_api"
    assert enricher.api_key_source == "entered"


def test_api_key_rejects_unknown_prefix_without_replacing_key():
    enricher = Enricher()
    enricher.set_api_key("sk-proj-existing")

    try:
        enricher.set_api_key("unknown-key")
    except ValueError as e:
        assert "sk- (OpenAI) or sk-ant- (Anthropic)" in str(e)
    else:
        raise AssertionError("unknown key prefix was accepted")

    assert enricher.backend == "openai_api"


def test_clearing_entered_key_restores_subscription_backend(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("transmute.enrich._probe_claude", lambda: "signed_in")
    enricher = Enricher()
    enricher.set_api_key("sk-ant-api03-test")

    enricher.clear_api_key()

    assert enricher.backend == "claude"
    assert not enricher.has_api_key


def test_openai_api_uses_web_search_and_strict_schema(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    payload = {
        "kind": "original",
        "based_on": None,
        "artist": "Artist",
        "title": "Song",
        "album": None,
        "album_artist": None,
        "year": "2026",
        "genre": "Electronic",
        "confidence": "high",
    }
    create = Mock(return_value=SimpleNamespace(output_text=json.dumps(payload)))
    client = SimpleNamespace(responses=SimpleNamespace(create=create))
    enricher = Enricher()
    enricher.set_api_key("sk-proj-test")
    monkeypatch.setattr(enricher, "_get_openai_client", lambda: client)

    tags = enricher.lookup(
        title="Song",
        uploader="Artist",
        duration=180,
        url="https://example.com/song",
    )

    assert tags.artist == "Artist"
    kwargs = create.call_args.kwargs
    assert kwargs["model"] == "gpt-6-luna" == OPENAI_MODEL
    assert kwargs["tools"] == [{"type": "web_search"}]
    assert kwargs["text"]["format"] == {
        "type": "json_schema",
        "name": "track_tags",
        "strict": True,
        "schema": OUTPUT_SCHEMA,
    }
    assert kwargs["store"] is False


@patch("transmute.enrich.subprocess.run")
def test_codex_lookup_is_isolated_and_schema_constrained(run, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("CODEX_THREAD_ID", "parent-thread")
    monkeypatch.setenv("CODEX_HOME", "/tmp/test-codex-home")
    payload = {
        "kind": "original",
        "based_on": None,
        "artist": "Artist",
        "title": "Song",
        "album": None,
        "album_artist": None,
        "year": "2026",
        "genre": "Electronic",
        "confidence": "high",
    }

    def completed(args, **kwargs):
        assert args[:7] == [
            "codex",
            "--search",
            "--ask-for-approval",
            "never",
            "--disable",
            "shell_tool",
            "exec",
        ]
        assert args[args.index("--model") + 1] == "gpt-6-luna"
        assert "--ephemeral" in args
        assert "--ignore-user-config" in args
        assert "--ignore-rules" in args
        assert ["--sandbox", "read-only"] == args[
            args.index("--sandbox") : args.index("--sandbox") + 2
        ]
        schema_path = Path(args[args.index("--output-schema") + 1])
        assert json.loads(schema_path.read_text()) == OUTPUT_SCHEMA
        assert kwargs["cwd"] == str(schema_path.parent)
        assert "CODEX_THREAD_ID" not in kwargs["env"]
        assert kwargs["env"]["CODEX_HOME"] == "/tmp/test-codex-home"
        return CompletedProcess(args=args, returncode=0, stdout=json.dumps(payload), stderr="")

    run.side_effect = completed
    enricher = Enricher()
    enricher.use_backend("codex")

    tags = enricher.lookup(
        title="Song",
        uploader="Artist",
        duration=180,
        url="https://example.com/song",
    )

    assert tags == TrackTags(
        artist="Artist",
        title="Song",
        album=None,
        album_artist=None,
        year="2026",
        genre="Electronic",
        kind="original",
        based_on=None,
        confidence="high",
    )
    assert enricher.last_error is None


@patch("transmute.enrich.subprocess.run")
def test_codex_auth_error_disables_enrichment(run):
    run.return_value = CompletedProcess(
        args=["codex"],
        returncode=1,
        stdout="",
        stderr="Codex failed\nNot logged in; run codex login",
    )
    enricher = Enricher()
    enricher.use_backend("codex")

    assert enricher._ask_codex("prompt") is None
    assert not enricher.enabled
    assert "not logged in to Codex" in enricher.last_error


def test_codex_error_parser_prefers_actionable_line():
    output = (
        "error: unexpected argument '--old-flag'\n"
        "Usage: codex exec [OPTIONS]\n"
        "For more information, try '--help'."
    )

    assert Enricher._last_error_line(output) == (
        "error: unexpected argument '--old-flag'"
    )


@patch("transmute.enrich.subprocess.run")
def test_claude_error_surfaces_structured_result(run):
    run.return_value = CompletedProcess(
        args=["claude"],
        returncode=1,
        stdout=json.dumps(
            {
                "is_error": True,
                "terminal_reason": "api_error",
                "api_error_status": 429,
                "result": (
                    "You've hit your session limit · "
                    "resets 3:50am (America/New_York)"
                ),
            }
        ),
        stderr="",
    )
    enricher = Enricher()
    enricher.use_backend("claude")

    assert enricher._ask_claude("prompt") is None
    assert enricher.last_error == (
        "You've hit your session limit · resets 3:50am (America/New_York)"
    )
    assert enricher.enabled


def test_claude_cli_pins_model_and_preserves_isolation(monkeypatch):
    import tempfile

    monkeypatch.setenv("CLAUDECODE", "parent-session")
    run = Mock(
        return_value=CompletedProcess(
            args=["claude"],
            returncode=0,
            stdout=json.dumps({"result": "{}", "is_error": False}),
            stderr="",
        )
    )
    monkeypatch.setattr("transmute.enrich.subprocess.run", run)
    enricher = Enricher()
    enricher.use_backend("claude")

    assert enricher._ask_claude("prompt") == "{}"
    args = run.call_args.args[0]
    kwargs = run.call_args.kwargs
    assert args[args.index("--model") + 1] == "claude-haiku-5-5" == ANTHROPIC_MODEL
    assert args[args.index("--allowedTools") + 1 : args.index("--model")] == [
        "WebSearch",
        "WebFetch",
    ]
    assert kwargs["cwd"] == tempfile.gettempdir()
    assert kwargs["timeout"] == 300 and kwargs["check"] is False
    assert "CLAUDECODE" not in kwargs["env"]


@patch("transmute.enrich.subprocess.run")
def test_claude_auth_error_disables_enrichment(run):
    run.return_value = CompletedProcess(
        args=["claude"],
        returncode=1,
        stdout=json.dumps({"is_error": True, "result": "Not logged in"}),
        stderr="",
    )
    enricher = Enricher()
    enricher.use_backend("claude")

    assert enricher._ask_claude("prompt") is None
    assert not enricher.enabled
    assert "not logged in to Claude" in enricher.last_error


@patch("transmute.enrich.subprocess.run", side_effect=FileNotFoundError)
def test_missing_claude_cli_is_normalized(run):
    enricher = Enricher()
    enricher.use_backend("claude")

    assert enricher._ask_claude("prompt") is None
    assert not enricher.enabled
    assert "claude CLI not found" in enricher.last_error
    run.assert_called_once()


def test_lookup_rejects_incomplete_metadata(monkeypatch):
    enricher = Enricher()
    enricher.use_backend("codex")
    monkeypatch.setattr(
        enricher,
        "_ask_codex",
        lambda _prompt: json.dumps({"artist": "Artist", "title": "Song"}),
    )

    tags = enricher.lookup(
        title="Song",
        uploader="Artist",
        duration=180,
        url="https://example.com/song",
    )

    assert tags is None
    assert "metadata reply missing fields" in enricher.last_error


def test_lookup_prompt_asks_to_strip_release_status_markers(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    enricher = Enricher()
    enricher.use_backend("codex")
    prompts = []

    def ask(prompt):
        prompts.append(prompt)
        return None

    monkeypatch.setattr(enricher, "_ask_codex", ask)

    enricher.lookup(
        title="Artist - Song [UNRELEASED] (CDQ)",
        uploader="leaks",
        duration=180,
        url="https://example.com/song",
    )

    assert "- title: Artist - Song [UNRELEASED] (CDQ)" in prompts[0]
    assert "STEP 4 — Clean the names." in prompts[0]
    assert '"unreleased"' in prompts[0] and '"CDQ"' in prompts[0]


def test_lookup_rejects_unexpected_metadata_fields(monkeypatch):
    payload = {
        "kind": "original",
        "based_on": None,
        "artist": "Artist",
        "title": "Song",
        "album": None,
        "album_artist": None,
        "year": None,
        "genre": None,
        "confidence": "high",
        "explanation": "extra prose",
    }
    enricher = Enricher()
    enricher.use_backend("codex")
    monkeypatch.setattr(enricher, "_ask_codex", lambda _prompt: json.dumps(payload))

    tags = enricher.lookup(
        title="Song",
        uploader="Artist",
        duration=180,
        url="https://example.com/song",
    )

    assert tags is None
    assert "unexpected fields: explanation" in enricher.last_error


def test_lookup_normalizes_integer_year_and_embedded_json(monkeypatch):
    payload = {
        "kind": "original",
        "based_on": None,
        "artist": "Artist",
        "title": "Song",
        "album": None,
        "album_artist": None,
        "year": 2026,
        "genre": "Electronic",
        "confidence": "high",
    }
    enricher = Enricher()
    enricher.use_backend("codex")
    monkeypatch.setattr(
        enricher,
        "_ask_codex",
        lambda _prompt: f"result follows\n{json.dumps(payload)}\nfinished",
    )

    tags = enricher.lookup(
        title="Song",
        uploader="Artist",
        duration=180,
        url="https://example.com/song",
    )

    assert tags.year == "2026"
    assert enricher.last_error is None


def test_lookup_errors_are_thread_local():
    enricher = Enricher()
    barrier = Barrier(2)
    observed = {}

    def set_error(name):
        enricher.last_error = name
        barrier.wait()
        observed[name] = enricher.last_error

    first = Thread(target=set_error, args=("first",))
    second = Thread(target=set_error, args=("second",))
    first.start()
    second.start()
    first.join()
    second.join()

    assert observed == {"first": "first", "second": "second"}
    assert enricher.last_error is None


def test_provider_errors_redact_active_key():
    enricher = Enricher()
    key = "sk-proj-secret-value"
    enricher.set_api_key(key)

    message = enricher._provider_error(RuntimeError(f"request failed for {key}"))

    assert key not in message
    assert "[redacted]" in message


def test_provider_errors_redact_rotated_key_shape():
    enricher = Enricher()
    old_key = "sk-proj-old-secret-value"
    enricher.set_api_key("sk-proj-current-secret-value")

    message = enricher._provider_error(RuntimeError(f"request failed for {old_key}"))

    assert old_key not in message
    assert "[redacted]" in message


def test_anthropic_continuation_limit_is_bounded(monkeypatch):
    response = SimpleNamespace(stop_reason="pause_turn", content=[])
    create = Mock(return_value=response)
    client = SimpleNamespace(messages=SimpleNamespace(create=create))
    enricher = Enricher()
    enricher.set_api_key("sk-ant-api03-test")
    monkeypatch.setattr(enricher, "_get_anthropic_client", lambda: client)

    assert enricher._ask_anthropic_api("prompt") is None
    assert "continuation limit" in enricher.last_error
    assert create.call_count == 4
    assert all(
        call.kwargs["model"] == "claude-haiku-5-5" == ANTHROPIC_MODEL
        for call in create.call_args_list
    )
