"""Timed experimental protocol used by the GUI."""

from __future__ import annotations

from dataclasses import dataclass
import time

from PySide6.QtCore import QObject, QTimer, Signal


@dataclass(frozen=True)
class Phase:
    name: str
    duration_s: float
    repetition: int = 0
    intensity: str = ""


class ExperimentController(QObject):
    phase_changed = Signal(dict)
    progress_changed = Signal(float)
    finished = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._phases = self._build_protocol()
        self._phase_index = -1
        self._phase_started = 0.0
        self._running = False
        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._tick)

    @staticmethod
    def _build_protocol() -> list[Phase]:
        phases = [Phase("baseline_rest", 10.0)]
        intensities = ("weak", "medium", "strong")
        for repetition in range(1, 31):
            intensity = intensities[(repetition - 1) % len(intensities)]
            phases.extend(
                [
                    Phase("prepare", 2.0, repetition, intensity),
                    Phase("contract", 2.0, repetition, intensity),
                    Phase("rest", 3.0, repetition, intensity),
                ]
            )
        return phases

    def start(self) -> None:
        self.stop(emit_finished=False)
        self._running = True
        self._phase_index = 0
        self._phase_started = time.monotonic()
        self._emit_phase()
        self._timer.start()

    def stop(self, *, emit_finished: bool = True) -> None:
        was_running = self._running
        self._running = False
        self._timer.stop()
        if was_running and emit_finished:
            self.finished.emit()

    def snapshot(self) -> dict[str, object]:
        if not self._running or self._phase_index < 0:
            return {"phase": "", "repetition": 0, "intensity": ""}
        phase = self._phases[self._phase_index]
        return {
            "phase": phase.name,
            "repetition": phase.repetition,
            "intensity": phase.intensity,
        }

    def _emit_phase(self) -> None:
        phase = self._phases[self._phase_index]
        self.phase_changed.emit(
            {
                "phase": phase.name,
                "duration_s": phase.duration_s,
                "repetition": phase.repetition,
                "intensity": phase.intensity,
            }
        )

    def _tick(self) -> None:
        phase = self._phases[self._phase_index]
        elapsed = time.monotonic() - self._phase_started
        self.progress_changed.emit(min(elapsed / phase.duration_s, 1.0))
        if elapsed < phase.duration_s:
            return
        self._phase_index += 1
        if self._phase_index >= len(self._phases):
            self.stop()
            return
        self._phase_started = time.monotonic()
        self._emit_phase()
