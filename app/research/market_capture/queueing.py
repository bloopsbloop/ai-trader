from __future__ import annotations

from collections import deque
from typing import Generic, TypeVar

T = TypeVar("T")


class BackpressureError(RuntimeError):
    pass


class BoundedEventQueue(Generic[T]):
    def __init__(self, capacity: int) -> None:
        if capacity < 1:
            raise ValueError("capacity must be positive")
        self.capacity = capacity
        self._items: deque[T] = deque()

    def put_nowait(self, item: T) -> None:
        if len(self._items) >= self.capacity:
            raise BackpressureError("bounded event queue is full")
        self._items.append(item)

    def get_nowait(self) -> T:
        if not self._items:
            raise IndexError("queue is empty")
        return self._items.popleft()

    def __len__(self) -> int:
        return len(self._items)
