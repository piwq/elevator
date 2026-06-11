"""Событийное ядро: очередь событий с отменой, модельные часы."""

from __future__ import annotations

import heapq
import itertools


class Event:
    __slots__ = ("time", "seq", "fn", "cancelled")

    def __init__(self, time: float, seq: int, fn) -> None:
        self.time = time
        self.seq = seq
        self.fn = fn
        self.cancelled = False

    def __lt__(self, other: "Event") -> bool:
        return (self.time, self.seq) < (other.time, other.seq)


class Engine:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start
        self._heap: list = []
        self._seq = itertools.count()

    def schedule(self, delay: float, fn) -> Event:
        ev = Event(self.now + max(0.0, delay), next(self._seq), fn)
        heapq.heappush(self._heap, ev)
        return ev

    def schedule_at(self, time: float, fn) -> Event:
        return self.schedule(time - self.now, fn)

    def cancel(self, ev: Event) -> None:
        ev.cancelled = True

    def run_until(self, t_end: float) -> None:
        """Выполнить все события с временем <= t_end; часы остановить на t_end."""
        while self._heap and self._heap[0].time <= t_end:
            ev = heapq.heappop(self._heap)
            if ev.cancelled:
                continue
            self.now = ev.time
            ev.fn()
        self.now = max(self.now, t_end)

    def peek(self) -> float:
        while self._heap and self._heap[0].cancelled:
            heapq.heappop(self._heap)
        return self._heap[0].time if self._heap else float("inf")
