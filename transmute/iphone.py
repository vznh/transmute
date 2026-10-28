"""Synchronous, bounded AFC access to Spotify's vended Documents directory."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from typing import BinaryIO

_BUNDLE_ID = "com.spotify.client"
_DOCUMENTS_ROOT = "/Documents"
_CHUNK_SIZE = 1024 * 1024
_DEADLINE_SECONDS = 20.0
_MAX_ENTRIES = 20_000
_MAX_DEPTH = 32
_MAX_PATH_LENGTH = 1024
_TEMP_COMPONENT = re.compile(r"\.transmute-sync-[0-9a-f]{32}\.partial\Z")


def _sync_error(message: str, cause: BaseException | None = None) -> Exception:
    from .sync import SyncError

    error = SyncError(message)
    if cause is not None:
        error.__cause__ = cause
    return error


def _safe_relative(path: str) -> str:
    if (
        not isinstance(path, str)
        or not path
        or len(path) > _MAX_PATH_LENGTH
        or path.startswith("/")
        or "\\" in path
        or "\x00" in path
        or any(part in ("", ".", "..") for part in path.split("/"))
    ):
        raise _sync_error("Phone file path is unsafe; preview the phone again.")
    return path


def _remote_path(path: str) -> str:
    return f"{_DOCUMENTS_ROOT}/{_safe_relative(path)}"


def _is_hidden_or_partial(path: str) -> bool:
    parts = path.split("/")
    return any(part.startswith(".") for part in parts) or any(
        part.lower().endswith((".part", ".partial", ".tmp", ".temp")) for part in parts
    )


def _is_owned_temp(path: str) -> bool:
    return "/" not in path and bool(_TEMP_COMPONENT.fullmatch(path))


class _PhoneStore:
    def __init__(self, loop: asyncio.AbstractEventLoop, lockdown: object, service: object, device_id: str, device_name: str) -> None:
        self.device_id = device_id
        self.device_name = device_name
        self._loop = loop
        self._lockdown = lockdown
        self._service = service

    def _run(self, awaitable: object):
        try:
            return self._loop.run_until_complete(asyncio.wait_for(awaitable, timeout=_DEADLINE_SECONDS))
        except asyncio.TimeoutError as exc:
            raise _sync_error("iPhone USB operation timed out; unlock the phone and retry.", exc) from exc
        except Exception as exc:
            from pymobiledevice3.exceptions import AfcFileNotFoundError

            from .sync import SyncError

            if isinstance(exc, (AfcFileNotFoundError, FileNotFoundError, SyncError, FileExistsError)):
                raise
            raise _sync_error("iPhone USB operation failed; unlock and trust the phone, then retry.", exc) from exc

    def _check_exists(self, path: str) -> bool:
        from pymobiledevice3.exceptions import AfcFileNotFoundError

        try:
            self._run(self._service.stat(_remote_path(path)))
            return True
        except (AfcFileNotFoundError, FileNotFoundError):
            return False

    def list_files(self) -> list[str]:
        found: list[str] = []
        visited = 0

        def walk(relative_directory: str, remote_directory: str, depth: int) -> None:
            nonlocal visited
            if depth > _MAX_DEPTH:
                raise _sync_error("Spotify Documents is nested too deeply to scan safely.")
            names = self._run(self._service.listdir(remote_directory))
            for name in names:
                if not isinstance(name, str) or name in ("", ".", "..") or "/" in name or "\\" in name or "\x00" in name:
                    raise _sync_error("Phone returned an unsafe directory entry; scan stopped.")
                relative = f"{relative_directory}/{name}" if relative_directory else name
                relative = _safe_relative(relative)
                visited += 1
                if visited > _MAX_ENTRIES:
                    raise _sync_error("Spotify Documents contains too many entries to scan safely.")
                if _is_hidden_or_partial(relative):
                    continue
                absolute = f"{remote_directory.rstrip('/')}/{name}"
                try:
                    metadata = self._run(self._service.stat(absolute))
                except FileNotFoundError:
                    continue
                kind = metadata.get("st_ifmt")
                if kind == "S_IFDIR":
                    walk(relative, absolute, depth + 1)
                elif kind == "S_IFREG" and relative.lower().endswith(".mp3"):
                    found.append(relative)

        walk("", _DOCUMENTS_ROOT, 0)
        return found

    def exists(self, relative_path: str) -> bool:
        return self._check_exists(relative_path)

    def read_to(self, relative_path: str, sink: BinaryIO) -> None:
        handle = self._run(self._service.fopen(_remote_path(relative_path), "r"))
        try:
            while True:
                chunk = self._run(self._service.fread(handle, _CHUNK_SIZE))
                if not chunk:
                    break
                sink.write(chunk)
        finally:
            self._run(self._service.fclose(handle))

    def write_from(self, relative_path: str, source: BinaryIO) -> None:
        relative_path = _safe_relative(relative_path)
        if not _is_owned_temp(relative_path):
            raise _sync_error("Phone writes are limited to Transmute-owned temporary files.")
        handle = self._run(self._service.fopen(_remote_path(relative_path), "w"))
        try:
            while True:
                chunk = source.read(_CHUNK_SIZE)
                if not chunk:
                    break
                self._run(self._service.fwrite(handle, chunk))
        finally:
            self._run(self._service.fclose(handle))

    def publish(self, temporary_path: str, destination_path: str) -> None:
        temporary_path = _safe_relative(temporary_path)
        destination_path = _safe_relative(destination_path)
        if not _is_owned_temp(temporary_path):
            raise _sync_error("Phone publish source is not a Transmute temporary file.")
        if self._check_exists(destination_path):
            raise FileExistsError(destination_path)
        # AFC rename can replace a file created between this check and the rename.
        self._run(self._service.rename(_remote_path(temporary_path), _remote_path(destination_path)))

    def remove(self, relative_path: str) -> None:
        relative_path = _safe_relative(relative_path)
        if not _is_owned_temp(relative_path):
            raise _sync_error("Phone cleanup is limited to Transmute-owned temporary files.")
        self._run(self._service.rm_single(_remote_path(relative_path)))


@contextmanager
def open_spotify(device_id: str | None = None) -> Iterator[_PhoneStore]:
    """Open exactly one trusted USB iPhone and vend Spotify Documents only."""
    try:
        from pymobiledevice3.lockdown import create_using_usbmux
        from pymobiledevice3.services.house_arrest import HouseArrestService
        from pymobiledevice3.usbmux import list_devices
    except ImportError as exc:
        raise _sync_error("pymobiledevice3 is missing; reinstall Transmute to restore its iPhone sync support.", exc) from exc

    loop = asyncio.new_event_loop()
    lockdown = None
    service = None
    try:
        try:
            devices = loop.run_until_complete(asyncio.wait_for(list_devices(), timeout=_DEADLINE_SECONDS))
        except asyncio.TimeoutError as exc:
            raise _sync_error("iPhone USB discovery timed out; connect and unlock the phone, then retry.", exc) from exc
        except Exception as exc:
            raise _sync_error("Could not discover iPhones over USB; reconnect the phone and retry.", exc) from exc
        devices = [device for device in devices if device.is_usb]
        if device_id is not None:
            devices = [device for device in devices if device.serial == device_id]
            if not devices:
                raise _sync_error("The planned iPhone is not connected over USB; reconnect it and preview again.")
            if len(devices) != 1:
                raise _sync_error("The planned iPhone was found more than once; reconnect it and preview again.")
        elif not devices:
            raise _sync_error("No iPhone is connected by USB; connect and unlock your phone, then retry.")
        elif len(devices) != 1:
            raise _sync_error("More than one iPhone is connected; disconnect the other phones and retry.")
        device = devices[0]
        try:
            lockdown = loop.run_until_complete(
                asyncio.wait_for(
                    create_using_usbmux(device.serial, autopair=False, connection_type="USB"),
                    timeout=_DEADLINE_SECONDS,
                )
            )
            service = loop.run_until_complete(
                asyncio.wait_for(
                    HouseArrestService.create(lockdown, bundle_id=_BUNDLE_ID, documents_only=True),
                    timeout=_DEADLINE_SECONDS,
                )
            )
        except asyncio.TimeoutError as exc:
            raise _sync_error("Opening Spotify over USB timed out; unlock and reconnect the phone, then retry.", exc) from exc
        except Exception as exc:
            message = "Unlock and trust this iPhone, install Spotify, then reconnect and retry."
            raise _sync_error(message, exc) from exc
        name = str(lockdown.all_values.get("DeviceName") or "iPhone")[:100]
        store = _PhoneStore(loop, lockdown, service, device.serial, name)
        yield store
    finally:
        if service is not None:
            with suppress(Exception):
                loop.run_until_complete(asyncio.wait_for(service.close(), timeout=_DEADLINE_SECONDS))
        if lockdown is not None:
            with suppress(Exception):
                loop.run_until_complete(asyncio.wait_for(lockdown.close(), timeout=_DEADLINE_SECONDS))
        loop.close()
