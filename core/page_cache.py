"""A tiny least-recently-used cache, for the side panel's page turner.

Holds only a handful of decoded preview pages (the panel uses 5), so
turning back one page is instant while memory stays bounded: nothing
here is ever preloaded, entries only get in once the user looked at that
page. Pure Python, no Qt.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Generic, Hashable, Optional, TypeVar

V = TypeVar("V")


class LruCache(Generic[V]):
    def __init__(self, capacity: int):
        if capacity < 1:
            raise ValueError("capacity must be at least 1")
        self.capacity = capacity
        self._items: "OrderedDict[Hashable, V]" = OrderedDict()

    def get(self, key: Hashable) -> Optional[V]:
        if key not in self._items:
            return None
        self._items.move_to_end(key)
        return self._items[key]

    def put(self, key: Hashable, value: V) -> None:
        self._items[key] = value
        self._items.move_to_end(key)
        while len(self._items) > self.capacity:
            self._items.popitem(last=False)

    def clear(self) -> None:
        self._items.clear()

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, key: Hashable) -> bool:
        return key in self._items
