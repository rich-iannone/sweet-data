"""Compatibility re-exports. The widgets now live in per-concern modules."""

from ._common import CHATLAS_AVAILABLE, MAX_DISPLAY_ROWS, debug_logger
from .welcome import WelcomeOverlay
from .file_browser import DataDirectoryTree, FileBrowserModal
from .grid import CustomDataTable, ExcelDataGrid
from .search import SearchOverlay
from .tools_panel import ToolsPanel
from .drawer import DrawerContainer
from .modals import (
    CellEditModal,
    SaveFileModal,
    CommandReferenceModal,
    PasteOptionsModal,
    NumericExtractionModal,
    ColumnConversionModal,
    QuitConfirmationModal,
    InitConfirmationModal,
    RowColumnDeleteModal,
    ValidationErrorModal,
    RowNavigationModal,
    DatabaseConnectionModal,
)
from .footer import SweetFooter

__all__ = [
    "CHATLAS_AVAILABLE",
    "MAX_DISPLAY_ROWS",
    "debug_logger",
    "WelcomeOverlay",
    "DataDirectoryTree",
    "FileBrowserModal",
    "CustomDataTable",
    "ExcelDataGrid",
    "SearchOverlay",
    "ToolsPanel",
    "DrawerContainer",
    "CellEditModal",
    "SaveFileModal",
    "CommandReferenceModal",
    "PasteOptionsModal",
    "NumericExtractionModal",
    "ColumnConversionModal",
    "QuitConfirmationModal",
    "InitConfirmationModal",
    "RowColumnDeleteModal",
    "ValidationErrorModal",
    "RowNavigationModal",
    "DatabaseConnectionModal",
    "SweetFooter",
]
