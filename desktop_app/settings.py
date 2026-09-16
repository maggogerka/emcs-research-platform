"""Validated application settings and named QSettings profiles."""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
import json
from typing import Any, TypeVar

from PySide6.QtCore import QSettings


@dataclass(slots=True)
class AlgorithmSettings:
    on_coefficient: float = 6.0
    off_coefficient: float = 3.0
    motion_gyro_dps: float = 20.0
    motion_accel_delta_g: float = 0.25

    def validate(self) -> None:
        if not 1.0 <= self.on_coefficient <= 20.0:
            raise ValueError("Ton coefficient must be in [1, 20]")
        if not 0.5 <= self.off_coefficient < self.on_coefficient:
            raise ValueError("Toff coefficient must be in [0.5, Ton)")
        if not 1.0 <= self.motion_gyro_dps <= 500.0:
            raise ValueError("gyro threshold must be in [1, 500] deg/s")
        if not 0.01 <= self.motion_accel_delta_g <= 5.0:
            raise ValueError("acceleration threshold must be in [0.01, 5] g")


@dataclass(slots=True)
class ControlSettings:
    x_axis: str = "gz"
    y_axis: str = "gy"
    swap_axes: bool = False
    invert_x: bool = False
    invert_y: bool = False
    sensitivity: float = 12.0
    dead_zone_dps: float = 1.0
    smoothing: float = 0.35
    max_speed_px_s: float = 1200.0
    detector: str = "adaptive"
    action: str = "LMB"
    custom_key: str = ""

    def validate(self) -> None:
        valid_axes = {"gx", "gy", "gz"}
        if self.x_axis not in valid_axes or self.y_axis not in valid_axes:
            raise ValueError("control axes must be gx, gy or gz")
        if not 0.1 <= self.sensitivity <= 100.0:
            raise ValueError("sensitivity must be in [0.1, 100]")
        if not 0.0 <= self.dead_zone_dps <= 50.0:
            raise ValueError("dead zone must be in [0, 50] deg/s")
        if not 0.0 <= self.smoothing <= 0.95:
            raise ValueError("smoothing must be in [0, 0.95]")
        if not 10.0 <= self.max_speed_px_s <= 5000.0:
            raise ValueError("maximum speed must be in [10, 5000] px/s")
        if self.detector not in {"fixed", "adaptive"}:
            raise ValueError("detector must be fixed or adaptive")


TOOLTIPS = {
    "on_coefficient": "Ton multiplier; dimensionless; 1–20; default 6. Higher reduces sensitivity and false activations.",
    "off_coefficient": "Toff multiplier; dimensionless; 0.5–19 and below Ton; default 3. Higher prolongs active state.",
    "motion_gyro_dps": "Motion guard from gyro magnitude; deg/s; 1–500; default 20. Higher permits more motion during adaptation.",
    "motion_accel_delta_g": "Motion guard from |a|-1 g; g; 0.01–5; default 0.25. Higher permits stronger acceleration.",
    "repetitions": "Number of prescribed trials; 1–300; default 30. Higher increases session duration.",
    "prepare_s": "Preparation phase duration; seconds; 0.5–120; default 3. Longer gives more reaction time.",
    "contract_s": "Prescribed contraction cue duration; seconds; 0.5–120; default 2. This is not measured force.",
    "rest_s": "Rest phase duration; seconds; 0.5–120; default 3. Longer reduces fatigue.",
    "seed": "Randomization seed; integer; default 20260916. Same seed reproduces the prescribed order.",
    "sensitivity": "Pointer gain; px/(deg/s)/s; 0.1–100; default 12. Higher moves faster.",
    "dead_zone_dps": "Ignored gyro magnitude around zero; deg/s; 0–50; default 1. Higher suppresses drift but reduces fine control.",
    "smoothing": "Exponential smoothing weight; 0–0.95; default 0.35. Higher is steadier but adds lag.",
    "max_speed_px_s": "Pointer speed limit; px/s; 10–5000; default 1200. Higher allows faster movement.",
}


T = TypeVar("T")


def dataclass_from_dict(cls: type[T], values: dict[str, Any]) -> T:
    allowed = {field.name for field in fields(cls)}
    instance = cls(**{key: value for key, value in values.items() if key in allowed})
    validator = getattr(instance, "validate", None)
    if validator:
        validator()
    return instance


class SettingsStore:
    def __init__(self, settings: QSettings | None = None) -> None:
        self.settings = settings or QSettings("Felix Bembiev", "EMCS Research Platform")

    def save_profile(self, scope: str, name: str, value: object) -> None:
        if not name.strip():
            raise ValueError("profile name cannot be empty")
        payload = asdict(value) if hasattr(value, "__dataclass_fields__") else dict(value)
        self.settings.setValue(f"profiles/{scope}/{name.strip()}", json.dumps(payload))
        self.settings.sync()

    def load_profile(self, scope: str, name: str, cls: type[T]) -> T:
        raw = self.settings.value(f"profiles/{scope}/{name}")
        if raw is None:
            raise KeyError(name)
        return dataclass_from_dict(cls, json.loads(str(raw)))

    def profile_names(self, scope: str) -> list[str]:
        self.settings.beginGroup(f"profiles/{scope}")
        names = sorted(self.settings.childKeys())
        self.settings.endGroup()
        return names

    def remove_profile(self, scope: str, name: str) -> None:
        self.settings.remove(f"profiles/{scope}/{name}")
