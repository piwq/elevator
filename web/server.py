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

from sim import Building, CarParams, DemandProfile, RESIDENTIAL_DAY, Simulation

STATIC = Path(__file__).parent / "static"
START_HOUR = 6.0  # старт в начало утреннего пика
HORIZON_DAYS = 30


def make_sim(seed: int = 0) -> Simulation:
    building = Building()
    profile = DemandProfile(RESIDENTIAL_DAY, peak_percent=6.0)
    sim = Simulation(building, profile, CarParams(), seed=seed,
                     t_start=START_HOUR * 3600.0)
    sim.start_stream(START_HOUR * 3600.0 + HORIZON_DAYS * 86400.0)
    return sim


class SimRunner(threading.Thread):
    def __init__(self, speed: float) -> None:
        super().__init__(daemon=True)
        self.lock = threading.Lock()
        self.sim = make_sim()
        self.speed = speed
        self.paused = False
        self.snapshot: dict = {}
        self._seed = 0
        self._last_summary: dict = {}
        self._summary_at = 0.0

    def run(self) -> None:
        last = time.monotonic()
        while True:
            now = time.monotonic()
            dt, last = now - last, now
            with self.lock:
                if not self.paused:
                    target = self.sim.engine.now + dt * self.speed
                    self.sim.step_until(target)
                self.snapshot = self._make_snapshot()
            time.sleep(0.05)

    def control(self, cmd: dict) -> None:
        with self.lock:
            if "speed" in cmd:
                self.speed = max(0.1, min(600.0, float(cmd["speed"])))
            if "paused" in cmd:
                self.paused = bool(cmd["paused"])
            if cmd.get("reset"):
                self._seed += 1
                self.sim = make_sim(self._seed)
                self._last_summary, self._summary_at = {}, 0.0

    def _make_snapshot(self) -> dict:
        sim, t = self.sim, self.sim.engine.now
        car, ctrl = sim.car, sim.controller
        if t - self._summary_at > 2.0 * self.speed:  # пересчёт метрик ~раз в 2 c
            self._last_summary = sim.metrics.summary()
            self._summary_at = t
        mix = sim.profile.mix(t)
        pop = sim.building.total_population
        rate_5min = sim.profile.passenger_rate(t, pop) * 300.0
        day_s = t % 86400.0
        return {
            "t": t,
            "clock": f"{int(day_s // 3600):02d}:{int(day_s % 3600 // 60):02d}:{int(day_s % 60):02d}",
            "speed": self.speed,
            "paused": self.paused,
            "floors": sim.building.floors,
            "floor_height": sim.building.floor_height,
            "car": {
                "y": car.position(t),
                "state": car.state.value,
                "load": car.load(),
                "capacity": car.p.capacity,
                "direction": car.direction,
                "floor": car.floor,
                "target": car.target,
                "calls": sorted(car.car_calls),
            },
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
            },
            "metrics": self._last_summary,
        }


RUNNER: SimRunner = None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:
        pass

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            body = (STATIC / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
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
        if self.path == "/control":
            length = int(self.headers.get("Content-Length", 0))
            cmd = json.loads(self.rfile.read(length) or b"{}")
            RUNNER.control(cmd)
            self.send_response(204)
            self.end_headers()
        else:
            self.send_error(404)


def main() -> None:
    global RUNNER
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--speed", type=float, default=30.0,
                    help="ускорение модельного времени")
    args = ap.parse_args()
    RUNNER = SimRunner(speed=args.speed)
    RUNNER.start()
    server = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    print(f"Симуляция: http://localhost:{args.port}  (скорость x{args.speed})")
    server.serve_forever()


if __name__ == "__main__":
    main()
