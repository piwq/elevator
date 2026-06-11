"""Сценарии симуляции: все параметры в одном JSON-файле.

Формат (все ключи опциональны — на месте пропущенных значения по умолчанию,
неизвестные ключи — ошибка, чтобы ловить опечатки):

{
  "name": "Базовая 17-этажка",
  "building": {
    "floors": 17,                  // уровней всего, этаж 0 — холл
    "floor_height": 3.0,           // м
    "apartments_per_floor": 4,
    "persons_per_apartment": 2.7,
    "population_per_floor": null   // или массив длины floors (этаж 0 обычно 0)
  },
  "car": {
    "rated_speed": 1.6, "acceleration": 1.0, "jerk": 1.0,
    "capacity": 8,
    "door_open": 2.0, "door_close": 3.0,
    "dwell_hall": 4.0, "dwell_car": 2.0,
    "transfer_time": 1.2,
    "home_floor": 0, "home_timeout": 60.0
  },
  "cars": 1,                       // кабин в группе (1-8); 2+ — Nearest Car
  "demand": {
    "peak_percent": 6.0,           // пиковый спрос, % населения за 5 мин
    "mean_batch": 1.2,             // средний размер группы
    "patience_s": 600,             // ушёл пешком после стольких секунд; 0 — ждёт вечно
    "day_profile": [               // кусочно-постоянный суточный профиль
      {"from_hour": 6.0, "intensity": 1.0, "in": 0.15, "out": 0.75, "interfloor": 0.10},
      ...
    ]
  },
  "web": {"start_hour": 6.0}       // с какого часа стартует живая симуляция
}
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from pathlib import Path

from .building import Building
from .config import CarParams
from .traffic import RESIDENTIAL_DAY, DemandProfile, TrafficSlice


@dataclass
class Scenario:
    name: str
    building: Building
    car: CarParams
    profile: DemandProfile
    start_hour: float = 6.0
    n_cars: int = 1  # кабин в группе
    patience: float = 0.0  # сек ожидания до ухода пешком; 0 — без ухода
    dispatcher: str = "nearest_car"  # nearest_car | eta


def _take(section: dict, allowed: set, where: str) -> dict:
    unknown = set(section) - allowed
    if unknown:
        raise ValueError(f"{where}: неизвестные параметры {sorted(unknown)}; "
                         f"допустимые: {sorted(allowed)}")
    return section


def scenario_from_dict(raw: dict, fallback_name: str = "custom") -> Scenario:
    _take(raw, {"name", "building", "car", "cars", "dispatcher", "demand", "web"},
          "сценарий")

    b_kwargs = _take(raw.get("building", {}),
                     {f.name for f in dataclasses.fields(Building) if f.init},
                     "building")
    building = Building(**b_kwargs)

    c_kwargs = _take(raw.get("car", {}),
                     {f.name for f in dataclasses.fields(CarParams)}, "car")
    car = CarParams(**c_kwargs)
    if not 0 <= car.home_floor < building.floors:
        raise ValueError("car.home_floor вне диапазона этажей")

    n_cars = int(raw.get("cars", 1))
    if not 1 <= n_cars <= 8:
        raise ValueError("cars: ожидается от 1 до 8 кабин")
    dispatcher = str(raw.get("dispatcher", "nearest_car"))
    if dispatcher not in ("nearest_car", "eta"):
        raise ValueError("dispatcher: nearest_car или eta")

    demand = _take(raw.get("demand", {}),
                   {"peak_percent", "mean_batch", "day_profile", "patience_s"},
                   "demand")
    slices = RESIDENTIAL_DAY
    if "day_profile" in demand:
        slices = []
        for i, sl in enumerate(demand["day_profile"]):
            _take(sl, {"from_hour", "intensity", "in", "out", "interfloor"},
                  f"demand.day_profile[{i}]")
            mix_sum = sl.get("in", 0) + sl.get("out", 0) + sl.get("interfloor", 0)
            if mix_sum <= 0:
                raise ValueError(f"day_profile[{i}]: микс in/out/interfloor пуст")
            slices.append(TrafficSlice(
                start_h=float(sl["from_hour"]),
                intensity=float(sl["intensity"]),
                mix_in=sl.get("in", 0) / mix_sum,
                mix_out=sl.get("out", 0) / mix_sum,
                mix_inter=sl.get("interfloor", 0) / mix_sum,
            ))
        slices.sort(key=lambda s: s.start_h)
    profile = DemandProfile(
        slices=slices,
        peak_percent=float(demand.get("peak_percent", 6.0)),
        mean_batch=float(demand.get("mean_batch", 1.2)),
    )

    web = _take(raw.get("web", {}), {"start_hour"}, "web")
    return Scenario(
        name=raw.get("name", fallback_name),
        building=building,
        car=car,
        profile=profile,
        start_hour=float(web.get("start_hour", 6.0)),
        n_cars=n_cars,
        patience=float(demand.get("patience_s", 0.0)),
        dispatcher=dispatcher,
    )


def load_scenario(path: str | Path) -> Scenario:
    path = Path(path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    return scenario_from_dict(raw, fallback_name=path.stem)
