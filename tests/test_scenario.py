"""Загрузка сценариев: схема, значения, защита от опечаток."""

import json
from pathlib import Path

import pytest

from sim.building import Building
from sim.scenario import load_scenario

SCENARIOS = Path(__file__).parent.parent / "scenarios"


def test_all_bundled_scenarios_load():
    files = list(SCENARIOS.glob("*.json"))
    assert len(files) >= 3
    for f in files:
        sc = load_scenario(f)
        assert sc.building.floors >= 2
        assert sc.car.rated_speed > 0
        assert 0 < sc.profile.peak_percent <= 30
        assert abs(sum(s.mix_in + s.mix_out + s.mix_inter
                       for s in sc.profile.slices) / len(sc.profile.slices) - 1.0) < 1e-9


def test_default_scenario_values():
    sc = load_scenario(SCENARIOS / "default.json")
    assert sc.name.startswith("Базовая")
    assert sc.building.floors == 17
    assert sc.car.rated_speed == 1.6
    assert sc.car.capacity == 8
    assert sc.profile.peak_percent == 6.0
    assert sc.start_hour == 6.0
    assert len(sc.profile.slices) == 5


def test_unknown_key_rejected(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"car": {"speeed": 2.0}}), encoding="utf-8")
    with pytest.raises(ValueError, match="speeed"):
        load_scenario(bad)


def test_population_per_floor(tmp_path):
    pops = [0, 10, 20, 30]
    f = tmp_path / "pop.json"
    f.write_text(json.dumps({
        "building": {"floors": 4, "population_per_floor": pops},
    }), encoding="utf-8")
    sc = load_scenario(f)
    assert sc.building.population == pops
    assert sc.building.total_population == 60


def test_population_length_mismatch():
    with pytest.raises(ValueError, match="population_per_floor"):
        Building(floors=5, population_per_floor=[0, 1, 2])


def test_mix_normalized(tmp_path):
    f = tmp_path / "mix.json"
    f.write_text(json.dumps({
        "demand": {"day_profile": [
            {"from_hour": 0, "intensity": 1.0, "in": 2, "out": 1, "interfloor": 1},
        ]},
    }), encoding="utf-8")
    sc = load_scenario(f)
    sl = sc.profile.slices[0]
    assert sl.mix_in == pytest.approx(0.5)
    assert sl.mix_out == pytest.approx(0.25)
    assert sl.mix_inter == pytest.approx(0.25)
