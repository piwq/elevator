"""Сбор и агрегация метрик качества обслуживания (CIBSE/ISO, RESEARCH.md §6)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class Departure:
    t: float
    floor: int
    direction: int
    load: int
    car: int = 0


@dataclass
class Metrics:
    completed: list = field(default_factory=list)  # пассажиры с полным циклом
    abandoned: list = field(default_factory=list)  # ушли, не дождавшись
    boarded_count: int = 0
    departures: list = field(default_factory=list)
    energy_j: float = 0.0  # энергия поездок (без дежурной)
    # тепловая карта ожидания: [час][этаж] -> [сумма ожиданий, число пассажиров]
    heat: list = field(default_factory=lambda: [
        [[0.0, 0] for _ in range(64)] for _ in range(24)])

    # --- колбэки из симуляции -----------------------------------------------

    def on_depart(self, t: float, car) -> None:
        self.departures.append(Departure(t, car.floor, car.direction,
                                         car.load(), car.idx))

    def on_board(self, p) -> None:
        self.boarded_count += 1

    def on_alight(self, p) -> None:
        self.completed.append(p)
        hour = int(p.t_created // 3600) % 24
        if p.origin < 64:
            cell = self.heat[hour][p.origin]
            cell[0] += p.waiting_time()
            cell[1] += 1

    def on_abandon(self, p, t: float) -> None:
        self.abandoned.append(p)

    def on_trip_energy(self, joules: float) -> None:
        self.energy_j += joules

    # --- агрегация ------------------------------------------------------------

    def summary(self, t_from: float = 0.0, t_to: float = math.inf) -> dict:
        """Метрики по пассажирам, созданным в окне [t_from, t_to) (warm-up отброшен)."""
        ps = [p for p in self.completed if t_from <= p.t_created < t_to]
        aband = sum(1 for p in self.abandoned if t_from <= p.t_created < t_to)
        if not ps:
            return {"passengers": 0, "abandoned": aband}
        waits = sorted(p.waiting_time() for p in ps)
        transits = [p.transit_time() for p in ps]
        ttds = [p.time_to_destination() for p in ps]
        loads = [d.load for d in self.departures
                 if t_from <= d.t < t_to and d.load > 0]
        return {
            "passengers": len(ps),
            "abandoned": aband,
            "awt": _mean(waits),
            "wait_median": _percentile(waits, 50),
            "wait_p90": _percentile(waits, 90),
            "wait_max": waits[-1],
            "transit_mean": _mean(transits),
            "ttd_mean": _mean(ttds),
            "load_mean": _mean(loads) if loads else 0.0,
            "load_max": max(loads) if loads else 0,
        }

    def heatmap(self, floors: int) -> list:
        """[час][этаж] -> среднее ожидание, с (None, если пассажиров не было)."""
        return [[(cell[0] / cell[1] if cell[1] else None)
                 for cell in row[:floors]] for row in self.heat]

    def hourly_awt(self) -> list:
        """[час] -> (среднее ожидание, число пассажиров)."""
        out = []
        for hour in range(24):
            s = sum(c[0] for c in self.heat[hour])
            n = sum(c[1] for c in self.heat[hour])
            out.append((s / n if n else None, n))
        return out


def _mean(xs) -> float:
    return sum(xs) / len(xs)


def _percentile(sorted_xs, q: float) -> float:
    """Линейная интерполяция, sorted_xs отсортирован по возрастанию."""
    if len(sorted_xs) == 1:
        return sorted_xs[0]
    k = (len(sorted_xs) - 1) * q / 100.0
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return sorted_xs[lo]
    return sorted_xs[lo] + (sorted_xs[hi] - sorted_xs[lo]) * (k - lo)
