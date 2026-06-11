"""Здание: этажи, высоты, население (см. docs/DESIGN.md §2)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Building:
    floors: int = 17  # всего уровней, этаж 0 — холл/выход на улицу
    floor_height: float = 3.0  # м
    apartments_per_floor: int = 4
    persons_per_apartment: float = 2.7  # CIBSE: 1.5–1.9 чел/спальню

    # население по этажам; холл (этаж 0) не населён
    population: list = field(init=False)

    def __post_init__(self) -> None:
        per_floor = self.apartments_per_floor * self.persons_per_apartment
        self.population = [0.0] + [per_floor] * (self.floors - 1)

    @property
    def total_population(self) -> float:
        return sum(self.population)

    def height_of(self, floor: int) -> float:
        return floor * self.floor_height
