"""Settings and shared constants."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

STATE_DIR = Path.home() / ".transmute"
HISTORY_FILE = STATE_DIR / "history"
ACTIVITY_FILE = STATE_DIR / "activity.sqlite3"
SETTINGS_FILE = STATE_DIR / "settings.json"
SETTINGS_VERSION = 1
QUALITIES = ("128", "192", "256", "320")
MAX_WORKERS = 4


@dataclass(frozen=True)
class Settings:
    out_dir: Path = field(default_factory=lambda: Path.home() / "Downloads")
    quality: str = "320"
