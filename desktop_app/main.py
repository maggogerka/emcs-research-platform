"""PySide6 desktop interface for EMCS acquisition and experiments."""

from __future__ import annotations

from collections import deque
import csv
from datetime import datetime
from pathlib import Path
import sys
from typing import Any

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)
from serial.tools import list_ports

from .experiment import ExperimentController
from .protocol import ADS_VOLTS_PER_BIT
from .serial_worker import SerialWorker


CSV_FIELDS = [
    "record_type",
    "timestamp_us",
    "sample_index",
    "emg_timer_gap",
    "imu_sample_index",
    "imu_timer_gap",
    "ads_raw",
    "ads_voltage",
    "envelope_raw",
    "envelope_voltage",
    "adaptive_on_voltage",
    "adaptive_off_voltage",
    "fixed_on_voltage",
    "fixed_off_voltage",
    "detector_state",
    "leads",
    "lo_minus",
    "lo_plus",
    "motion",
    "fixed_active",
    "adaptive_active",
    "fixed_event",
    "adaptive_event",
    "fixed_release",
    "adaptive_release",
    "ax",
    "ay",
    "az",
    "gx",
    "gy",
    "gz",
    "phase",
    "repetition",
    "intensity",
    "on_coefficient",
    "off_coefficient",
    "motion_gyro_dps",
    "motion_accel_delta_g",
]


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("EMCS Research Platform")
        self.resize(1400, 950)

        self.worker: SerialWorker | None = None
        self.csv_file = None
        self.csv_writer: csv.DictWriter | None = None
        self.recording_path: Path | None = None
        self.latest_imu = {key: float("nan") for key in ("ax", "ay", "az", "gx", "gy", "gz")}
        self.latest_status: dict[str, Any] = {}
        self.time_origin_us: int | None = None

        self.emg_time: deque[float] = deque(maxlen=8600)
        self.emg_raw: deque[float] = deque(maxlen=8600)
        self.emg_envelope: deque[float] = deque(maxlen=8600)
        self.adaptive_on: deque[float] = deque(maxlen=8600)
        self.adaptive_off: deque[float] = deque(maxlen=8600)
        self.fixed_on: deque[float] = deque(maxlen=8600)
        self.fixed_off: deque[float] = deque(maxlen=8600)
        self.imu_time: deque[float] = deque(maxlen=1000)
        self.imu_values = {
            name: deque(maxlen=1000) for name in ("ax", "ay", "az", "gx", "gy", "gz")
        }

        self.experiment = ExperimentController(self)
        self.experiment.phase_changed.connect(self._on_phase_changed)
        self.experiment.progress_changed.connect(self._on_experiment_progress)
        self.experiment.finished.connect(self._on_experiment_finished)

        self._build_ui()
        self._refresh_ports()

        self.plot_timer = QTimer(self)
        self.plot_timer.setInterval(50)
        self.plot_timer.timeout.connect(self._update_plots)
        self.plot_timer.start()

        self.status_timer = QTimer(self)
        self.status_timer.setInterval(1000)
        self.status_timer.timeout.connect(self._request_status)
        self.status_timer.start()

    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.addWidget(self._connection_group())
        root.addWidget(self._status_group())
        root.addWidget(self._control_group())

        splitter = QSplitter(Qt.Orientation.Vertical)
        self.raw_plot = pg.PlotWidget(title="Raw EMG (ADS1115 AIN0-GND)")
        self.raw_curve = self.raw_plot.plot(pen=pg.mkPen("#4FC3F7", width=1))
        self.raw_plot.setLabel("left", "Voltage", units="V")
        self.raw_plot.setLabel("bottom", "Time", units="s")

        self.envelope_plot = pg.PlotWidget(title="RMS envelope and thresholds")
        self.envelope_curve = self.envelope_plot.plot(
            pen=pg.mkPen("#FFFFFF", width=1.5), name="Envelope"
        )
        self.adaptive_on_curve = self.envelope_plot.plot(
            pen=pg.mkPen("#F44336", width=1.2), name="Adaptive Ton"
        )
        self.adaptive_off_curve = self.envelope_plot.plot(
            pen=pg.mkPen("#FF9800", width=1.2), name="Adaptive Toff"
        )
        self.fixed_on_curve = self.envelope_plot.plot(
            pen=pg.mkPen("#AB47BC", width=1, style=Qt.PenStyle.DashLine), name="Fixed Ton"
        )
        self.fixed_off_curve = self.envelope_plot.plot(
            pen=pg.mkPen("#7E57C2", width=1, style=Qt.PenStyle.DashLine), name="Fixed Toff"
        )
        self.envelope_plot.addLegend()
        self.envelope_plot.setLabel("left", "Voltage", units="V")
        self.envelope_plot.setLabel("bottom", "Time", units="s")

        self.accel_plot = pg.PlotWidget(title="MPU6050 acceleration")
        self.accel_curves = {
            axis: self.accel_plot.plot(pen=color, name=axis)
            for axis, color in zip(("ax", "ay", "az"), ("#EF5350", "#66BB6A", "#42A5F5"))
        }
        self.accel_plot.addLegend()
        self.accel_plot.setLabel("left", "Acceleration", units="g")
        self.accel_plot.setLabel("bottom", "Time", units="s")

        self.gyro_plot = pg.PlotWidget(title="MPU6050 angular velocity")
        self.gyro_curves = {
            axis: self.gyro_plot.plot(pen=color, name=axis)
            for axis, color in zip(("gx", "gy", "gz"), ("#EF5350", "#66BB6A", "#42A5F5"))
        }
        self.gyro_plot.addLegend()
        self.gyro_plot.setLabel("left", "Angular velocity", units="deg/s")
        self.gyro_plot.setLabel("bottom", "Time", units="s")

        for plot in (self.raw_plot, self.envelope_plot, self.accel_plot, self.gyro_plot):
            plot.showGrid(x=True, y=True, alpha=0.2)
            splitter.addWidget(plot)
        root.addWidget(splitter, stretch=1)

        self.event_log = QPlainTextEdit()
        self.event_log.setReadOnly(True)
        self.event_log.setMaximumBlockCount(300)
        self.event_log.setMaximumHeight(130)
        root.addWidget(self.event_log)
        self.setCentralWidget(central)
        self.statusBar().showMessage("Disconnected")

    def _connection_group(self) -> QGroupBox:
        group = QGroupBox("Connection")
        layout = QHBoxLayout(group)
        self.port_combo = QComboBox()
        self.refresh_button = QPushButton("Refresh")
        self.connect_button = QPushButton("Connect")
        self.refresh_button.clicked.connect(self._refresh_ports)
        self.connect_button.clicked.connect(self._toggle_connection)
        layout.addWidget(QLabel("Serial port:"))
        layout.addWidget(self.port_combo)
        layout.addWidget(self.refresh_button)
        layout.addWidget(self.connect_button)
        layout.addStretch()
        return group

    def _status_group(self) -> QGroupBox:
        group = QGroupBox("Hardware and detector status")
        layout = QGridLayout(group)
        self.status_labels: dict[str, QLabel] = {}
        entries = (
            ("ads", "ADS1115"),
            ("mpu", "MPU6050"),
            ("leads", "Electrodes"),
            ("detector", "Detector"),
            ("motion", "Motion"),
            ("transport", "Transport"),
        )
        for column, (key, title) in enumerate(entries):
            layout.addWidget(QLabel(title + ":"), 0, column)
            label = QLabel("—")
            label.setStyleSheet("font-weight: bold")
            layout.addWidget(label, 1, column)
            self.status_labels[key] = label
        return group

    def _control_group(self) -> QGroupBox:
        group = QGroupBox("Acquisition and experiment")
        layout = QGridLayout(group)
        self.cal_emg_button = QPushButton("Calibrate EMG (10 s rest)")
        self.cal_imu_button = QPushButton("Calibrate IMU")
        self.record_button = QPushButton("Start recording")
        self.experiment_button = QPushButton("Start experiment")
        self.cal_emg_button.clicked.connect(lambda: self._send("CAL EMG"))
        self.cal_imu_button.clicked.connect(lambda: self._send("CAL IMU"))
        self.record_button.clicked.connect(self._toggle_recording)
        self.experiment_button.clicked.connect(self._toggle_experiment)

        self.on_coefficient = QDoubleSpinBox()
        self.on_coefficient.setRange(1.0, 20.0)
        self.on_coefficient.setValue(6.0)
        self.off_coefficient = QDoubleSpinBox()
        self.off_coefficient.setRange(0.5, 19.0)
        self.off_coefficient.setValue(3.0)
        self.motion_gyro = QDoubleSpinBox()
        self.motion_gyro.setRange(1.0, 500.0)
        self.motion_gyro.setValue(20.0)
        self.motion_accel = QDoubleSpinBox()
        self.motion_accel.setRange(0.01, 5.0)
        self.motion_accel.setDecimals(2)
        self.motion_accel.setValue(0.25)
        self.apply_settings_button = QPushButton("Apply parameters")
        self.apply_settings_button.clicked.connect(self._apply_parameters)

        self.phase_label = QLabel("Experiment: idle")
        self.experiment_progress = QProgressBar()
        self.experiment_progress.setRange(0, 1000)

        layout.addWidget(self.cal_emg_button, 0, 0)
        layout.addWidget(self.cal_imu_button, 0, 1)
        layout.addWidget(self.record_button, 0, 2)
        layout.addWidget(self.experiment_button, 0, 3)
        layout.addWidget(QLabel("Ton coefficient:"), 1, 0)
        layout.addWidget(self.on_coefficient, 1, 1)
        layout.addWidget(QLabel("Toff coefficient:"), 1, 2)
        layout.addWidget(self.off_coefficient, 1, 3)
        layout.addWidget(QLabel("Motion gyro (deg/s):"), 2, 0)
        layout.addWidget(self.motion_gyro, 2, 1)
        layout.addWidget(QLabel("Motion |Δa| (g):"), 2, 2)
        layout.addWidget(self.motion_accel, 2, 3)
        layout.addWidget(self.apply_settings_button, 1, 4, 2, 1)
        layout.addWidget(self.phase_label, 3, 0, 1, 3)
        layout.addWidget(self.experiment_progress, 3, 3, 1, 2)
        return group

    def _refresh_ports(self) -> None:
        current = self.port_combo.currentText() or "COM13"
        ports = [port.device for port in list_ports.comports()]
        if "COM13" not in ports:
            ports.append("COM13")
        self.port_combo.clear()
        self.port_combo.addItems(sorted(set(ports)))
        index = self.port_combo.findText(current)
        self.port_combo.setCurrentIndex(index if index >= 0 else self.port_combo.findText("COM13"))

    def _toggle_connection(self) -> None:
        if self.worker is not None:
            self.worker.stop()
            self.connect_button.setEnabled(False)
            return
        port = self.port_combo.currentText()
        self.worker = SerialWorker(port)
        self.worker.connected.connect(self._on_connected)
        self.worker.disconnected.connect(self._on_disconnected)
        self.worker.status_received.connect(self._on_status)
        self.worker.emg_received.connect(self._on_emg)
        self.worker.imu_received.connect(self._on_imu)
        self.worker.event_received.connect(self._on_event)
        self.worker.message_received.connect(self._log)
        self.worker.error_received.connect(self._on_error)
        self.worker.transport_stats.connect(self._on_transport_stats)
        self.worker.start()
        self.connect_button.setEnabled(False)
        self.statusBar().showMessage(f"Connecting to {port}…")

    def _on_connected(self, port: str) -> None:
        self.connect_button.setText("Disconnect")
        self.connect_button.setEnabled(True)
        self.port_combo.setEnabled(False)
        self.statusBar().showMessage(f"Connected to {port}")
        self._log(f"Connected to {port}; waiting for device startup")

    def _on_disconnected(self, port: str) -> None:
        self._close_recording()
        self.worker = None
        self.connect_button.setText("Connect")
        self.connect_button.setEnabled(True)
        self.port_combo.setEnabled(True)
        self.statusBar().showMessage(f"Disconnected from {port}")

    def _send(self, command: str) -> None:
        if self.worker is None:
            QMessageBox.warning(self, "Not connected", "Connect to the ESP32-S3 first.")
            return
        self.worker.send_command(command)

    def _request_status(self) -> None:
        if self.worker is not None:
            self.worker.send_command("STATUS")

    def _apply_parameters(self) -> None:
        if self.on_coefficient.value() <= self.off_coefficient.value():
            QMessageBox.warning(self, "Invalid thresholds", "Ton coefficient must exceed Toff.")
            return
        self._send(
            f"SET THRESH {self.on_coefficient.value():.3f} {self.off_coefficient.value():.3f}"
        )
        self._send(f"SET IMU {self.motion_gyro.value():.3f} {self.motion_accel.value():.3f}")

    def _on_status(self, status: dict[str, Any]) -> None:
        self.latest_status = status
        self._set_status("ads", "OK" if status["ads_ok"] else "ERROR", bool(status["ads_ok"]))
        self._set_status("mpu", "OK" if status["mpu_ok"] else "ERROR", bool(status["mpu_ok"]))
        leads_ok = not status["lo_minus"] and not status["lo_plus"]
        leads_text = f"LO−={int(status['lo_minus'])}, LO+={int(status['lo_plus'])}"
        self._set_status("leads", leads_text, leads_ok)
        self._set_status("detector", status["detector_state_name"], leads_ok)
        self._set_status("motion", "MOVING" if status["motion"] else "STILL", not status["motion"])
        self.on_coefficient.setValue(status["on_coefficient"])
        self.off_coefficient.setValue(status["off_coefficient"])
        self.motion_gyro.setValue(status["motion_gyro_dps"])
        self.motion_accel.setValue(status["motion_accel_delta_g"])

    def _set_status(self, key: str, text: str, good: bool) -> None:
        color = "#43A047" if good else "#E53935"
        self.status_labels[key].setText(text)
        self.status_labels[key].setStyleSheet(f"font-weight: bold; color: {color}")

    def _on_transport_stats(self, stats: dict[str, int]) -> None:
        good = stats["crc_errors"] == 0 and stats["sequence_gaps"] == 0
        text = f"CRC {stats['crc_errors']}, gaps {stats['sequence_gaps']}"
        self._set_status("transport", text, good)

    def _on_emg(self, batch: dict[str, Any]) -> None:
        if not batch["samples"]:
            return
        if self.time_origin_us is None:
            self.time_origin_us = batch["samples"][0]["timestamp_us"]
        adaptive_on_v = batch["adaptive_on"] * ADS_VOLTS_PER_BIT
        adaptive_off_v = batch["adaptive_off"] * ADS_VOLTS_PER_BIT
        fixed_on_v = batch["fixed_on"] * ADS_VOLTS_PER_BIT
        fixed_off_v = batch["fixed_off"] * ADS_VOLTS_PER_BIT

        rows = []
        experiment = self.experiment.snapshot()
        for sample in batch["samples"]:
            time_s = (sample["timestamp_us"] - self.time_origin_us) / 1_000_000.0
            self.emg_time.append(time_s)
            self.emg_raw.append(sample["ads_voltage"])
            self.emg_envelope.append(sample["envelope_voltage"])
            self.adaptive_on.append(adaptive_on_v)
            self.adaptive_off.append(adaptive_off_v)
            self.fixed_on.append(fixed_on_v)
            self.fixed_off.append(fixed_off_v)
            if self.csv_writer is not None:
                rows.append(
                    {
                        **sample,
                        "record_type": "emg",
                        "emg_timer_gap": sample["timer_gap"],
                        "adaptive_on_voltage": adaptive_on_v,
                        "adaptive_off_voltage": adaptive_off_v,
                        "fixed_on_voltage": fixed_on_v,
                        "fixed_off_voltage": fixed_off_v,
                        "detector_state": batch["detector_state_name"],
                        "leads": batch["leads"],
                        "lo_minus": int(batch["lo_minus"]),
                        "lo_plus": int(batch["lo_plus"]),
                        "motion": int(batch["motion"]),
                        **self.latest_imu,
                        **experiment,
                        "on_coefficient": self.on_coefficient.value(),
                        "off_coefficient": self.off_coefficient.value(),
                        "motion_gyro_dps": self.motion_gyro.value(),
                        "motion_accel_delta_g": self.motion_accel.value(),
                    }
                )
        if rows and self.csv_writer is not None:
            self.csv_writer.writerows(rows)
            self.csv_file.flush()
        leads_ok = not batch["lo_minus"] and not batch["lo_plus"]
        self._set_status(
            "leads",
            f"LO−={int(batch['lo_minus'])}, LO+={int(batch['lo_plus'])}",
            leads_ok,
        )
        self._set_status("detector", batch["detector_state_name"], leads_ok)

    def _on_imu(self, batch: dict[str, Any]) -> None:
        if self.time_origin_us is None and batch["samples"]:
            self.time_origin_us = batch["samples"][0]["timestamp_us"]
        rows = []
        experiment = self.experiment.snapshot()
        for sample in batch["samples"]:
            self.latest_imu = {key: sample[key] for key in self.latest_imu}
            time_s = (sample["timestamp_us"] - self.time_origin_us) / 1_000_000.0
            self.imu_time.append(time_s)
            for name in self.imu_values:
                self.imu_values[name].append(sample[name])
            if self.csv_writer is not None:
                rows.append(
                    {
                        "record_type": "imu",
                        "timestamp_us": sample["timestamp_us"],
                        "imu_sample_index": sample["sample_index"],
                        "imu_timer_gap": sample["timer_gap"],
                        **{key: sample[key] for key in self.latest_imu},
                        **experiment,
                        "on_coefficient": self.on_coefficient.value(),
                        "off_coefficient": self.off_coefficient.value(),
                        "motion_gyro_dps": self.motion_gyro.value(),
                        "motion_accel_delta_g": self.motion_accel.value(),
                    }
                )
        if rows and self.csv_writer is not None:
            self.csv_writer.writerows(rows)
            self.csv_file.flush()

    def _on_event(self, event: dict[str, Any]) -> None:
        self._log(
            f"{event['timestamp_us'] / 1e6:.3f}s {event['detector']}: "
            f"{event['event']} ({event['state']}, envelope={event['envelope_voltage']:.6f} V)"
        )

    def _on_error(self, message: str) -> None:
        self._log("ERROR: " + message)
        self.statusBar().showMessage(message, 10_000)

    def _log(self, message: str) -> None:
        self.event_log.appendPlainText(message)

    def _toggle_recording(self) -> None:
        if self.csv_writer is None:
            default_dir = Path(__file__).resolve().parents[1] / "data" / "recordings"
            default_dir.mkdir(parents=True, exist_ok=True)
            default_path = default_dir / f"emcs_{datetime.now():%Y%m%d_%H%M%S}.csv"
            selected, _ = QFileDialog.getSaveFileName(
                self, "Save EMCS recording", str(default_path), "CSV files (*.csv)"
            )
            if not selected:
                return
            self.csv_file = open(selected, "w", newline="", encoding="utf-8")
            self.csv_writer = csv.DictWriter(self.csv_file, fieldnames=CSV_FIELDS, extrasaction="ignore")
            self.csv_writer.writeheader()
            self.recording_path = Path(selected)
            self.record_button.setText("Stop recording")
            self._send("RECORD START")
            self._log(f"Recording to {selected}")
        else:
            self._close_recording()

    def _close_recording(self) -> None:
        if self.csv_file is None:
            return
        if self.worker is not None:
            self.worker.send_command("RECORD STOP")
        path = self.recording_path
        self.csv_file.close()
        self.csv_file = None
        self.csv_writer = None
        self.recording_path = None
        self.record_button.setText("Start recording")
        self._log(f"Recording saved: {path}")

    def _toggle_experiment(self) -> None:
        if self.experiment.snapshot()["phase"]:
            self.experiment.stop()
            return
        if self.csv_writer is None:
            self._toggle_recording()
            if self.csv_writer is None:
                return
        self._send("CAL EMG")
        self.experiment.start()
        self.experiment_button.setText("Stop experiment")
        self._log("Experiment started: 10 s baseline followed by 30 contractions")

    def _on_phase_changed(self, phase: dict[str, Any]) -> None:
        text = phase["phase"]
        if phase["repetition"]:
            text += f" — repetition {phase['repetition']}/30, {phase['intensity']}"
        self.phase_label.setText("Experiment: " + text)
        self._log("Phase: " + text)

    def _on_experiment_progress(self, progress: float) -> None:
        self.experiment_progress.setValue(round(progress * 1000))

    def _on_experiment_finished(self) -> None:
        self.phase_label.setText("Experiment: complete")
        self.experiment_button.setText("Start experiment")
        self.experiment_progress.setValue(1000)
        self._log("Experiment complete")
        self._close_recording()

    def _update_plots(self) -> None:
        if self.emg_time:
            x = np.fromiter(self.emg_time, dtype=float)
            self.raw_curve.setData(x, np.fromiter(self.emg_raw, dtype=float))
            self.envelope_curve.setData(x, np.fromiter(self.emg_envelope, dtype=float))
            self.adaptive_on_curve.setData(x, np.fromiter(self.adaptive_on, dtype=float))
            self.adaptive_off_curve.setData(x, np.fromiter(self.adaptive_off, dtype=float))
            self.fixed_on_curve.setData(x, np.fromiter(self.fixed_on, dtype=float))
            self.fixed_off_curve.setData(x, np.fromiter(self.fixed_off, dtype=float))
        if self.imu_time:
            x = np.fromiter(self.imu_time, dtype=float)
            for axis, curve in self.accel_curves.items():
                curve.setData(x, np.fromiter(self.imu_values[axis], dtype=float))
            for axis, curve in self.gyro_curves.items():
                curve.setData(x, np.fromiter(self.imu_values[axis], dtype=float))

    def closeEvent(self, event) -> None:  # noqa: N802
        self.experiment.stop(emit_finished=False)
        self._close_recording()
        if self.worker is not None:
            self.worker.stop()
            self.worker.wait(2000)
        event.accept()


def main() -> int:
    pg.setConfigOptions(antialias=False, background=QColor("#161A1D"), foreground="w")
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
