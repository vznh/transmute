"""Entry point: thin wiring only."""

import sys

from .app import App


def main() -> None:
    sys.stdout.write("\x1b]0;Transmute\x07")  # terminal tab title
    sys.stdout.flush()
    App().run()
