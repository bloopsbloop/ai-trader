from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Iterable, Iterator


class NetworkTransportForbidden(RuntimeError):
    pass


@dataclass(frozen=True)
class TransportMessage:
    stream: str
    payload_raw: str
    session_id: str = "session-1"


class MockTransport:
    """Finite offline transport. It owns no sockets and performs no I/O."""

    def __init__(self, messages: Iterable[TransportMessage]) -> None:
        self._messages = deque(messages)

    def __iter__(self) -> Iterator[TransportMessage]:
        while self._messages:
            yield self._messages.popleft()


def create_transport(kind: str, messages: Iterable[TransportMessage] = ()) -> MockTransport:
    if kind != "mock":
        raise NetworkTransportForbidden("M20.2 supports mock transport only; network transport is forbidden")
    return MockTransport(messages)
