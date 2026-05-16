__version__ = "0.1.0"
__author__ = "Rich Iannone"
__email__ = "riannone@me.com"

# Only import what's available to avoid import errors during installation
__all__ = ["__version__", "__author__", "__email__"]

try:
    from .core.transforms import TransformStep  # noqa: F401
    from .core.workbook import Sheet, Workbook  # noqa: F401
    from .core.workspace import Workspace  # noqa: F401
    from .notebook import SweetWidget  # noqa: F401

    __all__.extend(["TransformStep", "Sheet", "Workbook", "Workspace", "SweetWidget"])
except ImportError:
    # Dependencies not yet installed
    pass


def load_ipython_extension(ipython):
    """IPython extension entry point — enables `%load_ext sweet`."""
    from .notebook import load_ipython_extension as _load

    _load(ipython)


def unload_ipython_extension(ipython):
    """IPython extension cleanup."""
    from .notebook import unload_ipython_extension as _unload

    _unload(ipython)
