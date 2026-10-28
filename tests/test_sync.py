from __future__ import annotations

import hashlib
import io
import json
from contextlib import contextmanager
from pathlib import Path

import pytest

from transmute.sync import (
    SyncError,
    SyncService,
    TrackFile,
    _build_items,
    _safe_basename,
)


class FakePhone:
    def __init__(self, files: dict[str, bytes] | None = None) -> None:
        self.device_id = "device-1"
        self.device_name = "Test phone"
        self.files = dict(files or {})
        self.corrupt_write = False
        self.fail_read: set[str] = set()
        self.fail_write = False

    def list_files(self) -> list[str]:
        return list(self.files)

    def exists(self, relative_path: str) -> bool:
        return relative_path in self.files

    def read_to(self, relative_path: str, sink: io.BufferedIOBase) -> None:
        if relative_path in self.fail_read:
            raise OSError("disconnected")
        sink.write(self.files[relative_path])

    def write_from(self, relative_path: str, source: io.BufferedIOBase) -> None:
        if self.fail_write:
            raise OSError("storage full")
        data = source.read()
        self.files[relative_path] = data[:-1] if self.corrupt_write else data

    def publish(self, temporary_path: str, destination_path: str) -> None:
        if destination_path in self.files:
            raise FileExistsError(destination_path)
        self.files[destination_path] = self.files.pop(temporary_path)

    def remove(self, relative_path: str) -> None:
        self.files.pop(relative_path, None)


@pytest.fixture
def setup_sync(tmp_path: Path):
    out = tmp_path / "music"
    out.mkdir()
    phone = FakePhone()

    @contextmanager
    def factory(device_id: str | None = None):
        if device_id is not None and device_id != phone.device_id:
            raise SyncError("Phone changed")
        yield phone

    return out, phone, SyncService(factory, tmp_path / "manifest")


def put(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def test_preview_finds_exact_hashes_once_and_excludes_unsafe_local_files(setup_sync) -> None:
    out, phone, service = setup_sync
    put(out / "A" / "song.mp3", b"same")
    put(out / "song-copy.mp3", b"same")
    put(out / ".hidden.mp3", b"hidden")
    put(out / "partial.mp3.part", b"partial")
    (out / "linked.mp3").symlink_to(out / "A" / "song.mp3")
    phone.files["Music/renamed.mp3"] = b"same"

    plan = service.preview(out)

    assert len([item for item in plan.items if item.direction == "matched"]) == 1
    assert not [item for item in plan.items if item.direction != "matched"]


def test_preview_marks_same_name_and_near_metadata_low_confidence(setup_sync) -> None:
    out, phone, service = setup_sync
    put(out / "Artist - Track.mp3", b"computer")
    phone.files["Artist - Track.mp3"] = b"phone"

    plan = service.preview(out)

    assert len(plan.items) == 2
    assert all(item.confidence == "low" for item in plan.items)
    assert all("filename" in item.reason for item in plan.items)


def test_typo_near_name_with_artist_and_duration_is_low_confidence() -> None:
    local = [TrackFile("local/Blue Monday.mp3", 10, "a" * 64, "New Order", "Blue Monday", 450)]
    phone = [TrackFile("Blue Mondy.mp3", 10, "b" * 64, "New Order", "Blue Mondy", 451)]

    items = _build_items(local, phone)

    assert len(items) == 2
    assert all(item.confidence == "low" for item in items)
    assert all("Possible duplicate" in item.reason for item in items)


def test_different_songs_by_same_artist_and_duration_stay_high_confidence() -> None:
    local = [TrackFile("local/Blue Monday.mp3", 10, "a" * 64, "New Order", "Blue Monday", 450)]
    phone = [TrackFile("Temptation.mp3", 10, "b" * 64, "New Order", "Temptation", 450)]

    items = _build_items(local, phone)

    assert len(items) == 2
    assert all(item.confidence == "high" for item in items)


def test_similarity_normalization_retains_non_latin_script() -> None:
    local = [TrackFile("local/夜に駆ける.mp3", 10, "a" * 64, "ヨルシカ", "夜に駆ける", 240)]
    phone = [TrackFile("夜に駆ける (Live).mp3", 10, "b" * 64, "ヨルシカ", "夜に駆ける", 241)]

    items = _build_items(local, phone)

    assert all(item.confidence == "low" for item in items)
    assert all("Possible duplicate" in item.reason for item in items)


def test_preview_uses_streamed_phone_metadata_for_near_match(setup_sync, monkeypatch) -> None:
    import mutagen

    out, phone, service = setup_sync
    put(out / "Artist - Blue Monday.mp3", b"computer bytes")
    phone.files["Artist - Blue Mondy.mp3"] = b"phone bytes"

    class Audio:
        info = type("Info", (), {"length": 201.0})()

        def __init__(self, title: str) -> None:
            self.tags = {"artist": ["Artist"], "title": [title]}

        def get(self, key: str):
            return self.tags.get(key)

    def read_tags(filething, easy: bool):
        assert easy is True
        if isinstance(filething, Path):
            return Audio("Blue Monday")
        return Audio("Blue Mondy")

    monkeypatch.setattr(mutagen, "File", read_tags)

    plan = service.preview(out)

    assert len(plan.items) == 2
    assert all(item.confidence == "low" for item in plan.items)
    assert all("Possible duplicate" in item.reason for item in plan.items)


def test_sanitized_collision_names_are_reserved_before_allocation() -> None:
    tracks = [
        TrackFile("a:b.mp3", 1, "a" * 64),
        TrackFile("a?b.mp3", 1, "b" * 64),
        TrackFile("z" * 300 + ".mp3", 1, "c" * 64),
    ]

    items = _build_items(tracks, [])

    targets = {item.track.relative_path: item.target_name for item in items}
    assert targets["a:b.mp3"] == "a_b.mp3"
    assert targets["a?b.mp3"] == "a_b (2).mp3"
    assert _safe_basename(targets["z" * 300 + ".mp3"]).endswith(".mp3")
    assert len(targets["z" * 300 + ".mp3"].encode("utf-8")) <= 240


def test_long_colliding_names_keep_distinct_suffixes_with_utf8_byte_limit() -> None:
    prefix = "界" * 100
    tracks = [
        TrackFile(f"{prefix} first.mp3", 1, "a" * 64),
        TrackFile(f"{prefix} second.mp3", 1, "b" * 64),
    ]

    items = _build_items(tracks, [])

    names = [item.target_name for item in items]
    assert names[0] != names[1]
    assert names[1].endswith(" (2).mp3")
    assert all(name.endswith(".mp3") for name in names)
    assert all(len(name.encode("utf-8")) <= 240 for name in names)


@pytest.mark.parametrize("symlink_parent", [False, True])
def test_transfer_rejects_source_replaced_by_symlink(setup_sync, symlink_parent: bool) -> None:
    out, phone, service = setup_sync
    source = out / "nested" / "song.mp3"
    put(source, b"planned bytes")
    plan = service.preview(out)
    item = plan.items[0]
    outside = out.parent / "outside.mp3"
    outside.write_bytes(b"outside bytes")
    if symlink_parent:
        source.parent.rename(out / "moved")
        source.parent.symlink_to(out / "moved", target_is_directory=True)
    else:
        source.unlink()
        source.symlink_to(outside)

    result = service.transfer(plan, {item.id})

    assert result[0].status == "error"
    assert "symlink" in result[0].message
    assert phone.files == {}


def test_preview_rejects_phone_traversal(setup_sync) -> None:
    out, phone, service = setup_sync
    phone.files["../escape.mp3"] = b"bad"
    with pytest.raises(SyncError, match="unsafe"):
        service.preview(out)


def test_to_phone_stages_verifies_publishes_and_retry_skips_identical(setup_sync) -> None:
    out, phone, service = setup_sync
    put(out / "nested" / "song.mp3", b"audio bytes")
    plan = service.preview(out)
    selected = {item.id for item in plan.items}

    result = service.transfer(plan, selected)
    repeated = service.transfer(plan, selected)

    assert [entry.status for entry in result] == ["done"]
    assert phone.files["song.mp3"] == b"audio bytes"
    assert repeated[0].status == "done"
    assert "identical" in repeated[0].message
    assert not any(name.startswith(".transmute-sync") for name in phone.files)
    saved = next((tmp_path for tmp_path in service.manifest_dir.glob("*.json")))
    assert json.loads(saved.read_text())["version"] == 1


def test_to_phone_corruption_and_collision_preserve_existing(setup_sync) -> None:
    out, phone, service = setup_sync
    put(out / "song.mp3", b"original")
    plan = service.preview(out)
    item_id = plan.items[0].id
    phone.corrupt_write = True

    result = service.transfer(plan, {item_id})

    assert result[0].status == "error"
    assert "readback" in result[0].message
    assert "song.mp3" not in phone.files
    assert not phone.files

    phone.corrupt_write = False
    phone.files["song.mp3"] = b"user content"
    result = service.transfer(plan, {item_id})
    assert result[0].status == "error"
    assert phone.files["song.mp3"] == b"user content"


def test_stale_source_and_cancel_are_truthful(setup_sync) -> None:
    out, phone, service = setup_sync
    put(out / "one.mp3", b"one")
    put(out / "two.mp3", b"two")
    plan = service.preview(out)
    selected = {item.id for item in plan.items}
    first = plan.items[0]
    (out / first.track.relative_path).write_bytes(b"changed")

    results = service.transfer(plan, selected, cancelled=lambda: True)
    assert all(result.status == "cancelled" for result in results)
    result = service.transfer(plan, {first.id})
    assert result[0].status == "error"
    assert "changed" in result[0].message
    assert not phone.files


def test_to_computer_readback_and_no_replace(setup_sync) -> None:
    out, phone, service = setup_sync
    phone.files["nested/song.mp3"] = b"phone audio"
    plan = service.preview(out)
    item = next(item for item in plan.items if item.direction == "to_computer")

    result = service.transfer(plan, {item.id})

    assert result[0].status == "done"
    assert (out / "song.mp3").read_bytes() == b"phone audio"
    (out / "song.mp3").write_bytes(b"user media")
    result = service.transfer(plan, {item.id})
    assert result[0].status == "error"
    assert (out / "song.mp3").read_bytes() == b"user media"


def test_phone_disconnect_isolated_to_item(setup_sync) -> None:
    out, phone, service = setup_sync
    phone.files["bad.mp3"] = b"bad"
    phone.files["good.mp3"] = b"good"
    plan = service.preview(out)
    phone.fail_read.add("bad.mp3")

    results = service.transfer(plan, {item.id for item in plan.items})

    assert [result.status for result in results] == ["error", "done"]
    assert (out / "good.mp3").read_bytes() == b"good"


def test_malformed_manifest_is_replaced_with_valid_version(setup_sync) -> None:
    out, phone, service = setup_sync
    put(out / "song.mp3", b"bytes")
    plan = service.preview(out)
    manifest = service._manifest_path(plan, phone.device_id)
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{broken')

    result = service.transfer(plan, {plan.items[0].id})

    assert result[0].status == "done"
    assert json.loads(manifest.read_text())["version"] == 1


def test_changed_target_content_never_overwritten(setup_sync) -> None:
    out, phone, service = setup_sync
    put(out / "song.mp3", b"computer bytes")
    plan = service.preview(out)
    phone.files["song.mp3"] = b"different phone bytes"

    result = service.transfer(plan, {plan.items[0].id})

    assert result[0].status == "error"
    assert phone.files["song.mp3"] == b"different phone bytes"
    assert hashlib.sha256(phone.files["song.mp3"]).hexdigest() != plan.items[0].track.sha256


def test_unsupported_phone_publish_fails_before_upload_and_keeps_computer_copy(setup_sync) -> None:
    out, phone, service = setup_sync
    put(out / "to-phone.mp3", b"full local track")
    phone.files["from-phone.mp3"] = b"full phone track"
    plan = service.preview(out)
    selected = {item.id for item in plan.items}

    writes: list[bytes] = []

    def record_write(path: str, source) -> None:
        value = source.read()
        writes.append(value)
        phone.files[path] = value

    phone.write_from = record_write
    phone.check_publish_support = lambda: (_ for _ in ()).throw(SyncError("safe no-replace unavailable"))
    results = service.transfer(plan, selected)

    assert not writes
    assert {result.status for result in results} == {"error", "done"}
    assert (out / "from-phone.mp3").read_bytes() == b"full phone track"
    assert "to-phone.mp3" not in phone.files


def test_preview_orders_available_then_possible_duplicates_then_exact_matches(setup_sync) -> None:
    out, phone, service = setup_sync
    put(out / "same.mp3", b"identical")
    put(out / "clash.mp3", b"local clash")
    put(out / "local.mp3", b"local only")
    phone.files.update({"same.mp3": b"identical", "clash.mp3": b"phone clash", "remote.mp3": b"phone only"})

    items = service.preview(out).items

    assert [(item.direction, item.confidence) for item in items] == [
        ("to_phone", "high"), ("to_computer", "high"),
        ("to_phone", "low"), ("to_computer", "low"), ("matched", "high"),
    ]
