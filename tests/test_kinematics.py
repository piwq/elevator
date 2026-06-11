"""Верификация кинематики: формулы режимов A/B/C, непрерывность границ,
согласованность Profile.state с временем поездки (DESIGN.md §4.1)."""

import math

import pytest

from sim.kinematics import Profile, trip_time

V, A, J = 1.6, 1.0, 1.0


def test_reference_value_single_floor():
    # пример из RESEARCH.md §1.3: d=3.3, a=1, j=1 -> t = 1 + sqrt(1 + 13.2) ~= 4.77
    t = trip_time(3.3, 2.5, 1.0, 1.0)
    assert t == pytest.approx(1 + math.sqrt(1 + 4 * 3.3), rel=1e-12)
    assert t == pytest.approx(4.768, abs=1e-3)


def test_case_a_formula():
    d = 30.0  # заведомо режим A для V=1.6
    assert trip_time(d, V, A, J) == pytest.approx(d / V + V / A + A / J)


def test_case_boundaries_continuous():
    d_ab = V * V / A + V * A / J  # граница A/B
    d_bc = 2 * A**3 / J**2  # граница B/C
    eps = 1e-9
    assert trip_time(d_ab - eps, V, A, J) == pytest.approx(
        trip_time(d_ab + eps, V, A, J), abs=1e-6)
    assert trip_time(d_bc - eps, V, A, J) == pytest.approx(
        trip_time(d_bc + eps, V, A, J), abs=1e-6)


def test_monotone_in_distance():
    ts = [trip_time(d / 10, V, A, J) for d in range(1, 600)]
    assert all(b > a for a, b in zip(ts, ts[1:]))


@pytest.mark.parametrize("d", [0.5, 1.0, 2.9, 3.0, 4.16, 6.0, 15.0, 48.0])
def test_profile_state_consistent(d):
    """Интегрирование по фазам сходится с замкнутой формулой расстояния."""
    p = Profile.plan(0.0, d, V, A, J)
    assert p.total == pytest.approx(trip_time(d, V, A, J), rel=1e-9)
    x_end, v_end = p.state(p.total)
    assert x_end == pytest.approx(d, abs=1e-6)
    assert v_end == pytest.approx(0.0, abs=1e-6)
    # пиковая скорость достигается к началу торможения и не превышает номинал
    x_mid, v_mid = p.state(p.decel_start_time())
    assert v_mid == pytest.approx(p.v_pk, abs=1e-6)
    assert p.v_pk <= V + 1e-9


def test_downward_profile_symmetric():
    p = Profile.plan(21.0, 3.0, V, A, J)
    x_half, v_half = p.state(p.total / 2)
    assert 3.0 <= x_half <= 21.0
    assert v_half < 0  # движение вниз
    assert p.state(p.total)[0] == pytest.approx(3.0, abs=1e-6)


def test_s_curve_penalty_vs_trapezoid():
    """S-кривая добавляет ровно a/j к трапецеидальному времени (режим A)."""
    d = 30.0
    trapezoid = d / V + V / A
    assert trip_time(d, V, A, J) - trapezoid == pytest.approx(A / J)
