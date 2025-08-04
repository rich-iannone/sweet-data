"""Sweet - A Textual-based interactive console application for data engineering."""

__version__ = "0.1.0"
__author__ = "Rich Iannone"
__email__ = "rich@posit.co"

# Only import what's available to avoid import errors during installation
__all__ = ["__version__", "__author__", "__email__"]

try:
    from .core.transforms import TransformStep  # noqa: F401
    from .core.workbook import Sheet, Workbook  # noqa: F401

    __all__.extend(["TransformStep", "Sheet", "Workbook"])
except ImportError:
    # Dependencies not yet installed
    pass
