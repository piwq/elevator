"""Группа лифтов (Nearest Car), терпение пассажиров, энергия, вывод из строя."""

import pytest

from sim.building import Building
from sim.car import CarState
from sim.config import CarParams
from sim.simulation import Simulation
from sim.traffic import constant_profile


def run_sim(n_cars, percent=8.0, seed=21, patience=0.0, hours=2.0,
            cp=None, floors=17):
    b = Building(floors=floors)
    prof = constant_profile(percent, 0.45, 0.45, 0.10)
    sim = Simulation(b, prof, cp or CarParams(), seed=seed,
                     n_cars=n_cars, patience=patience)
    sim.run(hours * 3600.0)
    return sim


def test_two_cars_beat_one():
    """Та же нагрузка, тот же seed: два лифта заметно сокращают ожидание."""
    awt1 = run_sim(1).metrics.summary(900.0)["awt"]
    awt2 = run_sim(2).metrics.summary(900.0)["awt"]
    assert awt2 < awt1 * 0.6, f"1 лифт: {awt1:.1f} c, 2 лифта: {awt2:.1f} c"


def test_both_cars_actually_work():
    sim = run_sim(2)
    cars_used = {d.car for d in sim.metrics.departures}
    assert cars_used == {0, 1}
    # обе кабины везли пассажиров, а не просто катались
    for idx in (0, 1):
        assert any(d.load > 0 for d in sim.metrics.departures if d.car == idx)


def test_all_delivered_with_two_cars():
    sim = run_sim(2, percent=6.0)
    assert sim.controller.total_waiting() == 0
    assert all(c.load() == 0 for c in sim.cars)
    for p in sim.metrics.completed:
        assert p.t_created <= p.t_board <= p.t_alight


def test_patience_abandonment():
    """Перегруз + терпение 120 с: часть уходит пешком, и это видно в метриках."""
    sim = run_sim(1, percent=25.0, patience=120.0, hours=1.0)
    s = sim.metrics.summary()
    assert len(sim.metrics.abandoned) > 0
    assert s["abandoned"] == len(sim.metrics.abandoned)
    # никто из уехавших не ждал заметно дольше терпения
    assert s["wait_max"] <= 120.0 + 30.0  # запас на тех, кто уже садился
    # брошенные не попали в доставленные
    done_ids = {p.pid for p in sim.metrics.completed}
    assert all(p.pid not in done_ids for p in sim.metrics.abandoned)


def test_energy_accounting():
    sim = run_sim(1, percent=6.0)
    kwh = sim.metrics.energy_j / 3.6e6
    n_trips = len(sim.metrics.departures)
    assert n_trips > 50
    assert kwh > 0
    # порядок величины: единицы Вт*ч на рейс (литература: 10-50 Вт*ч/старт)
    wh_per_trip = kwh * 1000 / n_trips
    assert 1.0 < wh_per_trip < 100.0


def test_out_of_service_car_finishes_riders_then_stops():
    sim = run_sim(2, percent=6.0, hours=0.0)  # пустой прогон, управляем вручную
    from sim.passengers import Passenger
    c0, c1 = sim.cars
    # пассажир едет, в пути кабину выводят из строя
    sim.controller.add_passengers([Passenger(0, 0, 9, sim.engine.now)])
    sim.engine.run_until(sim.engine.now + 5.0)
    served_by = c0 if (c0.state != CarState.IDLE) else c1
    served_by.set_out_of_service(True)
    sim.engine.run_until(sim.engine.now + 300.0)
    # пассажир доставлен, кабина больше не берёт вызовы
    assert len(sim.metrics.completed) == 1
    sim.controller.add_passengers([Passenger(1, 5, 0, sim.engine.now)])
    sim.engine.run_until(sim.engine.now + 300.0)
    assert len(sim.metrics.completed) == 2
    other = c1 if served_by is c0 else c0
    assert any(d.car == other.idx for d in sim.metrics.departures
               if d.t > sim.engine.now - 300.0)


def test_heatmap_and_hourly():
    sim = run_sim(1, percent=6.0)
    hm = sim.metrics.heatmap(17)
    assert len(hm) == 24 and len(hm[0]) == 17
    # пассажиры были только в первые 2 часа прогона (час 0 и 1)
    populated_hours = {h for h in range(24)
                       if any(v is not None for v in hm[h])}
    assert populated_hours and populated_hours <= {0, 1, 2}
    hourly = sim.metrics.hourly_awt()
    assert hourly[0][1] > 0  # в первый час кто-то уехал
