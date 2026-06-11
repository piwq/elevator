"""Системная верификация (DESIGN.md §4.3-4.5).

Главный тест: насыщенный up-peak должен воспроизводить аналитический RTT
Barney & Dos Santos: RTT = 2*H*tv + (S+1)*ts + 2*P*tp с
E[S] = N(1-(1-1/N)^P), E[H] = N - sum_{i<N} (i/N)^P.

Условия применимости формулы соблюдены в конфигурации теста: один вход,
равные высоты и населения этажей, номинальная скорость достигается за один
пролёт (v=1.0), прибытия по одному (без групп), нулевая выдержка дверей.
"""

import pytest

from sim.building import Building
from sim.config import CarParams
from sim.kinematics import trip_time
from sim.simulation import Simulation
from sim.traffic import constant_profile


def uppeak_car() -> CarParams:
    return CarParams(rated_speed=1.0, dwell_hall=0.0, dwell_car=0.0,
                     home_timeout=1e9)


def barney_rtt(n_floors: int, p: int, df: float, cp: CarParams) -> float:
    n = n_floors
    s = n * (1 - (1 - 1 / n) ** p)
    h = n - sum((i / n) ** p for i in range(1, n))
    tv = df / cp.rated_speed
    t_perf = trip_time(df, cp.rated_speed, cp.acceleration, cp.jerk) \
        + cp.door_open + cp.door_close
    ts = t_perf - tv
    return 2 * h * tv + (s + 1) * ts + 2 * p * cp.transfer_time


def test_uppeak_rtt_matches_barney():
    b = Building()  # 17 уровней: холл + 16 жилых, равные населения
    cp = uppeak_car()
    # 30% населения за 5 мин — глубокое насыщение, кабина всегда уходит полной
    prof = constant_profile(30.0, 1.0, 0.0, 0.0, mean_batch=1.0)
    sim = Simulation(b, prof, cp, seed=7)
    sim.run(6 * 3600.0)

    lobby_departs = [d.t for d in sim.metrics.departures
                     if d.floor == 0 and d.direction > 0 and d.load == cp.capacity]
    assert len(lobby_departs) > 100
    trips = lobby_departs[10:]  # отбросить разогрев
    measured_rtt = (trips[-1] - trips[0]) / (len(trips) - 1)

    expected = barney_rtt(b.floors - 1, cp.capacity, b.floor_height, cp)
    assert measured_rtt == pytest.approx(expected, rel=0.02), \
        f"sim {measured_rtt:.1f}s vs Barney {expected:.1f}s"


def test_all_passengers_delivered_daily_run():
    """Суточный прогон при умеренном спросе: все доставлены, адреса верны."""
    from sim.traffic import RESIDENTIAL_DAY, DemandProfile
    b = Building(floors=10)
    prof = DemandProfile(RESIDENTIAL_DAY, peak_percent=5.0)
    sim = Simulation(b, prof, CarParams(), seed=3)
    sim.run(24 * 3600.0)
    done = sim.metrics.completed
    assert len(done) > 200
    assert sim.controller.total_waiting() == 0
    assert sim.car.load() == 0
    for p in done:
        assert p.t_created <= p.t_board <= p.t_alight
        assert p.waiting_time() >= 0
    # умеренный спрос в 10-этажке: ожидание в разумных пределах ISO
    s = sim.metrics.summary()
    assert s["awt"] < 60.0


def test_capacity_never_exceeded():
    b = Building()
    prof = constant_profile(20.0, 0.5, 0.5, 0.0)
    cp = CarParams()
    sim = Simulation(b, prof, cp, seed=11)
    sim.run(3600.0)
    assert all(d.load <= cp.capacity for d in sim.metrics.departures)
    assert max(d.load for d in sim.metrics.departures) == cp.capacity  # насыщение


def test_home_landing():
    """Без спроса кабина возвращается в холл после таймаута простоя."""
    b = Building(floors=10)
    prof = constant_profile(0.0, 1.0, 0.0, 0.0)
    cp = CarParams(home_timeout=60.0)
    sim = Simulation(b, prof, cp, seed=1)
    # один пассажир: холл -> 7 этаж
    from sim.passengers import Passenger
    sim.engine.schedule(10.0, lambda: sim.controller.add_passengers(
        [Passenger(0, 0, 7, sim.engine.now)]))
    sim.run(600.0)
    assert sim.metrics.completed[0].destination == 7
    assert sim.car.floor == 0  # вернулась на парковку
    from sim.car import CarState
    assert sim.car.state == CarState.IDLE


def test_saturation_grows_with_demand():
    """Ступенчатый рост спроса: за точкой насыщения очереди расходятся."""
    b = Building()
    waits = []
    for pct in (4.0, 12.0):
        prof = constant_profile(pct, 0.45, 0.45, 0.10)
        sim = Simulation(b, prof, CarParams(), seed=5)
        sim.run(2 * 3600.0)
        waits.append(sim.metrics.summary(900.0)["awt"])
    assert waits[1] > waits[0] * 2  # 12% для одного лифта — глубокое насыщение
