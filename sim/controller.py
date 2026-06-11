"""Групповое управление лифтами.

Один лифт — full selective collective (стандарт жилого дома): кабина собирает
вызовы по ходу движения в порядке этажей, реверс у дальнего вызова
противоположного направления, полная кабина проезжает мимо.

Группа 2+ кабин — классический Nearest Car (Barney, Elevator Traffic
Handbook): каждому вызову считается «figure of suitability» по всем кабинам,
вызов закрепляется за лучшей; при освобождении кабины вызовы
перераспределяются. Внутри кабины — те же правила collective.
См. docs/RESEARCH.md §4.1-4.2.
"""

from __future__ import annotations

from collections import deque


class GroupCollective:
    def __init__(self, floors: int, patience: float = 0.0) -> None:
        self.floors = floors
        self.patience = patience  # сек до ухода пешком; 0 — ждут вечно
        # очереди ожидающих: waiting[floor][направление]
        self.waiting = [{1: deque(), -1: deque()} for _ in range(floors)]
        self.assigned: dict = {}  # (floor, dir) -> Car
        self.cars: list = []
        self.engine = None
        self.metrics = None

    def attach(self, cars, engine, metrics) -> None:
        self.cars = cars
        self.engine = engine
        self.metrics = metrics

    # --- интерфейс генератора пассажиров -----------------------------------

    def add_passengers(self, batch) -> None:
        touched = set()
        for p in batch:
            self.waiting[p.origin][p.direction].append(p)
            touched.add((p.origin, p.direction))
            if self.patience > 0:
                self.engine.schedule(self.patience, lambda p=p: self._abandon(p))
        for floor, d in touched:
            self._ensure_assigned(floor, d)

    def _abandon(self, p) -> None:
        if p.t_board >= 0:
            return  # уже уехал
        q = self.waiting[p.origin][p.direction]
        try:
            q.remove(p)
        except ValueError:
            return
        self.metrics.on_abandon(p, self.engine.now)
        if not q:
            self.assigned.pop((p.origin, p.direction), None)

    # --- назначение вызовов (Nearest Car) -----------------------------------

    def _figure_of_suitability(self, car, floor: int, d: int) -> float:
        if car.out_of_service:
            return 0.0
        n = self.floors
        dist = abs(car.floor - floor)
        if car.direction == 0:
            return (n + 1) - dist
        toward = (car.direction > 0 and floor >= car.floor) or \
                 (car.direction < 0 and floor <= car.floor)
        if toward and car.direction == d:
            return (n + 2) - dist
        if toward:
            return (n + 1) - dist
        return 1.0

    def _ensure_assigned(self, floor: int, d: int) -> None:
        if not self.waiting[floor][d]:
            self.assigned.pop((floor, d), None)
            return
        best = max(self.cars,
                   key=lambda c: self._figure_of_suitability(c, floor, d))
        if self._figure_of_suitability(best, floor, d) <= 0:
            return  # все кабины выведены из обслуживания
        if self.assigned.get((floor, d)) is not best:
            self.assigned[(floor, d)] = best
            best.notify_call()

    def rebalance(self) -> None:
        """Пересмотр всех назначений (кабина освободилась / вышла из строя)."""
        for floor in range(self.floors):
            for d in (1, -1):
                if self.waiting[floor][d]:
                    self._ensure_assigned(floor, d)

    def on_idle(self, car) -> None:
        self.rebalance()

    # --- интерфейс кабины ---------------------------------------------------

    def _hall_call_for(self, car, floor: int, d: int) -> bool:
        return bool(self.waiting[floor][d]) and \
            self.assigned.get((floor, d)) is car

    def next_target(self, car, in_flight: bool = False):
        """Следующий этаж остановки по правилам collective, или None."""
        has_space = car.load() < car.p.capacity
        direction = car.direction if car.direction != 0 else None

        if direction is None:
            floors_with_calls = [
                f for f in range(self.floors)
                if f in car.car_calls or (
                    has_space and (self._hall_call_for(car, f, 1)
                                   or self._hall_call_for(car, f, -1)))
            ]
            if not floors_with_calls:
                return None
            return min(floors_with_calls, key=lambda f: (abs(f - car.floor), f))

        for d in (direction, -direction):
            # 1) попутные остановки впереди по направлению d
            ahead = self._ahead_floors(car.floor, d, include_current=not in_flight)
            same_dir = [
                f for f in ahead
                if f in car.car_calls
                or (has_space and self._hall_call_for(car, f, d))
            ]
            if same_dir:
                return min(same_dir, key=lambda f: abs(f - car.floor))
            # 2) реверс: дальний вызов противоположного направления впереди
            opposite = [f for f in ahead
                        if has_space and self._hall_call_for(car, f, -d)]
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
            if any(f in car.car_calls or self._queued_for(car, f) for f in ahead):
                return d
            behind = self._ahead_floors(floor, -d, include_current=False)
            if any(f in car.car_calls or self._queued_for(car, f) for f in behind):
                return -d
        if self.waiting[floor][1]:
            return 1
        if self.waiting[floor][-1]:
            return -1
        return d

    def board(self, car, floor: int) -> list:
        """Посадка на этаже floor в направлении обслуживания. Возвращает севших.

        Садятся в первую пришедшую кабину независимо от назначения вызова —
        как в жизни с обычными кнопками.
        """
        if car.out_of_service:
            return []
        d = self.service_direction(car, floor)
        if d == 0:
            return []
        car.direction = d
        queue = self.waiting[floor][d]
        boarded = []
        while queue and car.load() + len(boarded) < car.p.capacity:
            boarded.append(queue.popleft())
        if not queue:
            self.assigned.pop((floor, d), None)
        else:
            self._ensure_assigned(floor, d)  # остались — перераспределить
        return boarded

    # --- наблюдение (метрики, веб) -------------------------------------------

    def queue_length(self, floor: int) -> int:
        return len(self.waiting[floor][1]) + len(self.waiting[floor][-1])

    def total_waiting(self) -> int:
        return sum(self.queue_length(f) for f in range(self.floors))

    # --- внутреннее -----------------------------------------------------------

    def _queued_for(self, car, floor: int) -> bool:
        return self._hall_call_for(car, floor, 1) or \
            self._hall_call_for(car, floor, -1)

    def _ahead_floors(self, floor: int, d: int, include_current: bool) -> list:
        start = floor if include_current else floor + d
        return list(range(start, self.floors if d > 0 else -1, d))


# единственная кабина — частный случай группового управления
SelectiveCollective = GroupCollective
