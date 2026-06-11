"""Суточный профиль спроса жилого дома.

Кусочно-постоянная интенсивность и микс потоков по форме измерений Siikonen
(утренний down-peak, вечерний пик с преобладанием входящего, двусторонний
трафик днём) — docs/RESEARCH.md §2.2. Точные часы — наша параметризация.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TrafficSlice:
    start_h: float  # час начала
    intensity: float  # доля от пикового спроса (0..1)
    mix_in: float  # доля входящих (с улицы вверх)
    mix_out: float  # доля исходящих (вниз на улицу)
    mix_inter: float  # доля межэтажных


RESIDENTIAL_DAY = [
    TrafficSlice(0.0, 0.05, 0.50, 0.50, 0.00),
    TrafficSlice(6.0, 1.00, 0.15, 0.75, 0.10),  # утренний down-peak
    TrafficSlice(9.0, 0.35, 0.40, 0.40, 0.20),  # дневной двусторонний
    TrafficSlice(16.0, 0.90, 0.55, 0.30, 0.15),  # вечерний пик, преобладает вход
    TrafficSlice(20.0, 0.40, 0.45, 0.45, 0.10),
]


@dataclass
class DemandProfile:
    """Преобразует профиль и пиковый спрос в λ(t) прибытия групп.

    peak_percent — пиковый спрос, % населения за 5 минут (ISO 8100-32
    residential: 6%). mean_batch — средний размер группы (жилые: 1.1–1.4).
    """

    slices: list
    peak_percent: float = 6.0
    mean_batch: float = 1.2

    def passenger_rate(self, t: float, population: float) -> float:
        """λ пассажиров, чел/с, в момент модельного времени t (с от 00:00)."""
        hour = (t / 3600.0) % 24.0
        sl = self._slice(hour)
        peak_per_s = (self.peak_percent / 100.0) * population / 300.0
        return sl.intensity * peak_per_s

    def batch_rate(self, t: float, population: float) -> float:
        """λ прибытия групп, групп/с."""
        return self.passenger_rate(t, population) / self.mean_batch

    def max_batch_rate(self, population: float) -> float:
        peak_per_s = (self.peak_percent / 100.0) * population / 300.0
        return max(s.intensity for s in self.slices) * peak_per_s / self.mean_batch

    def mix(self, t: float) -> TrafficSlice:
        return self._slice((t / 3600.0) % 24.0)

    def _slice(self, hour: float) -> TrafficSlice:
        current = self.slices[-1]
        for sl in self.slices:
            if hour >= sl.start_h:
                current = sl
        return current


def constant_profile(percent: float, mix_in: float, mix_out: float,
                     mix_inter: float, mean_batch: float = 1.2) -> DemandProfile:
    """Постоянный спрос для ISO-сценариев и верификационных прогонов."""
    return DemandProfile(
        slices=[TrafficSlice(0.0, 1.0, mix_in, mix_out, mix_inter)],
        peak_percent=percent,
        mean_batch=mean_batch,
    )
