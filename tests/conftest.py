import pytest


@pytest.fixture(autouse=True)
def stub_subscription_probes(monkeypatch):
    from transmute import enrich

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(enrich, "_probe_claude", lambda: "missing")
    monkeypatch.setattr(enrich, "_probe_codex", lambda: "missing")
