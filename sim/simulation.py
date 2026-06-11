"""Сборка симуляции: здание + спрос + кабина + контроллер + метрики."""

from __future__ import annotations

import random

from .building import Building
from .car import Car
from .config import CarParams
from .controller import DISPATCHERS
from .engine import Engine
from .metrics import Metrics
from .passengers import PassengerGenerator
from .traffic import DemandProfile


class Simulation:
    def __init__(self, building: Building, profile: DemandProfile,
                 car_params: CarParams | None = None, seed: int = 0,
                 t_start: float = 0.0, n_cars: int = 1,
                 patience: float = 0.0, dispatcher: str = "nearest_car") -> None:
        self.building = building
        self.profile = profile
        self.engine = Engine(start=t_start)
        self.metrics = Metrics()
        self.controller = DISPATCHERS[dispatcher](building.floors,
                                                  patience=patience)
        params = car_params or CarParams()
        self.cars = [Car(self.engine, building, params, self.controller,
                         self.metrics, idx=i) for i in range(n_cars)]
        self.controller.attach(self.cars, self.engine, self.metrics)
        self.rng = random.Random(seed)
        self.generator = PassengerGenerator(building, profile, self.rng)
        self._batches = None

    @property
    def car(self):  # удобство для одиночных конфигураций и старых тестов
        return self.cars[0]

    def run(self, t_end: float) -> None:
        """Прогнать симуляцию до момента t_end (секунды модельного времени)."""
        if self._batches is None:
            self._batches = self.generator.batches(self.engine.now, t_end)
        else:
            raise RuntimeError("Simulation.run() вызывается один раз; "
                               "для пошагового режима используйте step_until()")
        self._pump(t_end)

    # --- пошаговый режим (для realtime-веба) ---------------------------------

    def start_stream(self, t_end: float) -> None:
        self._batches = self.generator.batches(self.engine.now, t_end)
        self._next_batch = next(self._batches, None)

    def step_until(self, t: float) -> None:
        while self._next_batch is not None and self._next_batch[0] <= t:
            bt, batch = self._next_batch
            self.engine.run_until(bt)
            self.controller.add_passengers(batch)
            self._next_batch = next(self._batches, None)
        self.engine.run_until(t)

    # --- внутреннее ------------------------------------------------------------

    def _pump(self, t_end: float) -> None:
        for bt, batch in self._batches:
            self.engine.run_until(bt)
            self.controller.add_passengers(batch)
        self.engine.run_until(t_end)
        # дать кабинам развезти оставшихся (без новых прибытий), максимум 30 мин
        deadline = t_end + 1800.0
        while self.controller.total_waiting() + sum(c.load() for c in self.cars) > 0 \
                and self.engine.now < deadline and self.engine.peek() < deadline:
            self.engine.run_until(self.engine.peek())
