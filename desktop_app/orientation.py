"""Lightweight accel/gyro orientation estimate and QPainter 3D view."""

from __future__ import annotations

import math
from typing import Mapping

from PySide6.QtCore import QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget


MOUNT_ORIENTATIONS = ("default", "x_up", "x_down", "y_up", "y_down", "z_down")


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

    def __init__(self) -> None:
        super().__init__()
        self.filter = ComplementaryOrientation()
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
        self.canvas = OrientationCanvas()
        layout.addWidget(self.canvas, 1)
        self.reset_button.clicked.connect(self.reset)
        self.enabled_box.toggled.connect(self.enabled_changed)
        self.timer = QTimer(self)
        self.timer.setInterval(33)
        self.timer.timeout.connect(self._render)
        self.timer.start()

    def reset(self) -> None:
        self.filter.reset()
        self.latest_q = tuple(self.filter.q)

    def update_sample(self, sample: Mapping[str, float], dt_s: float) -> None:
        if self.enabled_box.isChecked():
            transformed = mount_sample(sample, self.mount_combo.currentText())
            self.latest_q = self.filter.update(transformed, dt_s)

    def _render(self) -> None:
        if not self.enabled_box.isChecked():
            return
        self.canvas.set_quaternion(self.latest_q)
        roll, pitch, yaw = self.filter.euler_degrees()
        self.euler_label.setText(f"roll {roll:.1f}°, pitch {pitch:.1f}°, relative yaw {yaw:.1f}°")
