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


@dataclass
class Metrics:
    completed: list = field(default_factory=list)  # пассажиры с полным циклом
    boarded_count: int = 0
    departures: list = field(default_factory=list)

    # --- колбэки из симуляции -----------------------------------------------

    def on_depart(self, t: float, car) -> None:
        self.departures.append(Departure(t, car.floor, car.direction, car.load()))

    def on_board(self, p) -> None:
        self.boarded_count += 1

    def on_alight(self, p) -> None:
        self.completed.append(p)

    # --- агрегация ------------------------------------------------------------

    def summary(self, t_from: float = 0.0, t_to: float = math.inf) -> dict:
        """Метрики по пассажирам, созданным в окне [t_from, t_to) (warm-up отброшен)."""
        ps = [p for p in self.completed if t_from <= p.t_created < t_to]
        if not ps:
            return {"passengers": 0}
        waits = sorted(p.waiting_time() for p in ps)
        transits = [p.transit_time() for p in ps]
        ttds = [p.time_to_destination() for p in ps]
        loads = [d.load for d in self.departures
                 if t_from <= d.t < t_to and d.load > 0]
        return {
            "passengers": len(ps),
            "awt": _mean(waits),
            "wait_median": _percentile(waits, 50),
            "wait_p90": _percentile(waits, 90),
            "wait_max": waits[-1],
            "transit_mean": _mean(transits),
            "ttd_mean": _mean(ttds),
            "load_mean": _mean(loads) if loads else 0.0,
            "load_max": max(loads) if loads else 0,
        }


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
