"""Lightweight accel/gyro orientation estimate and QPainter 3D view."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping

from PySide6.QtCore import QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


MOUNT_ORIENTATIONS = ("default", "x_up", "x_down", "y_up", "y_down", "z_down")

CALIBRATION_POSES = (
    ("level", "Положите модуль ровно, верхней стороной вверх."),
    ("left", "Поверните модуль на левый бок и удерживайте неподвижно."),
    ("right", "Поверните модуль на правый бок и удерживайте неподвижно."),
    ("nose_down", "Наклоните переднюю часть модуля вниз и удерживайте."),
    ("nose_up", "Наклоните переднюю часть модуля вверх и удерживайте."),
    ("upside_down", "Переверните модуль верхней стороной вниз."),
    ("reference", "Верните модуль в ровное положение — это будет нулевой наклон."),
)


def mount_sample(sample: Mapping[str, float], mounting: str) -> dict[str, float]:
    values = [float(sample[key]) for key in ("ax", "ay", "az", "gx", "gy", "gz")]
    a = values[:3]
    g = values[3:]
    transforms = {
        "default": lambda v: (v[0], v[1], v[2]),
        "x_up": lambda v: (v[2], v[1], -v[0]),
        "x_down": lambda v: (-v[2], v[1], v[0]),
        "y_up": lambda v: (v[0], v[2], -v[1]),
        "y_down": lambda v: (v[0], -v[2], v[1]),
        "z_down": lambda v: (v[0], -v[1], -v[2]),
    }
    transform = transforms.get(mounting, transforms["default"])
    ax, ay, az = transform(a)
    gx, gy, gz = transform(g)
    return dict(zip(("ax", "ay", "az", "gx", "gy", "gz"), (ax, ay, az, gx, gy, gz)))


@dataclass(frozen=True, slots=True)
class OrientationCalibration:
    accel_bias: tuple[float, float, float]
    accel_scale: tuple[float, float, float]
    gyro_bias: tuple[float, float, float]

    def apply(self, sample: Mapping[str, float]) -> dict[str, float]:
        result: dict[str, float] = {}
        for index, axis in enumerate(("x", "y", "z")):
            result[f"a{axis}"] = (
                float(sample[f"a{axis}"]) - self.accel_bias[index]
            ) * self.accel_scale[index]
            result[f"g{axis}"] = float(sample[f"g{axis}"]) - self.gyro_bias[index]
        return result


class SixPoseCalibration:
    """Stationary six-face accelerometer calibration plus gyro residual bias."""

    sample_count = 80

    def __init__(self) -> None:
        self.pose_index = 0
        self.measurements: dict[str, dict[str, float]] = {}
        self.result: OrientationCalibration | None = None

    @property
    def active(self) -> bool:
        return self.pose_index < len(CALIBRATION_POSES)

    @property
    def instruction(self) -> str:
        return CALIBRATION_POSES[self.pose_index][1] if self.active else "Калибровка завершена."

    def reset(self) -> None:
        self.pose_index = 0
        self.measurements.clear()
        self.result = None

    @staticmethod
    def _mean(samples: list[Mapping[str, float]], name: str) -> float:
        return sum(float(sample[name]) for sample in samples) / len(samples)

    def capture(self, samples: list[Mapping[str, float]]) -> OrientationCalibration | None:
        if not self.active:
            return self.result
        if len(samples) < self.sample_count:
            raise ValueError("Недостаточно отсчётов; удерживайте модуль неподвижно.")
        names = ("ax", "ay", "az", "gx", "gy", "gz")
        means = {name: self._mean(samples, name) for name in names}
        standard_deviations = {
            name: math.sqrt(
                sum((float(sample[name]) - means[name]) ** 2 for sample in samples)
                / len(samples)
            )
            for name in names
        }
        accel_norm = math.sqrt(sum(means[name] ** 2 for name in ("ax", "ay", "az")))
        gyro_norm = math.sqrt(sum(means[name] ** 2 for name in ("gx", "gy", "gz")))
        if not 0.65 <= accel_norm <= 1.35:
            raise ValueError("Положение нестабильно: модуль должен быть неподвижен под действием 1 g.")
        if max(standard_deviations[name] for name in ("ax", "ay", "az")) > 0.055:
            raise ValueError("Модуль двигался во время захвата. Повторите положение.")
        if gyro_norm > 8.0 or max(standard_deviations[name] for name in ("gx", "gy", "gz")) > 2.0:
            raise ValueError("Вращение ещё не остановилось. Удерживайте модуль и повторите.")

        pose = CALIBRATION_POSES[self.pose_index][0]
        self.measurements[pose] = means
        self.pose_index += 1
        if self.active:
            return None
        try:
            self.result = self._solve()
        except ValueError:
            self.reset()
            raise
        return self.result

    def _solve(self) -> OrientationCalibration:
        pairs = (("left", "right", "ax"), ("nose_down", "nose_up", "ay"),
                 ("level", "upside_down", "az"))
        accel_bias: list[float] = []
        accel_scale: list[float] = []
        for first, second, axis in pairs:
            a = self.measurements[first][axis]
            b = self.measurements[second][axis]
            span = abs(a - b)
            if span < 1.25:
                raise ValueError(
                    f"Пара положений {first}/{second} недостаточно различается по {axis}. "
                    "Проверьте Mounting и повторите калибровку."
                )
            accel_bias.append((a + b) / 2.0)
            accel_scale.append(2.0 / span)
        gyro_bias = tuple(
            sum(values[f"g{axis}"] for values in self.measurements.values())
            / len(self.measurements)
            for axis in ("x", "y", "z")
        )
        result = OrientationCalibration(tuple(accel_bias), tuple(accel_scale), gyro_bias)
        reference = result.apply(self.measurements["reference"])
        if reference["az"] < 0.65 or abs(reference["ax"]) > 0.35 or abs(reference["ay"]) > 0.35:
            raise ValueError(
                "Нулевое положение не распознано как ровное. Проверьте Mounting и повторите."
            )
        return result


class ComplementaryOrientation:
    """Quaternion complementary filter; yaw is relative and will drift without a magnetometer."""

    def __init__(self, accel_gain: float = 1.8) -> None:
        self.accel_gain = accel_gain
        self.q = [1.0, 0.0, 0.0, 0.0]

    def reset(self) -> None:
        self.q[:] = [1.0, 0.0, 0.0, 0.0]

    def update(self, sample: Mapping[str, float], dt_s: float) -> tuple[float, float, float, float]:
        dt_s = max(0.001, min(float(dt_s), 0.05))
        ax, ay, az = (float(sample[key]) for key in ("ax", "ay", "az"))
        gx, gy, gz = (math.radians(float(sample[key])) for key in ("gx", "gy", "gz"))
        norm = math.sqrt(ax * ax + ay * ay + az * az)
        if 0.5 < norm < 1.5:
            ax, ay, az = ax / norm, ay / norm, az / norm
            w, x, y, z = self.q
            estimated = (
                2.0 * (x * z - w * y),
                2.0 * (w * x + y * z),
                w * w - x * x - y * y + z * z,
            )
            ex = ay * estimated[2] - az * estimated[1]
            ey = az * estimated[0] - ax * estimated[2]
            ez = ax * estimated[1] - ay * estimated[0]
            gx += self.accel_gain * ex
            gy += self.accel_gain * ey
            gz += self.accel_gain * ez
        w, x, y, z = self.q
        half_dt = 0.5 * dt_s
        self.q = [
            w + (-x * gx - y * gy - z * gz) * half_dt,
            x + (w * gx + y * gz - z * gy) * half_dt,
            y + (w * gy - x * gz + z * gx) * half_dt,
            z + (w * gz + x * gy - y * gx) * half_dt,
        ]
        q_norm = math.sqrt(sum(value * value for value in self.q)) or 1.0
        self.q = [value / q_norm for value in self.q]
        return tuple(self.q)

    def euler_degrees(self) -> tuple[float, float, float]:
        w, x, y, z = self.q
        roll = math.atan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y))
        pitch = math.asin(max(-1.0, min(1.0, 2 * (w * y - z * x))))
        yaw = math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))
        return tuple(math.degrees(value) for value in (roll, pitch, yaw))


class OrientationCanvas(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.q = (1.0, 0.0, 0.0, 0.0)
        self.setMinimumSize(420, 320)

    def set_quaternion(self, quaternion: tuple[float, float, float, float]) -> None:
        self.q = quaternion
        self.update()

    def _rotate(self, point: tuple[float, float, float]) -> tuple[float, float, float]:
        w, x, y, z = self.q
        matrix = (
            (1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)),
            (2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)),
            (2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)),
        )
        return tuple(sum(matrix[row][column] * point[column] for column in range(3)) for row in range(3))

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#11171d"))
        scale = min(self.width(), self.height()) * 0.26
        center = QPointF(self.width() / 2, self.height() / 2)
        vertices = [(x, y, z) for x in (-1.5, 1.5) for y in (-0.75, 0.75) for z in (-0.25, 0.25)]
        projected: list[QPointF] = []
        depths: list[float] = []
        for vertex in vertices:
            rx, ry, rz = self._rotate(vertex)
            perspective = 1.0 / max(0.5, 3.5 - rz)
            projected.append(QPointF(center.x() + rx * scale * perspective, center.y() - ry * scale * perspective))
            depths.append(rz)
        faces = ((0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3))
        colors = ("#1565c0", "#42a5f5", "#2e7d32", "#66bb6a", "#ef6c00", "#ffb74d")
        for face, color in sorted(zip(faces, colors), key=lambda item: sum(depths[i] for i in item[0])):
            painter.setBrush(QColor(color))
            painter.setPen(QPen(QColor("#e8eef4"), 1.2))
            painter.drawPolygon(QPolygonF([projected[index] for index in face]))


class OrientationPanel(QWidget):
    enabled_changed = Signal(bool)
    device_calibration_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.filter = ComplementaryOrientation()
        self.calibrator = SixPoseCalibration()
        self.calibration: OrientationCalibration | None = None
        self._capture_samples: list[dict[str, float]] = []
        self._capturing = False
        self.latest_q = tuple(self.filter.q)
        self.enabled_box = QCheckBox("Enable 3D at 30 FPS")
        self.enabled_box.setChecked(True)
        self.mount_combo = QComboBox()
        self.mount_combo.addItems(MOUNT_ORIENTATIONS)
        self.reset_button = QPushButton("Reset orientation")
        self.euler_label = QLabel("roll 0.0°, pitch 0.0°, relative yaw 0.0°")
        warning = QLabel("Yaw is relative and drifts because MPU6050 has no magnetometer.")
        warning.setStyleSheet("color: #ffb74d")
        controls = QHBoxLayout()
        controls.addWidget(self.enabled_box)
        controls.addWidget(QLabel("Mounting:"))
        controls.addWidget(self.mount_combo)
        controls.addWidget(self.reset_button)
        controls.addStretch()
        layout = QVBoxLayout(self)
        layout.addLayout(controls)
        layout.addWidget(warning)
        layout.addWidget(self.euler_label)

        calibration_box = QGroupBox("Пошаговая калибровка MPU6050")
        calibration_layout = QVBoxLayout(calibration_box)
        self.calibration_instruction = QLabel(
            "Нажмите «Начать калибровку». Для каждого положения дождитесь неподвижности и нажмите «Захватить»."
        )
        self.calibration_instruction.setWordWrap(True)
        self.calibration_status = QLabel("Не выполнена")
        self.calibration_progress = QProgressBar()
        self.calibration_progress.setRange(0, len(CALIBRATION_POSES))
        calibration_buttons = QHBoxLayout()
        self.calibration_start = QPushButton("Начать / повторить калибровку")
        self.calibration_capture = QPushButton("Захватить положение")
        self.calibration_capture.setEnabled(False)
        calibration_buttons.addWidget(self.calibration_start)
        calibration_buttons.addWidget(self.calibration_capture)
        calibration_buttons.addStretch()
        calibration_layout.addWidget(self.calibration_instruction)
        calibration_layout.addWidget(self.calibration_status)
        calibration_layout.addWidget(self.calibration_progress)
        calibration_layout.addLayout(calibration_buttons)
        layout.addWidget(calibration_box)

        self.canvas = OrientationCanvas()
        layout.addWidget(self.canvas, 1)
        self.reset_button.clicked.connect(self.reset)
        self.calibration_start.clicked.connect(self.start_calibration)
        self.calibration_capture.clicked.connect(self.capture_pose)
        self.mount_combo.currentTextChanged.connect(self._mount_changed)
        self.enabled_box.toggled.connect(self.enabled_changed)
        self.timer = QTimer(self)
        self.timer.setInterval(33)
        self.timer.timeout.connect(self._render)
        self.timer.start()

    def reset(self) -> None:
        self.filter.reset()
        self.latest_q = tuple(self.filter.q)

    def start_calibration(self) -> None:
        self.calibrator.reset()
        self.calibration = None
        self._capturing = False
        self._capture_samples.clear()
        self.calibration_progress.setValue(0)
        self.calibration_status.setText("Шаг 1 из 7")
        self.calibration_instruction.setText(self.calibrator.instruction)
        self.calibration_capture.setEnabled(True)
        self.device_calibration_requested.emit()
        self.reset()

    def capture_pose(self) -> None:
        if not self.calibrator.active or self._capturing:
            return
        self._capture_samples.clear()
        self._capturing = True
        self.calibration_capture.setEnabled(False)
        self.calibration_status.setText("Захват: 0% — не двигайте модуль")

    def _mount_changed(self) -> None:
        if self.calibration is not None or self.calibrator.pose_index:
            self.calibration = None
            self.calibrator.reset()
            self._capturing = False
            self.calibration_capture.setEnabled(False)
            self.calibration_progress.setValue(0)
            self.calibration_status.setText("Mounting изменён — выполните калибровку заново")
        self.reset()

    def update_sample(self, sample: Mapping[str, float], dt_s: float) -> None:
        if self.enabled_box.isChecked():
            transformed = mount_sample(sample, self.mount_combo.currentText())
            if self._capturing:
                self._capture_samples.append(transformed)
                count = len(self._capture_samples)
                self.calibration_status.setText(
                    f"Захват: {min(100, round(count * 100 / self.calibrator.sample_count))}% — не двигайте модуль"
                )
                if count >= self.calibrator.sample_count:
                    self._finish_pose_capture()
            corrected = self.calibration.apply(transformed) if self.calibration else transformed
            self.latest_q = self.filter.update(corrected, dt_s)

    def _finish_pose_capture(self) -> None:
        self._capturing = False
        try:
            result = self.calibrator.capture(self._capture_samples)
        except ValueError as error:
            self.calibration_status.setText(str(error))
            self.calibration_instruction.setText(self.calibrator.instruction)
            self.calibration_progress.setValue(self.calibrator.pose_index)
            self.calibration_capture.setEnabled(True)
            return
        completed = self.calibrator.pose_index
        self.calibration_progress.setValue(completed)
        if result is None:
            self.calibration_status.setText(
                f"Положение принято. Шаг {completed + 1} из {len(CALIBRATION_POSES)}"
            )
            self.calibration_instruction.setText(self.calibrator.instruction)
            self.calibration_capture.setEnabled(True)
            return
        self.calibration = result
        self.reset()
        bias = ", ".join(f"{value:+.4f}" for value in result.accel_bias)
        scale = ", ".join(f"{value:.4f}" for value in result.accel_scale)
        gyro = ", ".join(f"{value:+.3f}" for value in result.gyro_bias)
        self.calibration_instruction.setText(
            "Калибровка применена к 3D-модели. Оставьте модуль ровно для проверки нулевого положения."
        )
        self.calibration_status.setText(
            f"Готово · accel bias [{bias}] g · scale [{scale}] · gyro bias [{gyro}] °/s"
        )
        self.calibration_capture.setEnabled(False)

    def _render(self) -> None:
        if not self.enabled_box.isChecked():
            return
        self.canvas.set_quaternion(self.latest_q)
        roll, pitch, yaw = self.filter.euler_degrees()
        self.euler_label.setText(f"roll {roll:.1f}°, pitch {pitch:.1f}°, relative yaw {yaw:.1f}°")
