"""Кабина: состояние, движение по S-кривой, цикл дверей, посадка/высадка.

Решения «куда ехать» делегируются контроллеру (sim/controller.py).
Перенацеливание в полёте разрешено консервативно: только на этаж, профиль
до которого совпадает с текущим до точки начала торможения (оба — режим A
с той же пиковой скоростью) и до наступления этой точки. Пропущенный этаж
обслуживается следующим проходом — так ведут себя и реальные контроллеры
(понятие committable floor).
"""

from __future__ import annotations

from enum import Enum

from .config import CarParams
from .kinematics import Profile


class CarState(Enum):
    IDLE = "idle"
    MOVING = "moving"
    DOORS_OPENING = "doors_opening"
    TRANSFER = "transfer"  # двери открыты: высадка/посадка/выдержка
    DOORS_CLOSING = "doors_closing"


class Car:
    def __init__(self, engine, building, params: CarParams, controller, metrics,
                 idx: int = 0) -> None:
        self.engine = engine
        self.building = building
        self.p = params
        self.controller = controller
        self.metrics = metrics
        self.idx = idx
        self.out_of_service = False  # вывод из обслуживания (ремонт)

        self.state = CarState.IDLE
        self.floor = params.home_floor  # текущий/последний этаж
        self.direction = 0  # +1 вверх, -1 вниз, 0 нет цели
        self.riders: list = []  # пассажиры в кабине
        self.car_calls: set = set()  # приказы (этажи назначения)

        self.profile: Profile | None = None
        self.t_depart = 0.0
        self.target: int | None = None

        self._phase_event = None
        self._home_event = None
        self._stop_was_hall = False

    # --- наблюдение состояния (для веба/метрик) -------------------------

    def position(self, t: float) -> float:
        return self.kinematics_at(t)[0]

    def kinematics_at(self, t: float) -> tuple:
        """(позиция м, скорость м/с, ускорение м/с²) в произвольный момент t."""
        if self.state == CarState.MOVING and self.profile is not None:
            return self.profile.state(t - self.t_depart)
        return self.building.height_of(self.floor), 0.0, 0.0

    def load(self) -> int:
        return len(self.riders)

    # --- внешние стимулы --------------------------------------------------

    def notify_call(self) -> None:
        """Контроллер сообщает: появился новый вызов/приказ."""
        if self.state == CarState.IDLE:
            self._cancel_home_timer()
            self._decide_next()
        elif self.state == CarState.MOVING:
            self._consider_retarget()

    # --- цикл состояния ---------------------------------------------------

    def set_out_of_service(self, value: bool) -> None:
        """Вывод из строя: развозит пассажиров в кабине, новых не берёт."""
        self.out_of_service = value
        self.controller.rebalance()
        if not value:
            self.notify_call()

    def _decide_next(self) -> None:
        """Стоим с закрытыми дверями — выбрать следующее действие."""
        nxt = self.controller.next_target(self)
        if nxt is None:
            self.direction = 0
            self.state = CarState.IDLE
            self.controller.on_idle(self)  # вызовы могут переназначить на нас
            if self.state != CarState.IDLE:
                return
            nxt = self.controller.next_target(self)
            if nxt is None:
                self._arm_home_timer()
                return
        if nxt == self.floor:
            self.direction = self.controller.service_direction(self, self.floor)
            self._open_doors()
            return
        self._depart(nxt)

    def _depart(self, target: int) -> None:
        self.direction = 1 if target > self.floor else -1
        self.target = target
        self.profile = Profile.plan(
            self.building.height_of(self.floor),
            self.building.height_of(target),
            self.p.rated_speed, self.p.acceleration, self.p.jerk,
        )
        self.t_depart = self.engine.now
        self.state = CarState.MOVING
        self.metrics.on_depart(self.engine.now, self)
        self.metrics.on_trip_energy(self._trip_energy(abs(
            self.building.height_of(target) - self.profile.origin)))
        self._phase_event = self.engine.schedule(self.profile.total, self._arrive)

    def _trip_energy(self, distance: float) -> float:
        """Энергия поездки, Дж. Упрощённая модель привода с противовесом:

        противовес уравновешивает кабину + counterweight_ratio номинала,
        мотор работает против остаточного дисбаланса с КПД drive_efficiency,
        рекуперация не учитывается; плюс фиксированная энергия старта.
        """
        imbalance_kg = abs(len(self.riders) * 75.0
                           - self.p.counterweight_ratio * self.p.capacity * 75.0)
        e_mech = imbalance_kg * 9.81 * distance
        return e_mech / self.p.drive_efficiency + self.p.start_energy_j

    def _consider_retarget(self) -> None:
        """Новый вызов в полёте: можно ли остановиться раньше текущей цели."""
        assert self.profile is not None and self.target is not None
        cand = self.controller.next_target(self, in_flight=True)
        if cand is None or cand == self.target:
            return
        between = (self.floor < cand < self.target) if self.direction > 0 \
            else (self.target < cand < self.floor)
        if not between:
            return
        new_profile = Profile.plan(
            self.profile.origin, self.building.height_of(cand),
            self.p.rated_speed, self.p.acceleration, self.p.jerk,
        )
        elapsed = self.engine.now - self.t_depart
        coincident = abs(new_profile.v_pk - self.profile.v_pk) < 1e-9
        if coincident and elapsed < new_profile.decel_start_time():
            self.engine.cancel(self._phase_event)
            self.target = cand
            self.profile = new_profile
            remaining = new_profile.total - elapsed
            self._phase_event = self.engine.schedule(remaining, self._arrive)

    def _arrive(self) -> None:
        assert self.target is not None
        self.floor = self.target
        self.target = None
        self.profile = None
        self._open_doors()

    def _open_doors(self) -> None:
        self.state = CarState.DOORS_OPENING
        self._stop_was_hall = False
        self._phase_event = self.engine.schedule(self.p.door_open, self._begin_transfer)

    def _begin_transfer(self) -> None:
        self.state = CarState.TRANSFER
        self._transfer_round()

    def _transfer_round(self) -> None:
        """Высадка и посадка; повторяется, пока есть кому войти/выйти."""
        t = self.engine.now
        out = [p for p in self.riders if p.destination == self.floor]
        for p in out:
            self.riders.remove(p)
        self.car_calls.discard(self.floor)

        boarded = self.controller.board(self, self.floor)
        if boarded:
            self._stop_was_hall = True
        n_moves = len(out) + len(boarded)

        for p in out:
            p.t_alight = t + len(out) * self.p.transfer_time  # высадка раньше посадки
            self.metrics.on_alight(p)
        for p in boarded:
            p.t_board = t + (len(out) + len(boarded)) * self.p.transfer_time
            self.riders.append(p)
            self.car_calls.add(p.destination)
            self.metrics.on_board(p)

        if n_moves > 0:
            dt = n_moves * self.p.transfer_time
            self._phase_event = self.engine.schedule(dt, self._transfer_round)
        else:
            dwell = self.p.dwell_hall if self._stop_was_hall else self.p.dwell_car
            self._phase_event = self.engine.schedule(dwell, self._close_doors)

    def _close_doors(self) -> None:
        self.state = CarState.DOORS_CLOSING
        self._phase_event = self.engine.schedule(self.p.door_close, self._decide_next)

    # --- парковка (home landing) -------------------------------------------

    def _arm_home_timer(self) -> None:
        if self.floor != self.controller.park_floor(self):
            self._home_event = self.engine.schedule(self.p.home_timeout, self._go_home)

    def _cancel_home_timer(self) -> None:
        if self._home_event is not None:
            self.engine.cancel(self._home_event)
            self._home_event = None

    def _go_home(self) -> None:
        if self.state == CarState.IDLE and self.controller.next_target(self) is None:
            target = self.controller.park_floor(self)
            if target != self.floor:
                self._depart(target)
