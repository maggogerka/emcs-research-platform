"""Focused Qt panels used by the seven-tab main window."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from PySide6.QtCore import QTimer, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGridLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QProgressBar,
    QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from .experiment import ProtocolConfig
from .settings import AlgorithmSettings, ControlSettings, SettingsStore, TOOLTIPS


def _spin(minimum: float, maximum: float, value: float, tooltip: str) -> QDoubleSpinBox:
    widget = QDoubleSpinBox()
    widget.setRange(minimum, maximum)
    widget.setDecimals(2)
    widget.setValue(value)
    widget.setToolTip(tooltip)
    return widget


class StatusStrip(QGroupBox):
    def __init__(self) -> None:
        super().__init__("Persistent safety and data status")
        layout = QGridLayout(self)
        self.labels: dict[str, QLabel] = {}
        entries = (
            ("esp", "ESP"), ("ads", "ADS1115"), ("mpu", "MPU6050"),
            ("electrodes", "Electrodes"), ("calibration", "Calibration"),
            ("crc", "CRC"), ("gaps", "Gaps"),
        )
        for column, (key, title) in enumerate(entries):
            layout.addWidget(QLabel(title), 0, column)
            value = QLabel("unknown")
            value.setStyleSheet("padding:4px;color:#ffb74d")
            layout.addWidget(value, 1, column)
            self.labels[key] = value

    def set_value(self, key: str, text: str, ok: bool | None = None) -> None:
        self.labels[key].setText(text)
        color = "#66bb6a" if ok is True else "#ef5350" if ok is False else "#ffb74d"
        self.labels[key].setStyleSheet(f"padding:4px;color:{color}")


class ExperimentPanel(QWidget):
    start_requested = Signal(object, str, str)
    pause_requested = Signal()
    abort_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        instruction = QLabel(
            "Инструкция: сохраняйте покой во время калибровки. В фазе «Подготовка» приготовьтесь, "
            "в фазе «Сокращение» выполните предписанное слабое/среднее/сильное сокращение, затем "
            "полностью расслабьтесь. Эти уровни являются инструкцией, а не измерением силы."
        )
        instruction.setWordWrap(True)
        instruction.setStyleSheet("font-size:15px;padding:12px;background:#203040")
        layout.addWidget(instruction)
        form = QFormLayout()
        self.participant = QLineEdit("P001")
        self.participant.setToolTip("Anonymous code only; 1–64 letters, digits, '_' or '-'; no names.")
        self.note = QLineEdit()
        self.note.setToolTip("Optional non-identifying session note; do not enter personal data.")
        self.repetitions = QSpinBox()
        self.repetitions.setRange(1, 300)
        self.repetitions.setValue(30)
        self.repetitions.setToolTip(TOOLTIPS["repetitions"])
        self.prepare = _spin(0.5, 120, 3, TOOLTIPS["prepare_s"])
        self.contract = _spin(0.5, 120, 2, TOOLTIPS["contract_s"])
        self.rest = _spin(0.5, 120, 3, TOOLTIPS["rest_s"])
        self.seed = QSpinBox()
        self.seed.setRange(0, 2_147_483_647)
        self.seed.setValue(20_260_916)
        self.seed.setToolTip(TOOLTIPS["seed"])
        for label, widget in (
            ("Anonymous participant ID", self.participant), ("Session note", self.note),
            ("Repetitions", self.repetitions), ("Prepare, s", self.prepare),
            ("Contract cue, s", self.contract), ("Rest, s", self.rest), ("Seed", self.seed),
        ):
            form.addRow(label, widget)
        layout.addLayout(form)
        self.phase = QLabel("Ready")
        self.phase.setStyleSheet("font-size:30px;font-weight:bold")
        self.detail = QLabel("Trial — / —; prescribed intensity —; remaining —")
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        layout.addWidget(self.phase)
        layout.addWidget(self.detail)
        layout.addWidget(self.progress)
        buttons = QHBoxLayout()
        self.start = QPushButton("Start calibration and experiment")
        self.pause = QPushButton("Pause")
        self.abort = QPushButton("Abort")
        self.pause.setEnabled(False)
        self.abort.setEnabled(False)
        self.start.clicked.connect(self._request_start)
        self.pause.clicked.connect(self.pause_requested)
        self.abort.clicked.connect(self.abort_requested)
        for button in (self.start, self.pause, self.abort):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        layout.addStretch()

    def config(self) -> ProtocolConfig:
        return ProtocolConfig(self.repetitions.value(), self.prepare.value(), self.contract.value(),
                              self.rest.value(), self.seed.value())

    def _request_start(self) -> None:
        self.start_requested.emit(self.config(), self.participant.text(), self.note.text())

    def set_running(self, active: bool) -> None:
        self.start.setEnabled(not active)
        self.pause.setEnabled(active)
        self.abort.setEnabled(active)

    def update_progress(self, snapshot: dict[str, object]) -> None:
        phase = str(snapshot.get("phase", ""))
        labels = {"calibration_rest": "Калибровка — покой", "prepare": "Подготовка",
                  "contract": "Сокращение", "rest": "Отдых", "paused": "Пауза"}
        self.phase.setText(labels.get(phase, phase or "Ready"))
        self.detail.setText(
            f"Trial {snapshot.get('trial', 0)} / {self.repetitions.value()}; prescribed intensity "
            f"{snapshot.get('prescribed_intensity') or '—'}; remaining {float(snapshot.get('remaining_s', 0)):.1f} s"
        )
        self.progress.setValue(round(float(snapshot.get("overall_progress", 0)) * 1000))


class ControlPanel(QWidget):
    enable_requested = Signal()
    disable_requested = Signal()
    reset_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.store = SettingsStore()
        layout = QVBoxLayout(self)
        warning = QLabel("OFF by default. F12 is the global emergency stop. Control cannot run during research recording.")
        warning.setWordWrap(True)
        warning.setStyleSheet("padding:12px;color:#ffb74d")
        layout.addWidget(warning)
        form = QFormLayout()
        self.x_axis = QComboBox(); self.x_axis.addItems(("gx", "gy", "gz")); self.x_axis.setCurrentText("gz")
        self.x_axis.setToolTip("Gyroscope source for horizontal velocity; gx/gy/gz; default gz; choose for mounting.")
        self.y_axis = QComboBox(); self.y_axis.addItems(("gx", "gy", "gz")); self.y_axis.setCurrentText("gy")
        self.y_axis.setToolTip("Gyroscope source for vertical velocity; gx/gy/gz; default gy; choose for mounting.")
        self.swap = QCheckBox(); self.invert_x = QCheckBox(); self.invert_y = QCheckBox()
        self.swap.setToolTip("Exchange horizontal/vertical sources; boolean; default off; adapts sensor mounting.")
        self.invert_x.setToolTip("Reverse horizontal direction; boolean; default off.")
        self.invert_y.setToolTip("Reverse vertical direction; boolean; default off.")
        self.sensitivity = _spin(0.1, 100, 12, TOOLTIPS["sensitivity"])
        self.dead_zone = _spin(0, 50, 1, TOOLTIPS["dead_zone_dps"])
        self.smoothing = _spin(0, 0.95, 0.35, TOOLTIPS["smoothing"])
        self.max_speed = _spin(10, 5000, 1200, TOOLTIPS["max_speed_px_s"])
        self.detector = QComboBox(); self.detector.addItems(("adaptive", "fixed"))
        self.detector.setToolTip("Detector whose contraction-start event triggers one action; default adaptive.")
        self.action = QComboBox(); self.action.addItems(("LMB", "RMB", "MMB", "double click", "Space", "Enter", "custom"))
        self.action.setToolTip("Exactly one SendInput action per selected detector contraction start; default LMB.")
        self.custom_key = QLineEdit(); self.custom_key.setMaxLength(1)
        self.custom_key.setToolTip("One keyboard character used only when action=custom; default empty.")
        for label, widget in (("X axis", self.x_axis), ("Y axis", self.y_axis), ("Swap axes", self.swap),
                              ("Invert X", self.invert_x), ("Invert Y", self.invert_y),
                              ("Sensitivity", self.sensitivity), ("Dead zone, deg/s", self.dead_zone),
                              ("Smoothing", self.smoothing), ("Max speed, px/s", self.max_speed),
                              ("Contraction detector", self.detector), ("Start action", self.action),
                              ("Custom key", self.custom_key)):
            form.addRow(label, widget)
        layout.addLayout(form)
        buttons = QHBoxLayout()
        self.enable = QPushButton("Enable after 3-second countdown")
        self.disable = QPushButton("Disable / F12")
        self.reset = QPushButton("Reset pointer filter")
        self.disable.setEnabled(False)
        self.enable.clicked.connect(self._start_countdown)
        self.disable.clicked.connect(self.disable_requested)
        self.reset.clicked.connect(self.reset_requested)
        for button in (self.enable, self.disable, self.reset): buttons.addWidget(button)
        layout.addLayout(buttons)
        profiles = QHBoxLayout()
        self.profile_name = QLineEdit("default")
        self.profile_name.setToolTip("Named local QSettings control profile; default name 'default'.")
        save_profile = QPushButton("Save profile"); load_profile = QPushButton("Load profile")
        save_profile.clicked.connect(self._save_profile); load_profile.clicked.connect(self._load_profile)
        for item in (QLabel("Profile:"), self.profile_name, save_profile, load_profile): profiles.addWidget(item)
        profiles.addStretch(); layout.addLayout(profiles)
        self.status = QLabel("CONTROL OFF")
        self.status.setStyleSheet("font-size:24px;color:#ef5350")
        layout.addWidget(self.status)
        layout.addStretch()
        self._countdown = 0
        self._timer = QTimer(self); self._timer.timeout.connect(self._tick)

    def settings(self) -> ControlSettings:
        return ControlSettings(self.x_axis.currentText(), self.y_axis.currentText(), self.swap.isChecked(),
                               self.invert_x.isChecked(), self.invert_y.isChecked(), self.sensitivity.value(),
                               self.dead_zone.value(), self.smoothing.value(), self.max_speed.value(),
                               self.detector.currentText(), self.action.currentText(), self.custom_key.text())

    def _save_profile(self) -> None:
        try:
            self.store.save_profile("control", self.profile_name.text(), self.settings())
            self.status.setText(f"Saved profile: {self.profile_name.text()}")
        except ValueError as error:
            self.status.setText(str(error))

    def _load_profile(self) -> None:
        try: value = self.store.load_profile("control", self.profile_name.text(), ControlSettings)
        except (KeyError, ValueError):
            self.status.setText("Control profile not found or invalid"); return
        self.x_axis.setCurrentText(value.x_axis); self.y_axis.setCurrentText(value.y_axis)
        self.swap.setChecked(value.swap_axes); self.invert_x.setChecked(value.invert_x); self.invert_y.setChecked(value.invert_y)
        self.sensitivity.setValue(value.sensitivity); self.dead_zone.setValue(value.dead_zone_dps)
        self.smoothing.setValue(value.smoothing); self.max_speed.setValue(value.max_speed_px_s)
        self.detector.setCurrentText(value.detector); self.action.setCurrentText(value.action)
        self.custom_key.setText(value.custom_key); self.status.setText(f"Loaded profile: {self.profile_name.text()}")

    def _start_countdown(self) -> None:
        self._countdown = 3
        self.enable.setEnabled(False)
        self.status.setText("Control starts in 3…")
        self._timer.start(1000)

    def _tick(self) -> None:
        self._countdown -= 1
        if self._countdown <= 0:
            self._timer.stop(); self.enable_requested.emit(); return
        self.status.setText(f"Control starts in {self._countdown}…")

    def set_enabled_state(self, enabled: bool, reason: str = "") -> None:
        self.enable.setEnabled(not enabled)
        self.disable.setEnabled(enabled)
        self.status.setText("CONTROL ON — F12 to stop" if enabled else f"CONTROL OFF{': ' + reason if reason else ''}")
        self.status.setStyleSheet(f"font-size:24px;color:{'#66bb6a' if enabled else '#ef5350'}")


class ResultsPanel(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        self.summary = QPlainTextEdit(); self.summary.setReadOnly(True)
        self.open_report = QPushButton("Open latest report.html"); self.open_report.setEnabled(False)
        self.open_folder = QPushButton("Open session folder"); self.open_folder.setEnabled(False)
        self.open_report.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.report))))
        self.open_folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.folder))))
        buttons = QHBoxLayout(); buttons.addWidget(self.open_report); buttons.addWidget(self.open_folder); buttons.addStretch()
        layout.addLayout(buttons); layout.addWidget(self.summary)
        self.report = Path(); self.folder = Path()

    def show_result(self, folder: Path, report: Path, text: str) -> None:
        self.folder, self.report = folder, report
        self.summary.setPlainText(text)
        self.open_folder.setEnabled(folder.exists()); self.open_report.setEnabled(report.exists())
