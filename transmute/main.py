"""Entry point: thin wiring only."""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .app import App


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="transmute",
        description="Convert YouTube or SoundCloud links into rich MP3s.",
        epilog=(
            "Run with no arguments to open the interface, then paste links. "
            "Type /help inside it for the command reference."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"transmute {__version__}",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    """Parse argv, then hand control to the interface.

    Installed as a console script, so `--version` and `--help` must answer and
    exit before the full-screen interface takes over the terminal.
    """
    _build_parser().parse_args(argv)
    sys.stdout.write("\x1b]0;Transmute\x07")  # terminal tab title
    sys.stdout.flush()
    App().run()
