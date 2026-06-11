#!/usr/bin/env python3
"""Научный прогон: реплики с ДИ по методологии ISO 8100-32 (DESIGN.md §4.6).

Примеры:
  python3 run_experiment.py                      # базовый сценарий, вечерний пик
  python3 run_experiment.py --percent 6 --reps 20
"""

from __future__ import annotations

import argparse

from sim import Building, CarParams, run_experiment
from sim.traffic import constant_profile


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--floors", type=int, default=17)
    ap.add_argument("--percent", type=float, default=6.0,
                    help="спрос, %% населения за 5 мин (ISO residential: 6)")
    ap.add_argument("--mix", default="0.45,0.45,0.10",
                    help="доли in,out,interfloor")
    ap.add_argument("--reps", type=int, default=10)
    ap.add_argument("--minutes", type=float, default=120.0,
                    help="длительность сценария (ISO: >= 120 мин)")
    args = ap.parse_args()

    mi, mo, mx = (float(x) for x in args.mix.split(","))
    building = Building(floors=args.floors)
    profile = constant_profile(args.percent, mi, mo, mx)

    print(f"Здание: {args.floors} уровней, население {building.total_population:.0f} чел")
    print(f"Спрос: {args.percent}%/5 мин, микс in/out/inter = {mi}/{mo}/{mx}")
    print(f"Прогон: {args.minutes:.0f} мин x {args.reps} реплик, warm-up 15 мин\n")

    res = run_experiment(building, profile, CarParams(),
                         duration=args.minutes * 60.0,
                         replications=args.reps)
    print(res.fmt())
    awt = res.metric_means["awt"]
    verdict = "OK (ISO residential: AWT <= 40 c)" if awt <= 40 else \
        "ПРЕВЫШЕНИЕ критерия ISO (AWT > 40 c) — лифт не справляется"
    print(f"\nВердикт: {verdict}")


if __name__ == "__main__":
    main()
