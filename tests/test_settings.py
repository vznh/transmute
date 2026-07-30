import json
import stat

import pytest

from transmute.config import SETTINGS_VERSION, Settings
from transmute.settings import SettingsStore, SettingsStoreError


@pytest.fixture
def store_path(tmp_path):
    return tmp_path / "state" / "settings.json"


@pytest.fixture
def store(store_path):
    return SettingsStore(store_path)


def test_missing_file_loads_defaults(store):
    loaded = store.load()
    assert loaded == Settings()
    assert not store.path.exists()  # loading never creates the file


def test_round_trip_preserves_out_dir_and_quality(store, tmp_path):
    saved = Settings(out_dir=tmp_path / "Music", quality="192")
    store.save(saved)
    assert store.load() == saved


def test_save_writes_versioned_json_atomically(store, tmp_path):
    store.save(Settings(out_dir=tmp_path / "Beats", quality="256"))

    data = json.loads(store.path.read_text(encoding="utf-8"))
    assert data == {
        "version": SETTINGS_VERSION,
        "out_dir": str(tmp_path / "Beats"),
        "quality": "256",
    }
    # No temporary artifacts remain beside the settings file.
    assert [p.name for p in store.path.parent.iterdir()] == [store.path.name]


def test_save_uses_owner_only_permissions(store):
    store.save(Settings())
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600


def test_unsupported_schema_version_is_rejected(store):
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(
        json.dumps({"version": SETTINGS_VERSION + 1, "quality": "320"}),
        encoding="utf-8",
    )
    with pytest.raises(SettingsStoreError):
        store.load()


def test_malformed_json_is_rejected(store):
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(SettingsStoreError):
        store.load()


def test_unknown_quality_is_rejected(store):
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(
        json.dumps({"version": SETTINGS_VERSION, "quality": "999"}),
        encoding="utf-8",
    )
    with pytest.raises(SettingsStoreError):
        store.load()


def test_missing_fields_fall_back_to_defaults(store):
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(
        json.dumps({"version": SETTINGS_VERSION}), encoding="utf-8"
    )
    assert store.load() == Settings()


def test_out_dir_expands_user_home(store, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(
        json.dumps({"version": SETTINGS_VERSION, "out_dir": "~/Songs"}),
        encoding="utf-8",
    )
    assert store.load().out_dir == tmp_path / "Songs"
