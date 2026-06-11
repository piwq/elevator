"""Кинематика кабины: 7-фазная S-кривая с ограничением рывка.

Замкнутые формулы времени поездки по трём режимам (Peters, "Ideal Lift
Kinematics", ELEVCON 1995; Al-Sharif, METE III) — см. docs/RESEARCH.md §1.3.
Симметричный разгон/торможение. Все величины в СИ.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


def trip_time(d: float, v: float, a: float, j: float) -> float:
    """Полное время поездки на расстояние d из состояния покоя в покой."""
    if d <= 0:
        return 0.0
    if d >= v * v / a + v * a / j:  # режим A: достигнута номинальная скорость
        return d / v + v / a + a / j
    if d >= 2 * a**3 / j**2:  # режим B: достигнуто ускорение, не скорость
        return a / j + math.sqrt((a / j) ** 2 + 4 * d / a)
    # режим C: не достигнуто даже номинальное ускорение
    return (32 * d / j) ** (1 / 3)


@dataclass(frozen=True)
class Profile:
    """Аналитический профиль одной поездки от пола до пола.

    Хранит длительности 7 фаз; position(t)/velocity(t) восстанавливаются
    поэтапным интегрированием (для анимации и решений об остановке).
    Направление учитывается знаком sign.
    """

    origin: float  # стартовая координата, м
    sign: float  # +1 вверх, -1 вниз
    v_pk: float  # пиковая скорость, м/с
    a_pk: float  # пиковое ускорение, м/с²
    j: float  # рывок, м/с³
    t_phase: tuple  # длительности 7 фаз
    total: float  # суммарное время

    @staticmethod
    def plan(origin: float, target: float, v: float, a: float, j: float) -> "Profile":
        d = abs(target - origin)
        sign = 1.0 if target >= origin else -1.0
        if d == 0:
            return Profile(origin, sign, 0, 0, j, (0,) * 7, 0.0)
        total = trip_time(d, v, a, j)
        if d >= v * v / a + v * a / j:  # A
            v_pk, a_pk = v, a
            t1 = a / j
            t2 = v / a - a / j
            t4 = total - 2 * (2 * t1 + t2)
        elif d >= 2 * a**3 / j**2:  # B
            a_pk = a
            t1 = a / j
            v_pk = a * (total / 2 - t1)
            t2 = v_pk / a - t1
            t4 = 0.0
        else:  # C
            t1 = total / 4
            a_pk = j * t1
            v_pk = j * t1 * t1
            t2 = 0.0
            t4 = 0.0
        phases = (t1, t2, t1, t4, t1, t2, t1)
        return Profile(origin, sign, v_pk, a_pk, j, phases, total)

    def state(self, t: float) -> tuple:
        """(позиция, скорость, ускорение) в момент t от начала поездки."""
        clamped = max(0.0, min(t, self.total))
        at_end = t >= self.total
        t = clamped
        j_signs = (1, 0, -1, 0, -1, 0, 1)
        x, u, acc = 0.0, 0.0, 0.0
        for dur, js in zip(self.t_phase, j_signs):
            dt = min(t, dur)
            jj = js * self.j
            x += u * dt + acc * dt * dt / 2 + jj * dt**3 / 6
            u += acc * dt + jj * dt * dt / 2
            acc += jj * dt
            t -= dt
            if t <= 0:
                break
        if at_end:
            u, acc = 0.0, 0.0  # подавить остаточную ошибку округления фаз
        return self.origin + self.sign * x, self.sign * u, self.sign * acc

    def decel_start_time(self) -> float:
        """Момент начала торможения (старт фазы 5) от начала поездки."""
        t1, t2, _, t4 = self.t_phase[0], self.t_phase[1], self.t_phase[2], self.t_phase[3]
        return 2 * t1 + t2 + t4
