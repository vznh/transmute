"""Durable, prompt-toolkit-independent user settings.

Settings resolve in one order: a runtime command in the current session
overrides the value read from this file, which overrides the built-in defaults
in `Settings`. There is no environment layer for output directory or quality
yet; a new source belongs in this resolution order rather than in scattered
`os.environ` reads.

The file is versioned JSON written atomically (temporary file plus
`os.replace`) so an interrupted write cannot corrupt the only saved copy.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .config import QUALITIES, SETTINGS_VERSION, Settings


class SettingsStoreError(RuntimeError):
    """Saved settings could not be read or written safely."""


class SettingsStore:
    """Reads and writes the persisted `Settings` record as versioned JSON."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path).expanduser()

    def load(self) -> Settings:
        """Return saved settings, or defaults when nothing is stored yet.

        A missing file is the normal first-run case and yields defaults. A
        present-but-unreadable or schema-incompatible file raises
        `SettingsStoreError` so the caller can warn and fall back to defaults
        without overwriting the unrecognized file.
        """
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return Settings()
        except OSError as exc:
            raise SettingsStoreError(
                f"Could not read settings at {self.path}: {exc}"
            ) from exc

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SettingsStoreError(
                f"Settings at {self.path} contain malformed JSON: {exc}"
            ) from exc
        if not isinstance(data, dict):
            raise SettingsStoreError(f"Settings at {self.path} are not a JSON object")

        version = data.get("version")
        if version != SETTINGS_VERSION:
            raise SettingsStoreError(
                f"Settings at {self.path} use unsupported schema version "
                f"{version!r}; expected {SETTINGS_VERSION}"
            )

        return Settings(
            out_dir=_resolve_out_dir(data.get("out_dir")),
            quality=_resolve_quality(data.get("quality")),
        )

    def save(self, settings: Settings) -> None:
        """Persist settings atomically with owner-only permissions."""
        payload = {
            "version": SETTINGS_VERSION,
            "out_dir": str(settings.out_dir),
            "quality": settings.quality,
        }
        text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        try:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            descriptor, tmp_name = tempfile.mkstemp(
                dir=self.path.parent, prefix=".settings-", suffix=".tmp"
            )
            tmp_path = Path(tmp_name)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    handle.write(text)
                    handle.flush()
                    os.fsync(handle.fileno())
                tmp_path.chmod(0o600)
                os.replace(tmp_path, self.path)
            except OSError:
                tmp_path.unlink(missing_ok=True)
                raise
        except OSError as exc:
            raise SettingsStoreError(
                f"Could not save settings at {self.path}: {exc}"
            ) from exc


def _resolve_out_dir(value: object) -> Path:
    if value is None:
        return Settings().out_dir
    if not isinstance(value, str) or not value:
        raise SettingsStoreError(f"invalid output directory: {value!r}")
    return Path(value).expanduser()


def _resolve_quality(value: object) -> str:
    if value is None:
        return Settings().quality
    if value not in QUALITIES:
        raise SettingsStoreError(f"unsupported quality: {value!r}")
    return value
