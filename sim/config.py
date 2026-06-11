"""Параметры симуляции. Значения по умолчанию обоснованы в docs/DESIGN.md §2."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CarParams:
    rated_speed: float = 1.6  # м/с
    acceleration: float = 1.0  # м/с²
    jerk: float = 1.0  # м/с³
    capacity: int = 8  # чел, физический предел (630 кг по EN 81-20)
    door_open: float = 2.0  # с
    door_close: float = 3.0  # с
    dwell_hall: float = 4.0  # с, выдержка при остановке по вызову с этажа
    dwell_car: float = 2.0  # с, выдержка при остановке только по приказу
    transfer_time: float = 1.2  # с/чел в одну сторону (Barney/CIBSE)
    home_floor: int = 0  # парковка жилого лифта — холл
    home_timeout: float = 60.0  # с простоя до возврата на home_floor
