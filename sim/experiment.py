"""Методология прогонов: реплики, warm-up, доверительные интервалы.

По ISO 8100-32 / Hakonen & Siikonen: сценарий >= 120 мин, первые ~15 мин
отбрасываются, несколько реплик с разными seed, отчёт с 95% ДИ
(docs/RESEARCH.md §6.2).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .building import Building
from .config import CarParams
from .simulation import Simulation
from .traffic import DemandProfile

# двусторонние квантили t-распределения, p=0.975
_T975 = {1: 12.71, 2: 4.30, 3: 3.18, 4: 2.78, 5: 2.57, 6: 2.45, 7: 2.36,
         8: 2.31, 9: 2.26, 10: 2.23, 14: 2.14, 19: 2.09, 29: 2.05}


def t_quantile(df: int) -> float:
    if df <= 0:
        return float("nan")
    keys = sorted(_T975)
    for k in keys:
        if df <= k:
            return _T975[k]
    return 1.96


@dataclass
class ExperimentResult:
    metric_means: dict
    metric_ci: dict  # полуширина 95% ДИ
    replications: int
    per_run: list = field(default_factory=list)

    def fmt(self) -> str:
        lines = [f"реплик: {self.replications}"]
        for k, m in self.metric_means.items():
            ci = self.metric_ci.get(k, 0.0)
            lines.append(f"  {k:>13}: {m:8.2f} ± {ci:.2f}")
        return "\n".join(lines)


def run_experiment(building: Building, profile: DemandProfile,
                   car_params: CarParams | None = None,
                   duration: float = 7200.0, warmup: float = 900.0,
                   cooldown: float = 300.0, replications: int = 10,
                   t_start: float = 0.0, base_seed: int = 1) -> ExperimentResult:
    keys = ("awt", "wait_median", "wait_p90", "wait_max",
            "transit_mean", "ttd_mean", "load_mean")
    runs = []
    for r in range(replications):
        sim = Simulation(building, profile, car_params,
                         seed=base_seed + r, t_start=t_start)
        sim.run(t_start + duration)
        s = sim.metrics.summary(t_start + warmup, t_start + duration - cooldown)
        if s["passengers"] > 0:
            runs.append(s)
    means, cis = {}, {}
    n = len(runs)
    for k in keys:
        xs = [run[k] for run in runs]
        m = sum(xs) / n
        means[k] = m
        if n > 1:
            sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))
            cis[k] = t_quantile(n - 1) * sd / math.sqrt(n)
        else:
            cis[k] = float("nan")
    means["passengers"] = sum(run["passengers"] for run in runs) / n
    cis["passengers"] = 0.0
    return ExperimentResult(means, cis, n, runs)
