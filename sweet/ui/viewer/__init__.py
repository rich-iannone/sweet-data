"""The Sweet data viewer: a virtualized, column-aware grid over lazy data."""

from .app import ViewerApp, run_viewer
from .data_view import DataView

__all__ = ["DataView", "ViewerApp", "run_viewer"]
