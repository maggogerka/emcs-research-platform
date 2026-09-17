"""Balanced, pauseable experiment scheduling driven by device calibration events."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import random
import time
from typing import Callable

from PySide6.QtCore import QObject, QTimer, Signal


INTENSITIES = ("weak", "medium", "strong")
TERMINAL_OUTCOMES = (
    "completed",
    "aborted_by_user",
    "lead_off",
    "hardware_error",
)


@dataclass(frozen=True, slots=True)
class ProtocolConfig:
    repetitions: int = 30
    prepare_s: float = 3.0
    contract_s: float = 2.0
    rest_s: float = 3.0
    seed: int = 20260916

    def validate(self) -> None:
        if not 1 <= self.repetitions <= 300:
            raise ValueError("repetitions must be between 1 and 300")
        for name in ("prepare_s", "contract_s", "rest_s"):
            if not 0.5 <= getattr(self, name) <= 120.0:
                raise ValueError(f"{name} must be between 0.5 and 120 seconds")


@dataclass(frozen=True, slots=True)
class Phase:
    name: str
    duration_s: float
    trial: int
    prescribed_intensity: str

    def as_dict(self) -> dict[str, object]:
        values = asdict(self)
        values["phase"] = values.pop("name")
        return values


class ExperimentSchedule:
    """Immutable balanced random protocol; intensity is prescribed, not measured."""

    def __init__(self, config: ProtocolConfig) -> None:
        config.validate()
        self.config = config
        labels = [INTENSITIES[index % len(INTENSITIES)] for index in range(config.repetitions)]
        random.Random(config.seed).shuffle(labels)
        self.intensities = tuple(labels)
        phases: list[Phase] = []
        for trial, intensity in enumerate(labels, start=1):
            phases.extend(
                (
                    Phase("prepare", config.prepare_s, trial, intensity),
                    Phase("contract", config.contract_s, trial, intensity),
                    Phase("rest", config.rest_s, trial, intensity),
                )
            )
        self.phases = tuple(phases)

    @property
    def duration_s(self) -> float:
        return sum(phase.duration_s for phase in self.phases)

    def intensity_counts(self) -> dict[str, int]:
        return {name: self.intensities.count(name) for name in INTENSITIES}


class ExperimentController(QObject):
    """Qt timer wrapper that cannot enter a contraction before device calibration."""

    phase_changed = Signal(dict)
    progress_changed = Signal(dict)
    finished = Signal(str)

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(parent)
        self._clock = clock
        self._schedule: ExperimentSchedule | None = None
        self._phase_index = -1
        self._phase_started = 0.0
        self._paused_elapsed = 0.0
        self._state = "idle"
        self._outcome = ""
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._tick)

    @property
    def state(self) -> str:
        return self._state

    @property
    def schedule(self) -> ExperimentSchedule | None:
        return self._schedule

    def arm(self, config: ProtocolConfig) -> ExperimentSchedule:
        if self._state not in {"idle", "finished"}:
            raise RuntimeError("experiment is already active")
        self._schedule = ExperimentSchedule(config)
        self._phase_index = -1
        self._state = "waiting_calibration"
        self._outcome = ""
        self._paused_elapsed = 0.0
        self.phase_changed.emit(
            {
                "phase": "calibration_rest",
                "duration_s": 10.0,
                "trial": 0,
                "prescribed_intensity": "",
                "waiting_for_device": True,
            }
        )
        self._timer.start()
        return self._schedule

    def calibration_done(self) -> bool:
        if self._state != "waiting_calibration" or self._schedule is None:
            return False
        self._state = "running"
        self._phase_index = 0
        self._phase_started = self._clock()
        self._emit_phase()
        return True

    def pause(self) -> bool:
        if self._state != "running":
            return False
        self._paused_elapsed = self._clock() - self._phase_started
        self._state = "paused"
        self.phase_changed.emit({**self.snapshot(), "phase": "paused"})
        return True

    def resume(self) -> bool:
        if self._state != "paused":
            return False
        self._phase_started = self._clock() - self._paused_elapsed
        self._state = "running"
        self._emit_phase()
        return True

    def stop(self, outcome: str = "aborted_by_user", *, emit_finished: bool = True) -> None:
        if outcome not in TERMINAL_OUTCOMES:
            raise ValueError(f"unknown experiment outcome: {outcome}")
        was_active = self._state in {"waiting_calibration", "running", "paused"}
        self._timer.stop()
        self._state = "finished"
        self._outcome = outcome
        if was_active and emit_finished:
            self.finished.emit(outcome)

    def reset(self) -> None:
        self._timer.stop()
        self._schedule = None
        self._phase_index = -1
        self._state = "idle"
        self._outcome = ""

    def snapshot(self) -> dict[str, object]:
        result: dict[str, object] = {
            "state": self._state,
            "outcome": self._outcome,
            "phase": "",
            "trial": 0,
            "prescribed_intensity": "",
            "remaining_s": 0.0,
            "overall_progress": 0.0,
        }
        if self._state == "waiting_calibration":
            result["phase"] = "calibration_rest"
            return result
        if (
            self._schedule is None
            or self._phase_index < 0
            or self._phase_index >= len(self._schedule.phases)
        ):
            return result
        phase = self._schedule.phases[self._phase_index]
        elapsed = (
            self._paused_elapsed
            if self._state == "paused"
            else max(0.0, self._clock() - self._phase_started)
        )
        completed = sum(item.duration_s for item in self._schedule.phases[: self._phase_index])
        result.update(
            {
                "phase": phase.name,
                "trial": phase.trial,
                "prescribed_intensity": phase.prescribed_intensity,
                "remaining_s": max(0.0, phase.duration_s - elapsed),
                "phase_progress": min(1.0, elapsed / phase.duration_s),
                "overall_progress": min(
                    1.0, (completed + min(elapsed, phase.duration_s)) / self._schedule.duration_s
                ),
            }
        )
        return result

    def _emit_phase(self) -> None:
        if self._schedule is None or self._phase_index < 0:
            return
        phase = self._schedule.phases[self._phase_index]
        self.phase_changed.emit({**phase.as_dict(), "waiting_for_device": False})

    def _tick(self) -> None:
        if self._state == "waiting_calibration":
            self.progress_changed.emit(self.snapshot())
            return
        if self._state != "running" or self._schedule is None:
            return
        snapshot = self.snapshot()
        self.progress_changed.emit(snapshot)
        phase = self._schedule.phases[self._phase_index]
        if self._clock() - self._phase_started < phase.duration_s:
            return
        if self._phase_index + 1 >= len(self._schedule.phases):
            self.stop("completed")
            return
        self._phase_index += 1
        self._phase_started = self._clock()
        self._emit_phase()
