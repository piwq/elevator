"""Генерация пассажиров: неоднородный Пуассоновский поток групп.

Модель Kuusinen et al. 2012 / Sorsa et al. 2021 (docs/RESEARCH.md §2.1):
группы прибывают по неоднородному Пуассону (розыгрыш методом прореживания,
Lewis & Shedler 1979), размер группы — геометрическое распределение,
происхождение/назначение — по миксу in/out/interfloor и населению этажей.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from .building import Building
from .traffic import DemandProfile


@dataclass
class Passenger:
    pid: int
    origin: int
    destination: int
    t_created: float
    t_board: float = -1.0
    t_alight: float = -1.0

    @property
    def direction(self) -> int:
        return 1 if self.destination > self.origin else -1

    def waiting_time(self) -> float:
        return self.t_board - self.t_created

    def transit_time(self) -> float:
        return self.t_alight - self.t_board

    def time_to_destination(self) -> float:
        return self.t_alight - self.t_created


@dataclass
class PassengerGenerator:
    building: Building
    profile: DemandProfile
    rng: random.Random
    _next_pid: int = 0
    _weights_cache: list = field(default_factory=list)

    def __post_init__(self) -> None:
        # веса жилых этажей для розыгрыша происхождения/назначения
        self._weights_cache = self.building.population[1:]
        self._upper_floors = list(range(1, self.building.floors))

    def batches(self, t_start: float, t_end: float):
        """Итератор (t, [Passenger, ...]) — группы на интервале [t_start, t_end).

        Прореживание: кандидаты с максимальной интенсивностью, принятие
        с вероятностью λ(t)/λ_max.
        """
        pop = self.building.total_population
        lam_max = self.profile.max_batch_rate(pop)
        if lam_max <= 0:
            return
        t = t_start
        while True:
            t += self.rng.expovariate(lam_max)
            if t >= t_end:
                return
            lam = self.profile.batch_rate(t, pop)
            if self.rng.random() * lam_max <= lam:
                yield t, self._make_batch(t)

    def _make_batch(self, t: float) -> list:
        size = self._geometric(self.profile.mean_batch)
        origin, destination = self._draw_od(t)
        batch = []
        for _ in range(size):
            batch.append(Passenger(self._next_pid, origin, destination, t))
            self._next_pid += 1
        return batch

    def _geometric(self, mean: float) -> int:
        """Геометрическое распределение на {1, 2, ...} со средним mean."""
        if mean <= 1.0:
            return 1
        p = 1.0 / mean
        size = 1
        while self.rng.random() > p:
            size += 1
        return size

    def _draw_od(self, t: float) -> tuple:
        mix = self.profile.mix(t)
        r = self.rng.random() * (mix.mix_in + mix.mix_out + mix.mix_inter)
        if r < mix.mix_in:  # вход: холл -> жилой этаж
            return 0, self._draw_floor()
        if r < mix.mix_in + mix.mix_out:  # выход: жилой этаж -> холл
            return self._draw_floor(), 0
        # межэтажная поездка
        o = self._draw_floor()
        d = o
        while d == o:
            d = self._draw_floor()
        return o, d

    def _draw_floor(self) -> int:
        return self.rng.choices(self._upper_floors, weights=self._weights_cache)[0]
