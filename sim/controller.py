"""Full selective collective — стандартный алгоритм одиночного жилого лифта.

Кабина собирает вызовы по ходу движения в порядке этажей; попутные вызовы
противоположного направления игнорируются; реверс — у дальнего вызова
противоположного направления; полная кабина проезжает мимо вызовов с этажей.
См. docs/RESEARCH.md §4.1.
"""

from __future__ import annotations

from collections import deque


class SelectiveCollective:
    def __init__(self, floors: int) -> None:
        self.floors = floors
        # очереди ожидающих: waiting[floor][направление]
        self.waiting = [{1: deque(), -1: deque()} for _ in range(floors)]
        self.car = None  # назначается при сборке симуляции

    # --- интерфейс генератора пассажиров -----------------------------------

    def add_passengers(self, batch) -> None:
        for p in batch:
            self.waiting[p.origin][p.direction].append(p)
        if batch:
            self.car.notify_call()

    # --- интерфейс кабины ---------------------------------------------------

    def next_target(self, car, in_flight: bool = False):
        """Следующий этаж остановки по правилам collective, или None."""
        has_space = car.load() < car.p.capacity
        direction = car.direction if car.direction != 0 else None

        if direction is None:
            floors_with_calls = [
                f for f in range(self.floors)
                if f in car.car_calls or (has_space and self._queued(f))
            ]
            if not floors_with_calls:
                return None
            return min(floors_with_calls, key=lambda f: (abs(f - car.floor), f))

        for d in (direction, -direction):
            # 1) попутные остановки впереди по направлению d
            ahead = self._ahead_floors(car.floor, d, include_current=not in_flight)
            same_dir = [
                f for f in ahead
                if f in car.car_calls or (has_space and self.waiting[f][d])
            ]
            if same_dir:
                return min(same_dir, key=lambda f: abs(f - car.floor))
            # 2) реверс: дальний вызов противоположного направления впереди
            opposite = [f for f in ahead if has_space and self.waiting[f][-d]]
            if opposite:
                return max(opposite, key=lambda f: abs(f - car.floor))
            if in_flight:
                return None  # в полёте смену направления не рассматриваем
        return None

    def service_direction(self, car, floor: int) -> int:
        """Направление дальнейшего движения после остановки на floor."""
        d = car.direction
        if d != 0:
            ahead = self._ahead_floors(floor, d, include_current=False)
            if any(f in car.car_calls or self._queued(f) for f in ahead):
                return d
            behind = self._ahead_floors(floor, -d, include_current=False)
            if any(f in car.car_calls or self._queued(f) for f in behind):
                return -d
        if self.waiting[floor][1]:
            return 1
        if self.waiting[floor][-1]:
            return -1
        return d

    def board(self, car, floor: int) -> list:
        """Посадка на этаже floor в направлении обслуживания. Возвращает севших."""
        d = self.service_direction(car, floor)
        if d == 0:
            return []
        car.direction = d
        queue = self.waiting[floor][d]
        boarded = []
        while queue and car.load() + len(boarded) < car.p.capacity:
            boarded.append(queue.popleft())
        return boarded

    # --- наблюдение (метрики, веб) -------------------------------------------

    def queue_length(self, floor: int) -> int:
        return len(self.waiting[floor][1]) + len(self.waiting[floor][-1])

    def total_waiting(self) -> int:
        return sum(self.queue_length(f) for f in range(self.floors))

    # --- внутреннее -----------------------------------------------------------

    def _queued(self, floor: int) -> bool:
        return bool(self.waiting[floor][1] or self.waiting[floor][-1])

    def _ahead_floors(self, floor: int, d: int, include_current: bool) -> list:
        start = floor if include_current else floor + d
        return list(range(start, self.floors if d > 0 else -1, d))
