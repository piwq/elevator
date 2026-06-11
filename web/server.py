"""Realtime веб-визуализация симуляции.

Только стандартная библиотека: HTTP-сервер раздаёт страницу и стримит
состояние симуляции через Server-Sent Events (SSE). Симуляция крутится в
фоновом потоке с управляемым ускорением модельного времени.

Запуск:  python3 -m web.server [--port 8000] [--speed 30]
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from sim import Simulation
from sim.scenario import Scenario, load_scenario, scenario_from_dict

STATIC = Path(__file__).parent / "static"
SCENARIOS = Path(__file__).parent.parent / "scenarios"
RUNS_DIR = Path(__file__).parent.parent / "runs"
HORIZON_DAYS = 30


def make_sim(sc: Scenario, seed: int = 0, horizon_s: float = None) -> Simulation:
    t0 = sc.start_hour * 3600.0
    sim = Simulation(sc.building, sc.profile, sc.car, seed=seed, t_start=t0,
                     n_cars=sc.n_cars, patience=sc.patience,
                     dispatcher=sc.dispatcher)
    sim.start_stream(t0 + (horizon_s or HORIZON_DAYS * 86400.0))
    return sim


def total_energy_j(sim: Simulation, sc: Scenario) -> float:
    elapsed = sim.engine.now - sc.start_hour * 3600.0
    standby = sc.car.standby_w * len(sim.cars) * elapsed
    return sim.metrics.energy_j + standby


def build_report(sim: Simulation, sc: Scenario) -> dict:
    """Итоговый отчёт прогона: метрики, тепловая карта, энергия, вердикты."""
    t0 = sc.start_hour * 3600.0
    s = sim.metrics.summary()
    n = max(1, len(sim.metrics.completed))
    share_over_90 = sum(1 for p in sim.metrics.completed
                        if p.waiting_time() > 90.0) / n
    energy_kwh = total_energy_j(sim, sc) / 3.6e6
    hours = max(1e-9, (sim.engine.now - t0) / 3600.0)
    verdicts = {
        "awt_le_40": ("Среднее ожидание ≤ 40 с (ISO 8100-32, жилое)",
                      s.get("awt", 0) <= 40.0 if s.get("passengers") else None),
        "share90_le_10": ("Ожиданий дольше 90 с — не более 10% (CIBSE)",
                          share_over_90 <= 0.10 if s.get("passengers") else None),
        "no_abandoned": ("Никто не ушёл, не дождавшись лифта",
                         len(sim.metrics.abandoned) == 0),
    }
    return {
        "scenario": sc.name,
        "sim_hours": round(hours, 2),
        "summary": s,
        "share_over_90": round(share_over_90, 4),
        "heatmap": sim.metrics.heatmap(sim.building.floors),
        "hourly_awt": sim.metrics.hourly_awt(),
        "energy_kwh": round(energy_kwh, 3),
        "energy_kwh_per_day": round(energy_kwh / hours * 24.0, 2),
        "trips": len(sim.metrics.departures),
        "verdicts": verdicts,
    }


STATE_CODE = {"idle": 0, "moving": 1, "doors_opening": 2,
              "transfer": 3, "doors_closing": 4}
STATE_NAME = {v: k for k, v in STATE_CODE.items()}
HIST_STEP = 2.0  # шаг выборки истории, сек модельного времени
HIST_MAX = 43200  # ~сутки при шаге 2 с


def list_scenarios() -> list:
    out = []
    for p in sorted(SCENARIOS.glob("*.json")):
        try:
            out.append({"file": p.name, "name": load_scenario(p).name})
        except (ValueError, json.JSONDecodeError) as e:
            out.append({"file": p.name, "name": f"{p.name} (ошибка: {e})"})
    return out


class SimRunner(threading.Thread):
    def __init__(self, scenario_file: str, speed: float) -> None:
        super().__init__(daemon=True)
        self.lock = threading.Lock()
        self.scenario_file = scenario_file
        self.scenario_raw = json.loads(
            (SCENARIOS / scenario_file).read_text(encoding="utf-8"))
        self.scenario = scenario_from_dict(self.scenario_raw,
                                           Path(scenario_file).stem)
        self.sim = make_sim(self.scenario)
        self.speed = speed
        self.paused = False
        self.snapshot: dict = {}
        self._seed = 0
        self._last_summary: dict = {}
        self._summary_at = 0.0
        self.history: list = []  # выборки состояния для перемотки/траектории
        self._next_sample = 0.0

    def run(self) -> None:
        last = time.monotonic()
        while True:
            now = time.monotonic()
            dt, last = now - last, now
            with self.lock:
                if not self.paused:
                    target = self.sim.engine.now + dt * self.speed
                    self.sim.step_until(target)
                    self._sample_history()
                self.snapshot = self._make_snapshot()
            time.sleep(0.05)

    def _sample_history(self) -> None:
        t = self.sim.engine.now
        if t < self._next_sample:
            return
        self._next_sample = t + HIST_STEP
        ctrl = self.sim.controller
        self.history.append({
            "t": t,
            "cars": [[round(c.position(t), 2), STATE_CODE[c.state.value],
                      c.load(), int(c.out_of_service)]
                     for c in self.sim.cars],
            "queues": [[len(ctrl.waiting[f][1]), len(ctrl.waiting[f][-1])]
                       for f in range(self.sim.building.floors)],
        })
        if len(self.history) > HIST_MAX:
            del self.history[:HIST_MAX // 10]

    def control(self, cmd: dict) -> None:
        with self.lock:
            if "speed" in cmd:
                self.speed = max(0.1, min(600.0, float(cmd["speed"])))
            if "paused" in cmd:
                self.paused = bool(cmd["paused"])
            if "scenario" in cmd:
                file = Path(str(cmd["scenario"])).name  # без выхода из каталога
                raw = json.loads((SCENARIOS / file).read_text(encoding="utf-8"))
                self.scenario = scenario_from_dict(raw, Path(file).stem)
                self.scenario_raw = raw
                self.scenario_file = file
                cmd["reset"] = True
            if "params" in cmd:
                raw = dict(cmd["params"])
                self.scenario = scenario_from_dict(raw)  # валидация до применения
                self.scenario_raw = raw
                self.scenario_file = "(настроено вручную)"
                cmd["reset"] = True
            if "car_oos" in cmd:
                idx, val = cmd["car_oos"]
                self.sim.cars[int(idx)].set_out_of_service(bool(val))
            if cmd.get("reset"):
                self._seed += 1
                self.sim = make_sim(self.scenario, self._seed)
                self._last_summary, self._summary_at = {}, 0.0
                self.history, self._next_sample = [], 0.0

    def _make_snapshot(self) -> dict:
        sim, t = self.sim, self.sim.engine.now
        car, ctrl = sim.car, sim.controller
        if t - self._summary_at > 2.0 * self.speed:  # пересчёт метрик ~раз в 2 c
            self._last_summary = sim.metrics.summary()
            self._last_summary["wait_hist"] = self._wait_histogram()
            self._summary_at = t
        mix = sim.profile.mix(t)
        pop = sim.building.total_population
        rate_5min = sim.profile.passenger_rate(t, pop) * 300.0
        day_s = t % 86400.0
        return {
            "scenario": {
                "file": self.scenario_file,
                "name": self.scenario.name,
                "population": round(pop),
                "rated_speed": car.p.rated_speed,
                "acceleration": car.p.acceleration,
                "jerk": car.p.jerk,
                "peak_percent": sim.profile.peak_percent,
                "dispatcher": self.scenario.dispatcher,
            },
            "t": t,
            "clock": f"{int(day_s // 3600):02d}:{int(day_s % 3600 // 60):02d}:{int(day_s % 60):02d}",
            "speed": self.speed,
            "paused": self.paused,
            "floors": sim.building.floors,
            "floor_height": sim.building.floor_height,
            "cars": [self._car_state(c, t) for c in sim.cars],
            "queues": [[len(ctrl.waiting[f][1]), len(ctrl.waiting[f][-1])]
                       for f in range(sim.building.floors)],
            "demand": {
                "percent_5min": round(100.0 * rate_5min / pop, 2),
                "mix": [mix.mix_in, mix.mix_out, mix.mix_inter],
            },
            "totals": {
                "created": sim.generator._next_pid,
                "delivered": len(sim.metrics.completed),
                "waiting": ctrl.total_waiting(),
                "abandoned": len(sim.metrics.abandoned),
                "energy_kwh": round(
                    total_energy_j(sim, self.scenario) / 3.6e6, 3),
            },
            "hist_range": [self.history[0]["t"], self.history[-1]["t"]]
            if self.history else None,
            "metrics": self._last_summary,
            "recent": [
                {
                    "pid": p.pid,
                    "from": p.origin,
                    "to": p.destination,
                    "wait": round(p.waiting_time(), 1),
                    "transit": round(p.transit_time(), 1),
                    "ttd": round(p.time_to_destination(), 1),
                }
                for p in sim.metrics.completed[-12:][::-1]
            ],
        }

    def _car_state(self, c, t: float) -> dict:
        y, v, acc = c.kinematics_at(t)
        return {
            "y": y, "v": round(v, 3), "a": round(acc, 3),
            "state": c.state.value,
            "load": c.load(), "capacity": c.p.capacity,
            "direction": c.direction, "floor": c.floor, "target": c.target,
            "calls": sorted(c.car_calls),
            "oos": c.out_of_service,
        }

    # --- история, траектория, отчёт -------------------------------------------

    def history_at(self, t: float) -> dict:
        with self.lock:
            if not self.history:
                return {}
            lo, hi = 0, len(self.history) - 1
            while lo < hi:
                mid = (lo + hi) // 2
                if self.history[mid]["t"] < t:
                    lo = mid + 1
                else:
                    hi = mid
            sample = dict(self.history[lo])
        sample["state_names"] = STATE_NAME
        return sample

    def trajectory(self, max_points: int = 2400) -> dict:
        with self.lock:
            step = max(1, len(self.history) // max_points)
            pts = self.history[::step]
            return {
                "t": [p["t"] for p in pts],
                "cars": [[p["cars"][i][0] for p in pts]
                         for i in range(len(self.sim.cars))],
            }

    def report(self) -> dict:
        with self.lock:
            return build_report(self.sim, self.scenario)

    def _wait_histogram(self, bin_s: float = 10.0, n_bins: int = 12) -> dict:
        """Гистограмма ожиданий по корзинам bin_s секунд; последняя — переполнение."""
        bins = [0] * (n_bins + 1)
        for p in self.sim.metrics.completed:
            k = min(int(p.waiting_time() // bin_s), n_bins)
            bins[k] += 1
        return {"bin_s": bin_s, "bins": bins}

    def passengers_csv(self) -> str:
        rows = ["pid,origin,destination,t_created,t_board,t_alight,wait_s,transit_s,ttd_s"]
        with self.lock:
            completed = list(self.sim.metrics.completed)
        for p in completed:
            rows.append(f"{p.pid},{p.origin},{p.destination},"
                        f"{p.t_created:.2f},{p.t_board:.2f},{p.t_alight:.2f},"
                        f"{p.waiting_time():.2f},{p.transit_time():.2f},"
                        f"{p.time_to_destination():.2f}")
        return "\n".join(rows) + "\n"


class BackgroundRuns:
    """Фоновые прогоны на полной скорости: запуск, прогресс, отчёты на диске."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.jobs: dict = {}  # rid -> {name, status, progress, error}

    def start(self, raw: dict, name: str, hours: float, seed: int = 1) -> str:
        scenario_from_dict(dict(raw))  # валидация до старта потока
        rid = time.strftime("%Y%m%d-%H%M%S") + f"-{len(self.jobs)}"
        with self.lock:
            self.jobs[rid] = {"name": name, "status": "running", "progress": 0.0}
        threading.Thread(target=self._work, daemon=True,
                         args=(rid, dict(raw), name, hours, seed)).start()
        return rid

    def _work(self, rid: str, raw: dict, name: str, hours: float, seed: int) -> None:
        job = self.jobs[rid]
        try:
            sc = scenario_from_dict(raw, name)
            t0 = sc.start_hour * 3600.0
            t_end = t0 + hours * 3600.0
            sim = make_sim(sc, seed=seed, horizon_s=hours * 3600.0)
            step = (t_end - t0) / 200.0
            t = t0
            while t < t_end:
                t = min(t_end, t + step)
                sim.step_until(t)
                job["progress"] = (t - t0) / (t_end - t0)
            RUNS_DIR.mkdir(exist_ok=True)
            payload = {
                "meta": {"rid": rid, "name": name, "hours": hours,
                         "scenario": sc.name, "seed": seed,
                         "finished": time.strftime("%Y-%m-%d %H:%M:%S")},
                "scenario_raw": raw,
                "report": build_report(sim, sc),
            }
            (RUNS_DIR / f"{rid}.json").write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            job["status"] = "done"
        except Exception as e:  # отчёт об ошибке вместо тихой смерти потока
            job["status"] = "error"
            job["error"] = str(e)

    def list(self) -> list:
        out = []
        with self.lock:
            running = {rid: dict(j) for rid, j in self.jobs.items()
                       if j["status"] != "done"}
        for rid, j in running.items():
            out.append({"rid": rid, **j})
        if RUNS_DIR.is_dir():
            for p in sorted(RUNS_DIR.glob("*.json"), reverse=True):
                try:
                    meta = json.loads(p.read_text(encoding="utf-8"))["meta"]
                    out.append({"rid": p.stem, "status": "done",
                                "progress": 1.0, **meta})
                except (ValueError, KeyError):
                    continue
        return out

    def get(self, rid: str) -> dict:
        p = RUNS_DIR / (Path(rid).name + ".json")
        return json.loads(p.read_text(encoding="utf-8"))

    def delete(self, rid: str) -> None:
        (RUNS_DIR / (Path(rid).name + ".json")).unlink(missing_ok=True)
        with self.lock:
            self.jobs.pop(rid, None)


RUNNER: SimRunner = None
RUNS = BackgroundRuns()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:
        pass

    def _send_json(self, obj) -> None:
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        from urllib.parse import parse_qs, urlparse
        url = urlparse(self.path)
        if url.path == "/history":
            t = float(parse_qs(url.query).get("at", ["0"])[0])
            self._send_json(RUNNER.history_at(t))
            return
        if url.path == "/trajectory":
            self._send_json(RUNNER.trajectory())
            return
        if url.path == "/report":
            self._send_json(RUNNER.report())
            return
        if url.path == "/params":
            with RUNNER.lock:
                self._send_json({"file": RUNNER.scenario_file,
                                 "raw": RUNNER.scenario_raw})
            return
        if url.path == "/runs":
            self._send_json(RUNS.list())
            return
        if url.path == "/runs/get":
            rid = parse_qs(url.query).get("rid", [""])[0]
            try:
                self._send_json(RUNS.get(rid))
            except OSError:
                self.send_error(404)
            return
        if self.path in ("/", "/index.html"):
            body = (STATIC / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/passengers.csv":
            body = RUNNER.passengers_csv().encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition",
                             "attachment; filename=passengers.csv")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/scenarios":
            body = json.dumps(list_scenarios()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            try:
                while True:
                    with RUNNER.lock:
                        data = json.dumps(RUNNER.snapshot)
                    self.wfile.write(f"data: {data}\n\n".encode())
                    self.wfile.flush()
                    time.sleep(0.1)
            except (BrokenPipeError, ConnectionResetError):
                return
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        try:
            cmd = json.loads(self.rfile.read(length) or b"{}")
            if self.path == "/control":
                RUNNER.control(cmd)
            elif self.path == "/scenarios/save":
                name = Path(str(cmd["file"])).name
                if not name.endswith(".json"):
                    name += ".json"
                scenario_from_dict(dict(cmd["data"]))  # валидация до записи
                (SCENARIOS / name).write_text(
                    json.dumps(cmd["data"], ensure_ascii=False, indent=2),
                    encoding="utf-8")
            elif self.path == "/runs/start":
                raw = cmd.get("params") or RUNNER.scenario_raw
                RUNS.start(raw, str(cmd.get("name") or "прогон"),
                           max(0.1, min(168.0, float(cmd.get("hours", 24)))),
                           seed=int(cmd.get("seed", 1)))
            elif self.path == "/runs/delete":
                RUNS.delete(str(cmd["rid"]))
            else:
                self.send_error(404)
                return
        except (ValueError, KeyError, OSError, json.JSONDecodeError) as e:
            body = str(e).encode()
            self.send_response(400)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(204)
        self.end_headers()


def main() -> None:
    global RUNNER
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--speed", type=float, default=30.0,
                    help="ускорение модельного времени")
    ap.add_argument("--scenario", default="default.json",
                    help="файл из каталога scenarios/")
    args = ap.parse_args()
    RUNNER = SimRunner(args.scenario, speed=args.speed)
    RUNNER.start()
    server = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    print(f"Симуляция: http://localhost:{args.port}  (скорость x{args.speed})")
    server.serve_forever()


if __name__ == "__main__":
    main()
