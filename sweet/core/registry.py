"""Named registries: the extension seams for step types, readers, and writers.

Built-in components register themselves through the same API that third-party
plugins will use, so the plugin surface is exercised from day one.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Generic, TypeVar

T = TypeVar("T")


class Registry(Generic[T]):
    """A name -> object mapping with decorator-based registration.

    Examples:
        >>> READERS = Registry("reader")
        >>> @READERS.register("csv")
        ... def read_csv(path): ...
        >>> READERS.get("csv")
    """

    def __init__(self, kind: str) -> None:
        self.kind = kind
        self._items: dict[str, T] = {}

    def register(self, name: str, obj: T | None = None, *, replace: bool = False):
        """Register `obj` under `name`, or return a decorator if `obj` is omitted.

        Raises:
            ValueError: If `name` is already registered and `replace` is False.
        """

        def _register(item: T) -> T:
            if name in self._items and not replace:
                raise ValueError(f"{self.kind} '{name}' is already registered")
            self._items[name] = item
            return item

        if obj is not None:
            return _register(obj)
        return _register

    def unregister(self, name: str) -> None:
        """Remove a registration (no-op if absent)."""
        self._items.pop(name, None)

    def get(self, name: str) -> T:
        """Look up a registered object.

        Raises:
            KeyError: With the list of known names, if `name` is not registered.
        """
        try:
            return self._items[name]
        except KeyError:
            known = ", ".join(sorted(self._items)) or "(none)"
            raise KeyError(f"Unknown {self.kind} '{name}'. Known: {known}") from None

    def find(self, predicate: Callable[[str, T], bool]) -> tuple[str, T] | None:
        """Return the first (name, obj) pair matching `predicate`, if any."""
        for name, item in self._items.items():
            if predicate(name, item):
                return name, item
        return None

    def names(self) -> list[str]:
        return list(self._items)

    def __contains__(self, name: object) -> bool:
        return name in self._items

    def __iter__(self) -> Iterator[str]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)
