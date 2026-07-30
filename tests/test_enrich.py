import json
from pathlib import Path
from subprocess import CompletedProcess
from types import SimpleNamespace
from unittest.mock import Mock, patch

from transmute.enrich import (
    OPENAI_MODEL,
    OUTPUT_SCHEMA,
    Enricher,
    TrackTags,
    _safe_filename,
)


def test_safe_filename_strips_reserved_chars():
    assert _safe_filename('a/b\\c:d*e?f"g<h>i|j') == "a_b_c_d_e_f_g_h_i_j"


def test_safe_filename_plain():
    assert _safe_filename("Artist - Title") == "Artist - Title"


def test_tracktags_defaults():
    t = TrackTags(artist="X", title="Y")
    assert t.album is None and t.confidence is None and t.kind is None


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
    monkeypatch.setattr(
        "transmute.enrich.shutil.which",
        lambda name: f"/bin/{name}" if name in ("codex", "claude") else None,
    )
    assert Enricher().backend == "claude"

    monkeypatch.setattr(
        "transmute.enrich.shutil.which",
        lambda name: "/bin/codex" if name == "codex" else None,
    )
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
    monkeypatch.setattr(
        "transmute.enrich.shutil.which",
        lambda name: f"/bin/{name}" if name in ("codex", "claude") else None,
    )
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
    assert kwargs["model"] == OPENAI_MODEL
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

    assert enricher._ask_claude("prompt") is None
    assert enricher.last_error == (
        "You've hit your session limit · resets 3:50am (America/New_York)"
    )
    assert enricher.enabled


@patch("transmute.enrich.subprocess.run")
def test_claude_auth_error_disables_enrichment(run):
    run.return_value = CompletedProcess(
        args=["claude"],
        returncode=1,
        stdout=json.dumps({"is_error": True, "result": "Not logged in"}),
        stderr="",
    )
    enricher = Enricher()

    assert enricher._ask_claude("prompt") is None
    assert not enricher.enabled
    assert "not logged in to Claude" in enricher.last_error
