"""Shared constants, optional imports, and debug logging for the TUI modules."""

from __future__ import annotations

import logging
import os
from pathlib import Path

# Maximum number of rows to display in the DataGrid for large datasets
MAX_DISPLAY_ROWS = 1000


def setup_debug_logging() -> logging.Logger:
    """Return the `sweet_llm` debug logger.

    Logging to a file is opt-in: set ``SWEET_DEBUG_LOG`` to a file path (or to
    ``1`` for ``./sweet_llm_debug.log``). Otherwise records are discarded.
    """
    logger = logging.getLogger("sweet_llm")
    logger.propagate = False  # Never write to the console (it would corrupt the TUI)
    if logger.handlers:
        return logger

    target = os.environ.get("SWEET_DEBUG_LOG", "").strip()
    if target:
        log_file = Path.cwd() / "sweet_llm_debug.log" if target == "1" else Path(target)
        handler: logging.Handler = logging.FileHandler(log_file)
        handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
        logger.setLevel(logging.DEBUG)
    else:
        handler = logging.NullHandler()
    logger.addHandler(handler)
    return logger


debug_logger = setup_debug_logging()

try:
    import polars as pl
except ImportError:
    pl = None

# Try to import chatlas, but don't fail if it's not available
try:
    import chatlas

    CHATLAS_AVAILABLE = True
except ImportError:
    chatlas = None
    CHATLAS_AVAILABLE = False
