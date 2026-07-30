"""Terminal helpers shared outside the full-screen app."""
import sys

from rich.console import Console

console = Console(highlight=False)


def set_terminal_title(title: str) -> None:
    sys.stdout.write(f"\x1b]0;{title}\x07")
    sys.stdout.flush()
