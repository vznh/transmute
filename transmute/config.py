"""Settings and shared constants."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

HISTORY_FILE = Path.home() / ".transmute" / "history"
QUALITIES = ("128", "192", "256", "320")
MAX_WORKERS = 4


@dataclass(frozen=True)
class Settings:
    out_dir: Path = field(default_factory=lambda: Path.home() / "Downloads")
    quality: str = "320"
