"""UI-independent, additive synchronization of local MP3 files and Spotify Documents."""

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import subprocess
import tempfile
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Literal, Protocol
from uuid import uuid4

from .config import STATE_DIR

CHUNK_SIZE = 1024 * 1024
MAX_REASON = 240
_MANIFEST_VERSION = 1
_PARTIAL_RE = re.compile(r"(?:\.part(?:ial)?|\.tmp|\.temp)$", re.IGNORECASE)


class SyncError(RuntimeError):
    """A bounded, actionable sync integration or validation error."""


@dataclass(frozen=True)
class TrackFile:
    relative_path: str
    size: int
    sha256: str
    artist: str = ""
    title: str = ""
    duration: float | None = None


@dataclass(frozen=True)
class SyncItem:
    id: str
    direction: Literal["to_phone", "to_computer", "matched"]
    track: TrackFile
    target_name: str
    confidence: Literal["high", "low"]
    reason: str = ""
    duplicate_of: str | None = None


@dataclass(frozen=True)
class SyncPlan:
    device_id: str
    device_name: str
    out_dir: Path
    items: tuple[SyncItem, ...]
    computer_name: str = ""


@dataclass(frozen=True)
class SyncProgress:
    phase: str
    current: int = 0
    total: int = 0
    filename: str = ""
    item_id: str | None = None


@dataclass(frozen=True)
class SyncResult:
    item_id: str
    status: Literal["done", "error", "cancelled"]
    message: str = ""
    target_name: str = ""


class PhoneStore(Protocol):
    device_id: str
    device_name: str

    def list_files(self) -> list[str]: ...
    def exists(self, relative_path: str) -> bool: ...
    def read_to(self, relative_path: str, sink: BinaryIO) -> None: ...
    def write_from(self, relative_path: str, source: BinaryIO) -> None: ...
    def publish(self, temporary_path: str, destination_path: str) -> None: ...
    def check_publish_support(self) -> None: ...
    def remove(self, relative_path: str) -> None: ...


PhoneFactory = Callable[[str | None], AbstractContextManager[PhoneStore]]
ProgressCallback = Callable[[SyncProgress], None]


def _safe_relative(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise SyncError("Phone returned an unsafe file path.")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in value.split("/")):
        raise SyncError("Phone returned an unsafe file path.")
    return path.as_posix()


def _safe_basename(value: str) -> str:
    name = PurePosixPath(value).name
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    if not name or name.startswith(".") or _PARTIAL_RE.search(name):
        raise SyncError("A sync filename is hidden or temporary and cannot be transferred.")
    suffix = Path(name).suffix
    stem = name[: -len(suffix)] if suffix else name
    name = _utf8_prefix(stem, 240 - len(suffix.encode("utf-8"))) + suffix
    return name


def _utf8_prefix(value: str, max_bytes: int) -> str:
    result: list[str] = []
    used = 0
    for char in value:
        width = len(char.encode("utf-8"))
        if used + width > max_bytes:
            break
        result.append(char)
        used += width
    return "".join(result)


def _unique_target(base: str, occupied: set[str]) -> str:
    safe_base = _safe_basename(base)
    if safe_base.casefold() not in occupied:
        return safe_base
    suffix = Path(safe_base).suffix
    stem = safe_base[: -len(suffix)] if suffix else safe_base
    number = 2
    while True:
        marker = f" ({number})"
        stem_limit = 240 - len((marker + suffix).encode("utf-8"))
        candidate = _safe_basename(_utf8_prefix(stem, stem_limit) + marker + suffix)
        if candidate.casefold() not in occupied:
            return candidate
        number += 1


def _hash_stream(stream: BinaryIO) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    while True:
        block = stream.read(CHUNK_SIZE)
        if not block:
            break
        digest.update(block)
        size += len(block)
    return digest.hexdigest(), size


def _metadata(path: Path, relative: str) -> TrackFile:
    artist = title = ""
    duration: float | None = None
    try:
        from mutagen import File as MutagenFile

        audio = MutagenFile(path, easy=True)
        if audio is not None:
            artist = _first_tag(audio, "artist")
            title = _first_tag(audio, "title")
            raw_duration = getattr(getattr(audio, "info", None), "length", None)
            duration = float(raw_duration) if raw_duration is not None else None
    except Exception:  # noqa: BLE001 - metadata parsing must not block byte copying.
        pass
    digest, size = _hash_file(path)
    return TrackFile(relative, size, digest, artist[:160], title[:160], duration)


def _metadata_stream(stream: BinaryIO) -> tuple[str, str, float | None]:
    """Read embedded tags from an already-open phone readback stream."""
    artist = title = ""
    duration: float | None = None
    try:
        from mutagen import File as MutagenFile

        stream.seek(0)
        audio = MutagenFile(stream, easy=True)
        if audio is not None:
            artist = _first_tag(audio, "artist")
            title = _first_tag(audio, "title")
            raw_duration = getattr(getattr(audio, "info", None), "length", None)
            duration = float(raw_duration) if raw_duration is not None else None
    except Exception:  # noqa: BLE001 - unreadable embedded metadata does not block byte sync.
        pass
    finally:
        stream.seek(0)
    return artist[:160], title[:160], duration


def _first_tag(audio: object, key: str) -> str:
    value = getattr(audio, "get", lambda _key: None)(key)
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    return str(value or "").strip()


def _hash_file(path: Path) -> tuple[str, int]:
    with path.open("rb") as stream:
        return _hash_stream(stream)


def _phone_hash(phone: PhoneStore, relative_path: str) -> str:
    sink = tempfile.SpooledTemporaryFile(max_size=CHUNK_SIZE)
    try:
        phone.read_to(relative_path, sink)
        sink.seek(0)
        return _hash_stream(sink)[0]
    finally:
        sink.close()


def _eligible_path(path: Path, root: Path) -> bool:
    try:
        relative = path.relative_to(root)
        if path.is_symlink() or not path.is_file():
            return False
        if any(part.startswith(".") for part in relative.parts):
            return False
        return path.suffix.lower() == ".mp3" and not _PARTIAL_RE.search(path.name)
    except (OSError, ValueError):
        return False


def _fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _norm(text: str) -> str:
    folded = text.casefold()
    return " ".join("".join(char if char.isalnum() else " " for char in folded).split())


def _near(a: TrackFile, b: TrackFile) -> bool:
    left_artist = _norm(a.artist)
    right_artist = _norm(b.artist)
    left_title = _norm(a.title or Path(a.relative_path).stem)
    right_title = _norm(b.title or Path(b.relative_path).stem)
    artist_match = bool(left_artist and right_artist and left_artist == right_artist)
    title_match = bool(left_title and right_title and left_title == right_title)
    title_similarity = SequenceMatcher(None, left_title, right_title).ratio() if left_title and right_title else 0.0
    left_filename = _norm(Path(a.relative_path).stem)
    right_filename = _norm(Path(b.relative_path).stem)
    filename_match = bool(left_filename and right_filename and left_filename == right_filename)
    filename_similarity = SequenceMatcher(None, left_filename, right_filename).ratio() if left_filename and right_filename else 0.0
    duration_match = (
        a.duration is not None and b.duration is not None
        and abs(a.duration - b.duration) <= 3
    )
    if artist_match and title_match:
        return True
    # Similarity only lowers transfer confidence. Require independent artist
    # and duration evidence so distinct tracks by one artist remain selectable.
    if artist_match and duration_match and title_similarity >= 0.82:
        return True
    return filename_match or (duration_match and filename_similarity >= 0.9)


def _manifest_key(device_id: str, out_dir: Path) -> str:
    return _fingerprint(f"{device_id}\0{out_dir.resolve()}")


def _computer_name() -> str:
    """Return the Mac's user-visible Computer Name with a hostname fallback."""
    try:
        result = subprocess.run(
            ["scutil", "--get", "ComputerName"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()[:80]
    except (OSError, subprocess.TimeoutExpired):
        pass
    return socket.gethostname().strip()[:80] or "This computer"


class SyncService:
    """Build read-only plans and transfer explicitly selected files."""

    def __init__(
        self,
        phone_factory: PhoneFactory | None = None,
        manifest_dir: Path | None = None,
    ) -> None:
        self._phone_factory = phone_factory
        self.manifest_dir = Path(manifest_dir) if manifest_dir is not None else STATE_DIR / "sync"

    def _open_phone(self, device_id: str | None = None) -> AbstractContextManager[PhoneStore]:
        factory = self._phone_factory
        if factory is None:
            from .iphone import open_spotify

            factory = open_spotify
        return factory(device_id)

    def preview(
        self,
        out_dir: Path,
        excluded_paths: tuple[Path, ...] = (),
        progress: ProgressCallback | None = None,
    ) -> SyncPlan:
        root = Path(out_dir).expanduser().resolve()
        if not root.is_dir():
            raise SyncError("Output folder is unavailable; check /out and try again.")
        excluded = {Path(p).expanduser().resolve() for p in excluded_paths}
        local: list[TrackFile] = []
        for path in sorted(root.rglob("*")):
            if not _eligible_path(path, root) or path.resolve() in excluded:
                continue
            if progress:
                progress(SyncProgress("scan_output", len(local) + 1, 0, path.name))
            try:
                before = path.stat()
                track = _metadata(path, path.relative_to(root).as_posix())
                after = path.stat()
                if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
                    continue
                local.append(track)
            except OSError:
                continue
        with self._open_phone() as phone:
            remote: list[TrackFile] = []
            for index, raw_path in enumerate(phone.list_files(), 1):
                rel = _safe_relative(raw_path)
                if rel.startswith(".") or any(part.startswith(".") for part in rel.split("/")) or _PARTIAL_RE.search(rel):
                    continue
                if not rel.lower().endswith(".mp3"):
                    continue
                if progress:
                    progress(SyncProgress("scan_phone", index, 0, Path(rel).name))
                sink = tempfile.SpooledTemporaryFile(max_size=CHUNK_SIZE)
                try:
                    phone.read_to(rel, sink)
                    sink.seek(0)
                    digest, size = _hash_stream(sink)
                    sink.seek(0)
                    artist, title, duration = _metadata_stream(sink)
                finally:
                    sink.close()
                remote.append(TrackFile(rel, size, digest, artist, title or Path(rel).stem, duration))
            items = _build_items(local, remote)
            return SyncPlan(phone.device_id, phone.device_name, root, tuple(items), _computer_name())

    def transfer(
        self,
        plan: SyncPlan,
        selected_ids: set[str],
        progress: ProgressCallback | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> tuple[SyncResult, ...]:
        selected = [item for item in plan.items if item.id in selected_ids and item.direction != "matched"]
        known = {item.id for item in plan.items if item.direction != "matched"}
        if selected_ids - known:
            raise SyncError("The sync selection is stale; preview the folders again.")
        if not selected:
            return ()
        results: list[SyncResult] = []
        with self._open_phone(plan.device_id) as phone:
            if phone.device_id != plan.device_id:
                raise SyncError("The connected phone changed; preview again before syncing.")
            manifest = self._load_manifest(plan, phone.device_id)
            phone_items = [item for item in selected if item.direction == "to_phone"]
            publish_error: str | None = None
            if phone_items:
                check_publish = getattr(phone, "check_publish_support", None)
                if check_publish is not None:
                    try:
                        check_publish()
                    except Exception as exc:  # noqa: BLE001 - probe failure blocks only phone-bound copies.
                        publish_error = _bounded_error(exc)
            for index, item in enumerate(selected, 1):
                if cancelled and cancelled():
                    results.extend(SyncResult(rest.id, "cancelled", "Cancelled before transfer.", rest.target_name) for rest in selected[index - 1 :])
                    break
                if progress:
                    progress(SyncProgress("transfer", index, len(selected), item.target_name, item.id))
                try:
                    if item.direction == "to_phone" and publish_error:
                        result = SyncResult(item.id, "error", publish_error, item.target_name)
                    elif item.direction == "to_phone":
                        result = self._to_phone(phone, plan.out_dir, item)
                    else:
                        result = self._to_computer(phone, plan.out_dir, item)
                    if result.status == "done":
                        manifest[item.id] = {"sha256": item.track.sha256, "target": result.target_name}
                        try:
                            self._save_manifest(plan, phone.device_id, manifest)
                        except OSError:
                            result = SyncResult(item.id, "error", "Copied, but sync history could not be saved; retry is safe.", result.target_name)
                    results.append(result)
                except Exception as exc:  # noqa: BLE001 - isolate device and filesystem failures per item.
                    results.append(SyncResult(item.id, "error", _bounded_error(exc), item.target_name))
        return tuple(results)

    def _to_phone(self, phone: PhoneStore, out_dir: Path, item: SyncItem) -> SyncResult:
        source = _source_path(item, out_dir)
        before = source.stat()
        digest, size = _hash_file(source)
        if digest != item.track.sha256 or size != item.track.size:
            raise SyncError("Source changed after preview; preview the folders again.")
        destination = item.target_name
        if phone.exists(destination):
            if _phone_hash(phone, destination) == item.track.sha256:
                return SyncResult(item.id, "done", "Already present with identical content.", destination)
            raise FileExistsError(destination)
        temporary = f".transmute-sync-{uuid4().hex}.partial"
        try:
            with source.open("rb") as stream:
                phone.write_from(temporary, stream)
            if _phone_hash(phone, temporary) != item.track.sha256:
                raise SyncError("Phone readback did not match; the file was not published.")
            after = source.stat()
            if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
                raise SyncError("Source changed during transfer; preview again.")
            try:
                phone.publish(temporary, destination)
            except FileExistsError:
                if _phone_hash(phone, destination) == item.track.sha256:
                    return SyncResult(item.id, "done", "Already present with identical content.", destination)
                raise
            return SyncResult(item.id, "done", "Copied to phone.", destination)
        finally:
            try:
                phone.remove(temporary)
            except Exception:  # noqa: BLE001 - cleanup failure must not mask transfer outcome.
                pass

    def _to_computer(self, phone: PhoneStore, out_dir: Path, item: SyncItem) -> SyncResult:
        source = _safe_relative(item.track.relative_path)
        if not phone.exists(source):
            raise SyncError("Phone source disappeared after preview; preview again.")
        target = out_dir / item.target_name
        if target.exists() or target.is_symlink():
            if target.is_file() and not target.is_symlink() and _hash_file(target)[0] == item.track.sha256:
                return SyncResult(item.id, "done", "Already present with identical content.", item.target_name)
            raise FileExistsError(item.target_name)
        descriptor, name = tempfile.mkstemp(prefix=".transmute-sync-", suffix=".partial", dir=out_dir)
        os.close(descriptor)
        temporary = Path(name)
        try:
            with temporary.open("wb") as sink:
                phone.read_to(source, sink)
                sink.flush()
                os.fsync(sink.fileno())
            digest, size = _hash_file(temporary)
            if digest != item.track.sha256 or size != item.track.size:
                raise SyncError("Phone readback did not match; the file was not published.")
            if not phone.exists(source) or _phone_hash(phone, source) != item.track.sha256:
                raise SyncError("Phone source changed during transfer; preview again.")
            try:
                os.link(temporary, target)
            except FileExistsError:
                if target.is_file() and _hash_file(target)[0] == item.track.sha256:
                    return SyncResult(item.id, "done", "Already present with identical content.", item.target_name)
                raise
            return SyncResult(item.id, "done", "Copied to computer.", item.target_name)
        finally:
            temporary.unlink(missing_ok=True)

    def _manifest_path(self, plan: SyncPlan, device_id: str) -> Path:
        return self.manifest_dir / f"{_manifest_key(device_id, plan.out_dir)}.json"

    def _load_manifest(self, plan: SyncPlan, device_id: str) -> dict[str, dict[str, str]]:
        path = self._manifest_path(plan, device_id)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(data, dict) or data.get("version") != _MANIFEST_VERSION or not isinstance(data.get("successes"), dict):
            return {}
        return {str(k): v for k, v in data["successes"].items() if isinstance(v, dict) and isinstance(v.get("sha256"), str)}

    def _save_manifest(self, plan: SyncPlan, device_id: str, successes: dict[str, dict[str, str]]) -> None:
        path = self._manifest_path(plan, device_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"version": _MANIFEST_VERSION, "successes": successes}, sort_keys=True)
        descriptor, name = tempfile.mkstemp(prefix=".sync-", suffix=".tmp", dir=path.parent)
        tmp = Path(name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)


def _build_items(local: list[TrackFile], remote: list[TrackFile]) -> list[SyncItem]:
    items: list[SyncItem] = []
    matched_local: set[str] = set()
    matched_remote: set[str] = set()
    remote_by_hash: dict[str, TrackFile] = {}
    for track in remote:
        remote_by_hash.setdefault(track.sha256, track)
    seen_exact: set[str] = set()
    for track in local:
        counterpart = remote_by_hash.get(track.sha256)
        if counterpart:
            matched_local.add(track.relative_path)
            matched_remote.update(candidate.relative_path for candidate in remote if candidate.sha256 == track.sha256)
            if track.sha256 not in seen_exact:
                seen_exact.add(track.sha256)
                items.append(_item("matched", track, Path(track.relative_path).name, "high", "Exact SHA256 match."))
    for direction, sources, other, already in (
        ("to_phone", local, remote, matched_local),
        ("to_computer", remote, local, matched_remote),
    ):
        other_hashes = {track.sha256 for track in other}
        other_names = {_safe_basename(Path(track.relative_path).name).casefold() for track in other}
        occupied = set(other_names)
        for track in sources:
            if track.relative_path in already or track.sha256 in other_hashes:
                continue
            similar = next((candidate for candidate in other if _near(track, candidate)), None)
            name_conflict = Path(track.relative_path).name.casefold() in other_names
            confidence: Literal["high", "low"] = "low" if similar or name_conflict else "high"
            reason = "Possible duplicate based on metadata or filename." if similar else "Same filename has different content." if name_conflict else ""
            base = _safe_basename(Path(track.relative_path).name)
            target = _unique_target(base, occupied)
            occupied.add(target.casefold())
            if target != base:
                reason = (reason + " Flattened filename collision.").strip()
                confidence = "low"
            items.append(_item(direction, track, target, confidence, reason))
    items.sort(key=lambda item: 2 if item.direction == "matched" else 1 if item.confidence == "low" else 0)
    return items


def _item(direction: Literal["to_phone", "to_computer", "matched"], track: TrackFile, target: str, confidence: Literal["high", "low"], reason: str) -> SyncItem:
    stable_id = _fingerprint(f"{direction}\0{track.relative_path}\0{track.sha256}")[:20]
    return SyncItem(stable_id, direction, track, _safe_basename(target), confidence, reason[:MAX_REASON])


def _bounded_error(exc: Exception) -> str:
    if isinstance(exc, FileExistsError):
        return "Destination already exists; no file was replaced. Preview again."
    if isinstance(exc, SyncError):
        return str(exc)[:MAX_REASON]
    if isinstance(exc, OSError) and exc.errno in (28,):
        return "Destination storage is full; free space and retry."
    return "Transfer failed; check the phone connection and destination, then retry."


def _source_path(item: SyncItem, out_dir: Path) -> Path:
    rel = _safe_relative(item.track.relative_path)
    root = out_dir.resolve()
    path = root
    for part in rel.split("/"):
        path = path / part
        if path.is_symlink():
            raise SyncError("Planned source is now a symlink; preview the folders again.")
    resolved = path.resolve()
    if not resolved.is_relative_to(root):
        raise SyncError("Planned source path escaped the output folder; preview again.")
    if not resolved.is_file():
        raise SyncError("Planned source is no longer a regular file; preview the folders again.")
    return resolved
