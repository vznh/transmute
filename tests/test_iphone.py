from __future__ import annotations

import asyncio
import io
import sys
import types
from uuid import uuid4

import pytest

import transmute.iphone as iphone
from transmute.iphone import _PhoneStore, open_spotify
from transmute.sync import SyncError


class FakeDevice:
    def __init__(self, serial: str, usb: bool = True) -> None:
        self.serial = serial
        self.is_usb = usb


class FakeAfc:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {"/Documents/song.mp3": b"abc"}
        self.handles: dict[int, tuple[str, str, io.BytesIO]] = {}
        self.closed: list[int] = []
        self.links: list[tuple[str, str, object]] = []
        self.removed: list[str] = []

    async def fopen(self, path: str, mode: str = "r") -> int:
        handle = len(self.handles) + 1
        initial = self.files.get(path, b"")
        self.handles[handle] = path, mode, io.BytesIO(initial)
        return handle

    async def fread(self, handle: int, size: int) -> bytes:
        return self.handles[handle][2].read(size)

    async def fwrite(self, handle: int, data: bytes) -> None:
        self.handles[handle][2].write(data)

    async def fclose(self, handle: int) -> None:
        path, mode, stream = self.handles[handle]
        if mode == "w":
            self.files[path] = stream.getvalue()
        stream.close()
        self.closed.append(handle)

    async def stat(self, path: str) -> dict[str, str]:
        if path == "/Documents":
            return {"st_ifmt": "S_IFDIR"}
        if path not in self.files:
            raise FileNotFoundError(path)
        return {"st_ifmt": "S_IFREG", "st_size": str(len(self.files[path]))}

    async def listdir(self, path: str) -> list[str]:
        return ["song.mp3"] if path == "/Documents" else []

    async def link(self, target: str, source: str, type_: object) -> None:
        self.links.append((target, source, type_))
        if target in self.files:
            self.files[source] = self.files[target]

    async def rm_single(self, path: str) -> None:
        self.removed.append(path)
        self.files.pop(path, None)


def store(afc: FakeAfc | None = None) -> tuple[_PhoneStore, FakeAfc]:
    event_loop = asyncio.new_event_loop()
    afc = afc or FakeAfc()
    return _PhoneStore(event_loop, object(), afc, "device-1", "Test iPhone"), afc


def install_fake_api(monkeypatch: pytest.MonkeyPatch, *, devices: list[FakeDevice]) -> dict[str, object]:
    calls: dict[str, object] = {}
    afc = FakeAfc()

    async def list_devices() -> list[FakeDevice]:
        return devices

    async def create_using_usbmux(serial: str, **kwargs: object) -> object:
        calls["usb"] = (serial, kwargs)
        return types.SimpleNamespace(all_values={"DeviceName": "Test iPhone"}, close=async_noop)

    async def create_house_arrest(lockdown: object, **kwargs: object) -> FakeAfc:
        calls["house"] = kwargs
        return afc

    async def async_noop() -> None:
        return None

    package = types.ModuleType("pymobiledevice3")
    package.__path__ = []  # type: ignore[attr-defined]
    lockdown_mod = types.ModuleType("pymobiledevice3.lockdown")
    lockdown_mod.create_using_usbmux = create_using_usbmux  # type: ignore[attr-defined]
    usbmux_mod = types.ModuleType("pymobiledevice3.usbmux")
    usbmux_mod.list_devices = list_devices  # type: ignore[attr-defined]
    services_mod = types.ModuleType("pymobiledevice3.services")
    services_mod.__path__ = []  # type: ignore[attr-defined]
    house_mod = types.ModuleType("pymobiledevice3.services.house_arrest")
    house_mod.HouseArrestService = types.SimpleNamespace(create=create_house_arrest)  # type: ignore[attr-defined]
    afc_mod = types.ModuleType("pymobiledevice3.services.afc")
    afc_mod.AfcLinkType = types.SimpleNamespace(HARDLINK="hardlink")  # type: ignore[attr-defined]
    exceptions_mod = types.ModuleType("pymobiledevice3.exceptions")

    AfcFileNotFoundError = FileNotFoundError

    exceptions_mod.AfcFileNotFoundError = AfcFileNotFoundError  # type: ignore[attr-defined]
    for name, module in (
        ("pymobiledevice3", package),
        ("pymobiledevice3.lockdown", lockdown_mod),
        ("pymobiledevice3.usbmux", usbmux_mod),
        ("pymobiledevice3.services", services_mod),
        ("pymobiledevice3.services.house_arrest", house_mod),
        ("pymobiledevice3.services.afc", afc_mod),
        ("pymobiledevice3.exceptions", exceptions_mod),
    ):
        monkeypatch.setitem(sys.modules, name, module)
    calls["afc"] = afc
    return calls


def test_open_spotify_uses_usb_without_pairing_and_documents_only(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = install_fake_api(monkeypatch, devices=[FakeDevice("usb-1"), FakeDevice("wifi-1", False)])

    with open_spotify() as phone:
        assert phone.device_id == "usb-1"
        assert phone.device_name == "Test iPhone"

    assert calls["usb"] == ("usb-1", {"autopair": False, "connection_type": "USB"})
    assert calls["house"] == {"bundle_id": "com.spotify.client", "documents_only": True}


@pytest.mark.parametrize(
    ("devices", "device_id", "message"),
    [([], None, "No iPhone"), ([FakeDevice("usb") , FakeDevice("usb2")], None, "More than one"), ([FakeDevice("wifi", False)], None, "No iPhone"), ([FakeDevice("other")], "wanted", "planned iPhone")],
)
def test_discovery_rejects_missing_ambiguous_or_wrong_device(
    monkeypatch: pytest.MonkeyPatch, devices: list[FakeDevice], device_id: str | None, message: str
) -> None:
    install_fake_api(monkeypatch, devices=devices)
    with pytest.raises(SyncError, match=message):
        with open_spotify(device_id):
            pytest.fail("discovery should have failed")


def test_missing_optional_dependency_is_actionable(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("pymobiledevice3.lockdown", "pymobiledevice3.usbmux", "pymobiledevice3.services.house_arrest"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.setitem(sys.modules, "pymobiledevice3", types.ModuleType("pymobiledevice3"))
    with pytest.raises(SyncError, match="pymobiledevice3 is missing"):
        with open_spotify():
            pass


@pytest.mark.parametrize(
    ("failure", "message"),
    [("locked", "Unlock and trust"), ("missing_spotify", "install Spotify")],
)
def test_locked_phone_and_missing_spotify_are_actionable(
    monkeypatch: pytest.MonkeyPatch, failure: str, message: str
) -> None:
    install_fake_api(monkeypatch, devices=[FakeDevice("usb")])
    if failure == "locked":
        lockdown_mod = sys.modules["pymobiledevice3.lockdown"]

        async def locked(*args: object, **kwargs: object) -> object:
            raise RuntimeError("device is locked")

        lockdown_mod.create_using_usbmux = locked  # type: ignore[attr-defined]
    else:
        house_mod = sys.modules["pymobiledevice3.services.house_arrest"]

        async def missing_app(*args: object, **kwargs: object) -> object:
            raise RuntimeError("ApplicationLookupFailed")

        house_mod.HouseArrestService.create = missing_app  # type: ignore[attr-defined]

    with pytest.raises(SyncError, match=message):
        with open_spotify():
            pytest.fail("device setup should have failed")


def test_list_files_walks_only_regular_mp3_and_skips_hidden_and_partial() -> None:
    phone, afc = store()

    async def listdir(path: str) -> list[str]:
        return {
            "/Documents": ["nested", ".hidden", "unfinished.part", "song.mp3", "link.mp3"],
            "/Documents/nested": ["child.MP3"],
        }[path]

    async def stat(path: str) -> dict[str, str]:
        if path == "/Documents/nested":
            return {"st_ifmt": "S_IFDIR"}
        if path.endswith("link.mp3"):
            return {"st_ifmt": "S_IFLNK"}
        return {"st_ifmt": "S_IFREG"}

    afc.listdir = listdir  # type: ignore[method-assign]
    afc.stat = stat  # type: ignore[method-assign]
    assert phone.list_files() == ["nested/child.MP3", "song.mp3"]
    phone._loop.close()


@pytest.mark.parametrize("path", ["../outside.mp3", "/absolute.mp3", "nested/../../x", "a\\b.mp3", "a\x00b.mp3"])
def test_paths_reject_traversal(path: str) -> None:
    phone, _ = store()
    with pytest.raises(SyncError, match="unsafe"):
        phone.exists(path)
    phone._loop.close()


def test_streams_in_chunks_and_closes_handles() -> None:
    phone, afc = store()
    temporary = f".transmute-sync-{uuid4().hex}.partial"
    afc.files["/Documents/large.mp3"] = b"a" * (1024 * 1024 + 5)
    sink = io.BytesIO()
    phone.read_to("large.mp3", sink)
    assert sink.getvalue() == afc.files["/Documents/large.mp3"]
    phone.write_from(temporary, io.BytesIO(b"b" * (1024 * 1024 + 1)))
    assert afc.files[f"/Documents/{temporary}"] == b"b" * (1024 * 1024 + 1)
    assert afc.closed == [1, 2]
    phone._loop.close()


def test_stream_failure_still_closes_handle() -> None:
    phone, afc = store()

    async def fail_read(handle: int, size: int) -> bytes:
        raise RuntimeError("device failed")

    afc.fread = fail_read  # type: ignore[method-assign]
    with pytest.raises(SyncError):
        phone.read_to("song.mp3", io.BytesIO())
    assert afc.closed == [1]
    phone._loop.close()


def test_operation_deadline_is_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    phone, _ = store()
    monkeypatch.setattr(iphone, "_DEADLINE_SECONDS", 0.001)

    async def stalled() -> None:
        await asyncio.Future()

    with pytest.raises(SyncError, match="timed out"):
        phone._run(stalled())
    phone._loop.close()


def test_unsafe_directory_entry_stops_inventory() -> None:
    phone, afc = store()

    async def malicious_listdir(path: str) -> list[str]:
        return ["../outside.mp3"]

    afc.listdir = malicious_listdir  # type: ignore[method-assign]
    with pytest.raises(SyncError, match="unsafe directory entry"):
        phone.list_files()
    phone._loop.close()


def test_publish_uses_hardlink_and_only_removes_own_temp(monkeypatch: pytest.MonkeyPatch) -> None:
    api = install_fake_api(monkeypatch, devices=[FakeDevice("usb")])
    phone, _ = store(api["afc"])  # type: ignore[arg-type]
    afc = api["afc"]
    assert isinstance(afc, FakeAfc)
    temporary = f".transmute-sync-{uuid4().hex}.partial"
    afc.files[f"/Documents/{temporary}"] = b"new"
    phone.publish(temporary, "final.mp3")
    assert afc.links == [
        (f"/Documents/{temporary}", "/Documents/final.mp3", "hardlink")
    ]
    assert afc.files["/Documents/final.mp3"] == b"new"
    assert afc.removed == [f"/Documents/{temporary}"]
    afc.files["/Documents/existing.mp3"] = b"keep"
    with pytest.raises(FileExistsError):
        phone.publish(temporary, "existing.mp3")
    assert afc.files["/Documents/existing.mp3"] == b"keep"
    phone._loop.close()


def test_publish_fails_closed_when_hardlinks_are_unsupported() -> None:
    phone, afc = store()
    temporary = f".transmute-sync-{uuid4().hex}.partial"
    afc.files[f"/Documents/{temporary}"] = b"new"

    async def unsupported_link(target: str, source: str, type_: object) -> None:
        raise OSError("hardlinks unsupported")

    afc.link = unsupported_link  # type: ignore[method-assign]
    with pytest.raises(SyncError, match="cannot publish files atomically"):
        phone.publish(temporary, "final.mp3")
    assert afc.files == {
        "/Documents/song.mp3": b"abc",
        f"/Documents/{temporary}": b"new",
    }
    assert not afc.removed
    phone._loop.close()


def test_afc_existing_destination_status_maps_to_file_exists() -> None:
    from pymobiledevice3.exceptions import AfcException
    from pymobiledevice3.services.afc import AfcError

    phone, afc = store()
    temporary = f".transmute-sync-{uuid4().hex}.partial"
    afc.files[f"/Documents/{temporary}"] = b"new"

    async def link_collision(target: str, source: str, type_: object) -> None:
        raise AfcException("object exists", AfcError.OBJECT_EXISTS, source)

    afc.link = link_collision  # type: ignore[method-assign]
    with pytest.raises(FileExistsError):
        phone.publish(temporary, "occupied.mp3")
    assert f"/Documents/{temporary}" in afc.files
    assert not afc.removed
    phone._loop.close()


def test_afc_missing_stat_maps_to_false() -> None:
    from pymobiledevice3.exceptions import AfcFileNotFoundError

    phone, afc = store()

    async def missing(path: str) -> dict[str, str]:
        raise AfcFileNotFoundError("missing", 4, path)

    afc.stat = missing  # type: ignore[method-assign]
    assert phone.exists("absent.mp3") is False
    phone._loop.close()


def test_writes_and_removes_reject_non_temporary_paths() -> None:
    phone, _ = store()
    with pytest.raises(SyncError, match="temporary"):
        phone.write_from("final.mp3", io.BytesIO(b"data"))
    with pytest.raises(SyncError, match="cleanup"):
        phone.remove("final.mp3")
    phone._loop.close()
