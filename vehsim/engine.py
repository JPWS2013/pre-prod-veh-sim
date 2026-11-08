"""Discrete-event core: a day-stamped priority queue with a fixed phase order."""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Iterator


class Phase(IntEnum):
    """Order in which events that share a day are processed."""

    RESERVATION_ARRIVES = 0
    ORDER_PLACED = 1
    BUILD_COMPLETE = 2
    HOLD_RELEASED = 3
    VEHICLE_ARRIVES = 4


@dataclass(order=True)
class Event:
    day: int
    phase: Phase
    seq: int
    data: dict[str, Any] = field(compare=False, default_factory=dict)


class EventQueue:
    def __init__(self) -> None:
        self._heap: list[Event] = []
        self._seq = 0  # tie-breaker keeps same-day, same-phase events in insertion order

    def schedule(self, day: int, phase: Phase, **data: Any) -> Event:
        self._seq += 1
        ev = Event(day, phase, self._seq, data)
        heapq.heappush(self._heap, ev)
        return ev

    def pop_day(self, day: int) -> Iterator[Event]:
        """Yield every event due on or before ``day`` in (day, phase, insertion) order.

        Events scheduled for ``day`` while iterating are also yielded.
        """
        while self._heap and self._heap[0].day <= day:
            yield heapq.heappop(self._heap)

    def __len__(self) -> int:
        return len(self._heap)
