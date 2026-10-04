"""Application footer."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import Footer, Static


class SweetFooter(Footer):
    """Custom footer with Sweet-specific bindings."""

    def compose(self) -> ComposeResult:
        yield Static("Press : for command mode")
