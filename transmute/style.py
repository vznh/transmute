"""Theme and user-facing text constants."""

from prompt_toolkit.styles import Style

STYLE = Style.from_dict(
    {
        "header.bar": "#22d3ee",
        "header.title": "#22d3ee bold",
        "header.desc": "#bbbbbb",
        "subsection": "#7dd3fc",
        "dim": "#777777",
        "ok": "#4ade80",
        "err": "#f87171",
        "warn": "#facc15",
        "accent": "#22d3ee",
        "prompt": "#888888",
        "placeholder": "#666666 italic",
        "input.hint": "#666666",
        "input.warn": "#facc15",
        "rule": "#333333",
        "toolbar": "#7dd3fc bg:#16303a",
        "selected": "bg:#1b3a46",
    }
)

TAGLINE = "Convert YouTube or SoundCloud links into rich MP3s. Bring your own provider."
PLACEHOLDER = "Paste a YouTube or SoundCloud link"
HINT_PLACEHOLDER = "type a hint (low confidence)"
HELP_HINT = "/help for more commands"

HISTORY_SHOWN = 14
MESSAGES_SHOWN = 8
