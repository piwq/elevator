"""Верификация генератора пассажиров (DESIGN.md §4.2):
экспоненциальность межгрупповых интервалов (K-S), средний размер группы,
соответствие фактического спроса заданному проценту населения."""

import math
import random

from sim.building import Building
from sim.passengers import PassengerGenerator
from sim.traffic import constant_profile


def make_gen(seed=42, percent=6.0, mean_batch=1.2):
    b = Building()
    prof = constant_profile(percent, 0.4, 0.4, 0.2, mean_batch=mean_batch)
    return b, prof, PassengerGenerator(b, prof, random.Random(seed))


def test_interarrival_exponential_ks():
    """K-S тест: межгрупповые интервалы ~ Exp(lambda), alpha=0.01."""
    b, prof, gen = make_gen()
    times = [t for t, _ in gen.batches(0.0, 48 * 3600.0)]
    gaps = sorted(t2 - t1 for t1, t2 in zip(times, times[1:]))
    n = len(gaps)
    assert n > 2000
    lam = 1.0 / (sum(gaps) / n)
    d_stat = max(
        max((i + 1) / n - (1 - math.exp(-lam * g)),
            (1 - math.exp(-lam * g)) - i / n)
        for i, g in enumerate(gaps)
    )
    assert d_stat < 1.63 / math.sqrt(n)  # критическое значение K-S, alpha=0.01


def test_mean_batch_size():
    b, prof, gen = make_gen()
    sizes = [len(batch) for _, batch in gen.batches(0.0, 48 * 3600.0)]
    mean = sum(sizes) / len(sizes)
    assert abs(mean - 1.2) < 0.03
    assert min(sizes) >= 1


def test_demand_matches_percent_population():
    """Фактический поток пассажиров == заданным 6% населения за 5 минут."""
    b, prof, gen = make_gen()
    horizon = 24 * 3600.0
    n_pass = sum(len(batch) for _, batch in gen.batches(0.0, horizon))
    expected = 0.06 * b.total_population * (horizon / 300.0)
    assert abs(n_pass - expected) / expected < 0.05


def test_od_structure():
    """Происхождение/назначение соответствуют миксу: вход с холла, выход в холл."""
    b, prof, gen = make_gen()
    counts = {"in": 0, "out": 0, "inter": 0}
    total = 0
    for _, batch in gen.batches(0.0, 24 * 3600.0):
        for p in batch:
            assert p.origin != p.destination
            total += 1
            if p.origin == 0:
                counts["in"] += 1
            elif p.destination == 0:
                counts["out"] += 1
            else:
                counts["inter"] += 1
    assert abs(counts["in"] / total - 0.4) < 0.03
    assert abs(counts["out"] / total - 0.4) < 0.03
    assert abs(counts["inter"] / total - 0.2) < 0.03
