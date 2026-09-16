"""Seven-tab PySide6 desktop application for EMSU acquisition and experiments."""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

import pyqtgraph as pg
from PySide6.QtCore import QProcess, QTimer, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton,
    QSplitter, QTabWidget, QVBoxLayout, QWidget,
)
from serial.tools import list_ports

from .control import GlobalEmergencyHotkey, MouseMapper, SendInputBackend
from .experiment import ExperimentController, ProtocolConfig
from .orientation import OrientationPanel
from .panels import ControlPanel, ExperimentPanel, ResultsPanel, StatusStrip
from .plots import ScientificPlot
from .protocol import ADS_VOLTS_PER_BIT, INTENSITY_CODES, PHASE_CODES
from .serial_worker import SerialWorker
from .session import SessionPaths, SessionRecorder
from .settings import AlgorithmSettings, SettingsStore, TOOLTIPS
from .version import APP_NAME, APP_VERSION


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} {APP_VERSION}")
        self.resize(1500, 960)
        self.worker: SerialWorker | None = None
        self.recorder = SessionRecorder(PROJECT_ROOT)
        self.latest_status: dict[str, Any] = {}
        self.transport = {"crc_errors": 0, "sequence_gaps": 0, "discarded_bytes": 0}
        self.time_origin_us: int | None = None
        self.last_emg_host = 0.0
        self.last_imu_host = 0.0
        self.latest_imu = {name: 0.0 for name in ("ax", "ay", "az", "gx", "gy", "gz")}
        self.latest_imu_timestamp: int | None = None
        self.ac_baseline: float | None = None
        self.marker_id = 0
        self.last_marker: dict[str, Any] | None = None
        self.last_device_timestamp_us = 0
        self.control_active = False
        self.mouse_mapper = MouseMapper()
        self.input_backend = SendInputBackend()
        self.settings_store = SettingsStore()
        self.analysis_process: QProcess | None = None

        self.experiment = ExperimentController(self)
        self.experiment.phase_changed.connect(self._on_phase_changed)
        self.experiment.progress_changed.connect(self.experiment_panel_update)
        self.experiment.finished.connect(self._finish_experiment)

        self._build_ui()
        self._wire_ui()
        self._refresh_ports()

        self.hotkey = GlobalEmergencyHotkey()
        self.hotkey.activated.connect(lambda: self._disable_control("F12 emergency stop"))
        QApplication.instance().installNativeEventFilter(self.hotkey)
        registered = self.hotkey.register()
        self._log(f"Global F12 emergency stop: {'registered' if registered else 'unavailable'}")

        self.plot_timer = QTimer(self)
        self.plot_timer.setInterval(50)
        self.plot_timer.timeout.connect(self._refresh_plots)
        self.plot_timer.start()
        self.status_timer = QTimer(self)
        self.status_timer.setInterval(100)
        self.status_timer.timeout.connect(self._safety_tick)
        self.status_timer.start()

    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        self.status_strip = StatusStrip()
        root.addWidget(self.status_strip)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)
        self.setCentralWidget(central)

        self.dashboard = self._dashboard_tab()
        self.experiment_panel = ExperimentPanel()
        self.signals = self._signals_tab()
        self.orientation = OrientationPanel()
        self.control_panel = ControlPanel()
        self.results_panel = ResultsPanel()
        self.diagnostics = self._diagnostics_tab()
        for label, widget in (
            ("Dashboard", self.dashboard), ("Experiment", self.experiment_panel),
            ("Signals", self.signals), ("3D Orientation", self.orientation),
            ("Computer Control", self.control_panel), ("Results", self.results_panel),
            ("Diagnostics/Settings", self.diagnostics),
        ):
            self.tabs.addTab(widget, label)

    def _dashboard_tab(self) -> QWidget:
        widget = QWidget(); layout = QVBoxLayout(widget)
        connection = QGroupBox("ESP32-S3 connection"); row = QHBoxLayout(connection)
        self.port_combo = QComboBox(); self.refresh_ports = QPushButton("Refresh")
        self.connect_button = QPushButton("Connect")
        row.addWidget(QLabel("Port:")); row.addWidget(self.port_combo); row.addWidget(self.refresh_ports)
        row.addWidget(self.connect_button); row.addStretch()
        layout.addWidget(connection)
        values = QGroupBox("Live values"); grid = QFormLayout(values)
        self.live_emg = QLabel("— V"); self.live_envelope = QLabel("— V")
        self.live_imu = QLabel("—"); self.live_state = QLabel("Disconnected")
        grid.addRow("ADS1115 A0 absolute", self.live_emg)
        grid.addRow("EMG envelope", self.live_envelope)
        grid.addRow("MPU6050 accel / gyro", self.live_imu)
        grid.addRow("Detector", self.live_state)
        layout.addWidget(values)
        notice = QLabel(
            "Research prototype only — not a medical device. Participant IDs must be anonymous. "
            "The application records all samples; graph downsampling affects display only."
        )
        notice.setWordWrap(True); notice.setStyleSheet("padding:12px;background:#30251d;color:#ffcc80")
        layout.addWidget(notice); layout.addStretch()
        return widget

    def _signals_tab(self) -> QWidget:
        widget = QWidget(); layout = QVBoxLayout(widget)
        self.raw_plot = ScientificPlot("AD8232 / ADS1115 absolute A0-GND", "Voltage, V", {"absolute": "#4fc3f7"})
        self.ac_plot = ScientificPlot("Centered AC component (desktop diagnostic)", "Voltage, V", {"ac": "#66bb6a"})
        self.envelope_plot = ScientificPlot(
            "Firmware envelope and thresholds", "Voltage, V",
            {"envelope": "#ffffff", "adaptive_on": "#ef5350", "adaptive_off": "#ff9800",
             "fixed_on": "#ab47bc", "fixed_off": "#7e57c2"},
        )
        self.accel_plot = ScientificPlot("MPU6050 acceleration", "Acceleration, g",
                                         {"ax": "#ef5350", "ay": "#66bb6a", "az": "#42a5f5"})
        self.gyro_plot = ScientificPlot("MPU6050 angular velocity", "Angular rate, deg/s",
                                        {"gx": "#ef5350", "gy": "#66bb6a", "gz": "#42a5f5"})
        self.all_plots = [self.raw_plot, self.ac_plot, self.envelope_plot, self.accel_plot, self.gyro_plot]
        for plot in self.all_plots[1:]: plot.link_time_axis(self.raw_plot)
        self.plot_splitter = QSplitter(Qt.Orientation.Vertical)
        for plot in self.all_plots: self.plot_splitter.addWidget(plot)
        layout.addWidget(self.plot_splitter)
        self._expanded_plot: ScientificPlot | None = None
        return widget

    def _diagnostics_tab(self) -> QWidget:
        widget = QWidget(); layout = QVBoxLayout(widget)
        algorithm = QGroupBox("Detector and motion-guard parameters"); form = QFormLayout(algorithm)
        self.on_coefficient = self._parameter(1, 20, 6, TOOLTIPS["on_coefficient"])
        self.off_coefficient = self._parameter(0.5, 19, 3, TOOLTIPS["off_coefficient"])
        self.motion_gyro = self._parameter(1, 500, 20, TOOLTIPS["motion_gyro_dps"])
        self.motion_accel = self._parameter(0.01, 5, 0.25, TOOLTIPS["motion_accel_delta_g"])
        for label, item in (("Ton coefficient", self.on_coefficient), ("Toff coefficient", self.off_coefficient),
                            ("Motion gyro, deg/s", self.motion_gyro), ("Motion accel delta, g", self.motion_accel)):
            form.addRow(label, item)
        buttons = QHBoxLayout(); self.apply_parameters = QPushButton("Apply to device")
        self.profile_name = QLineEdit("default"); self.save_profile = QPushButton("Save profile")
        self.load_profile = QPushButton("Load profile")
        for item in (self.apply_parameters, QLabel("Profile:"), self.profile_name, self.save_profile, self.load_profile):
            buttons.addWidget(item)
        form.addRow(buttons)
        layout.addWidget(algorithm)
        self.event_log = QPlainTextEdit(); self.event_log.setReadOnly(True); self.event_log.setMaximumBlockCount(2000)
        layout.addWidget(self.event_log, 1)
        return widget

    @staticmethod
    def _parameter(minimum: float, maximum: float, value: float, tooltip: str) -> QDoubleSpinBox:
        item = QDoubleSpinBox(); item.setRange(minimum, maximum); item.setDecimals(3)
        item.setValue(value); item.setToolTip(tooltip)
        return item

    def _wire_ui(self) -> None:
        self.refresh_ports.clicked.connect(self._refresh_ports)
        self.connect_button.clicked.connect(self._toggle_connection)
        self.experiment_panel.start_requested.connect(self._start_experiment)
        self.experiment_panel.pause_requested.connect(self._toggle_experiment_pause)
        self.experiment_panel.abort_requested.connect(lambda: self.experiment.stop("aborted_by_user"))
        self.control_panel.enable_requested.connect(self._enable_control)
        self.control_panel.disable_requested.connect(lambda: self._disable_control("disabled by user"))
        self.control_panel.reset_requested.connect(self.mouse_mapper.reset)
        self.apply_parameters.clicked.connect(self._apply_algorithm)
        self.save_profile.clicked.connect(self._save_algorithm_profile)
        self.load_profile.clicked.connect(self._load_algorithm_profile)
        for plot in self.all_plots:
            plot.expand_requested.connect(self._toggle_expand_plot)

    def _refresh_ports(self) -> None:
        current = self.port_combo.currentText()
        ports = [item.device for item in list_ports.comports()]
        if "COM13" not in ports:
            ports.append("COM13")
        self.port_combo.clear(); self.port_combo.addItems(sorted(set(ports)))
        preferred = current if current in ports else "COM13" if "COM13" in ports else ""
        self.port_combo.setCurrentText(preferred)

    def _toggle_connection(self) -> None:
        if self.worker is not None:
            self._disable_control("serial disconnect")
            self.worker.stop(); self.connect_button.setEnabled(False)
            return
        port = self.port_combo.currentText().strip()
        if not port:
            QMessageBox.warning(self, "No port", "Select an ESP32-S3 serial port.")
            return
        self.worker = SerialWorker(port)
        self.worker.connected.connect(self._on_connected)
        self.worker.disconnected.connect(self._on_disconnected)
        self.worker.status_received.connect(self._on_status)
        self.worker.emg_received.connect(self._on_emg)
        self.worker.imu_received.connect(self._on_imu)
        self.worker.event_received.connect(self._on_event)
        self.worker.marker_received.connect(self._on_marker)
        self.worker.message_received.connect(lambda message: self._log("DEVICE: " + message))
        self.worker.error_received.connect(self._on_error)
        self.worker.transport_stats.connect(self._on_transport)
        self.worker.start(); self.connect_button.setEnabled(False)
        self.statusBar().showMessage(f"Connecting to {port}…")

    def _on_connected(self, port: str) -> None:
        self.connect_button.setText("Disconnect"); self.connect_button.setEnabled(True)
        self.port_combo.setEnabled(False); self.status_strip.set_value("esp", port, True)
        self.statusBar().showMessage(f"Connected to {port}"); self._log(f"Connected to {port}")

    def _on_disconnected(self, port: str) -> None:
        self._disable_control("ESP disconnected")
        if self.experiment.state in {"waiting_calibration", "running", "paused"}:
            self.experiment.stop("hardware_error")
        self.worker = None; self.connect_button.setText("Connect"); self.connect_button.setEnabled(True)
        self.port_combo.setEnabled(True); self.status_strip.set_value("esp", "disconnected", False)
        self.statusBar().showMessage(f"Disconnected from {port}")

    def _send(self, command: str, *, warn: bool = False) -> bool:
        if self.worker is None:
            if warn: QMessageBox.warning(self, "Not connected", "Connect the ESP32-S3 first.")
            return False
        self.worker.send_command(command)
        return True

    def _algorithm_settings(self) -> AlgorithmSettings:
        settings = AlgorithmSettings(self.on_coefficient.value(), self.off_coefficient.value(),
                                     self.motion_gyro.value(), self.motion_accel.value())
        settings.validate(); return settings

    def _apply_algorithm(self) -> None:
        try: settings = self._algorithm_settings()
        except ValueError as error:
            QMessageBox.warning(self, "Invalid parameters", str(error)); return
        self._send(f"SET THRESH {settings.on_coefficient:.3f} {settings.off_coefficient:.3f}", warn=True)
        self._send(f"SET IMU {settings.motion_gyro_dps:.3f} {settings.motion_accel_delta_g:.3f}")

    def _save_algorithm_profile(self) -> None:
        try:
            self.settings_store.save_profile("algorithm", self.profile_name.text(), self._algorithm_settings())
            self._log(f"Saved algorithm profile: {self.profile_name.text()}")
        except ValueError as error: QMessageBox.warning(self, "Profile", str(error))

    def _load_algorithm_profile(self) -> None:
        try: settings = self.settings_store.load_profile("algorithm", self.profile_name.text(), AlgorithmSettings)
        except (KeyError, ValueError, json.JSONDecodeError) as error:
            QMessageBox.warning(self, "Profile", f"Cannot load profile: {error}"); return
        self.on_coefficient.setValue(settings.on_coefficient); self.off_coefficient.setValue(settings.off_coefficient)
        self.motion_gyro.setValue(settings.motion_gyro_dps); self.motion_accel.setValue(settings.motion_accel_delta_g)

    def _on_status(self, status: dict[str, Any]) -> None:
        self.latest_status = status
        self.status_strip.set_value("ads", "OK" if status["ads_ok"] else "ERROR", bool(status["ads_ok"]))
        self.status_strip.set_value("mpu", "OK" if status["mpu_ok"] else "ERROR", bool(status["mpu_ok"]))
        leads_ok = not status["lo_minus"] and not status["lo_plus"]
        self.status_strip.set_value("electrodes", f"LO−={int(status['lo_minus'])}, LO+={int(status['lo_plus'])}", leads_ok)
        calibrated = bool(status["emg_calibrated"]) and not bool(status["imu_calibrating"])
        self.status_strip.set_value("calibration", "ready" if calibrated else "not ready", calibrated)
        self.live_state.setText(status["detector_state_name"])
        if self.control_active and (not status["ads_ok"] or not status["mpu_ok"]):
            self._disable_control("hardware error")
        if self.control_active and not leads_ok: self._disable_control("electrode lead-off")
        if self.control_active and not calibrated: self._disable_control("calibration unavailable")
        if self.experiment.state in {"waiting_calibration", "running", "paused"}:
            if not status["ads_ok"] or not status["mpu_ok"]: self.experiment.stop("hardware_error")
            elif not leads_ok: self.experiment.stop("lead_off")

    def _on_transport(self, stats: dict[str, int]) -> None:
        self.transport = dict(stats)
        self.status_strip.set_value("crc", str(stats["crc_errors"]), stats["crc_errors"] == 0)
        self.status_strip.set_value("gaps", str(stats["sequence_gaps"]), stats["sequence_gaps"] == 0)

    def _origin_time(self, timestamp_us: int) -> float:
        if self.time_origin_us is None: self.time_origin_us = timestamp_us
        self.last_device_timestamp_us = max(self.last_device_timestamp_us, timestamp_us)
        return (timestamp_us - self.time_origin_us) / 1_000_000.0

    def _on_emg(self, batch: dict[str, Any]) -> None:
        if not batch["samples"]: return
        self.last_emg_host = time.monotonic()
        adaptive_on = batch["adaptive_on"] * ADS_VOLTS_PER_BIT
        adaptive_off = batch["adaptive_off"] * ADS_VOLTS_PER_BIT
        fixed_on = batch["fixed_on"] * ADS_VOLTS_PER_BIT
        fixed_off = batch["fixed_off"] * ADS_VOLTS_PER_BIT
        ac_values: list[float] = []
        for sample in batch["samples"]:
            voltage = float(sample["ads_voltage"])
            if self.ac_baseline is None: self.ac_baseline = voltage
            self.ac_baseline += 0.001162 * (voltage - self.ac_baseline)
            ac = voltage - self.ac_baseline; ac_values.append(ac)
            relative = self._origin_time(int(sample["timestamp_us"]))
            self.raw_plot.append(relative, {"absolute": voltage})
            self.ac_plot.append(relative, {"ac": ac})
            self.envelope_plot.append(relative, {"envelope": sample["envelope_voltage"],
                                      "adaptive_on": adaptive_on, "adaptive_off": adaptive_off,
                                      "fixed_on": fixed_on, "fixed_off": fixed_off})
        latest = batch["samples"][-1]
        self.live_emg.setText(f"{latest['ads_voltage']:.6f} V")
        self.live_envelope.setText(f"{latest['envelope_voltage']:.6f} V")
        if self.recorder.active:
            self.recorder.write_emg(batch, ac_values, asdict(self._algorithm_settings()))
        leads_ok = not batch["lo_minus"] and not batch["lo_plus"]
        self.status_strip.set_value("electrodes", f"LO−={int(batch['lo_minus'])}, LO+={int(batch['lo_plus'])}", leads_ok)
        if not leads_ok:
            if self.control_active: self._disable_control("electrode lead-off")
            if self.experiment.state in {"waiting_calibration", "running", "paused"}: self.experiment.stop("lead_off")

    def _on_imu(self, batch: dict[str, Any]) -> None:
        if not batch["samples"]: return
        self.last_imu_host = time.monotonic()
        algorithm = asdict(self._algorithm_settings())
        for sample in batch["samples"]:
            relative = self._origin_time(int(sample["timestamp_us"]))
            self.latest_imu = {name: float(sample[name]) for name in self.latest_imu}
            self.accel_plot.append(relative, {name: sample[name] for name in ("ax", "ay", "az")})
            self.gyro_plot.append(relative, {name: sample[name] for name in ("gx", "gy", "gz")})
            dt_s = 0.01 if self.latest_imu_timestamp is None else max(0.001, min(0.1, (sample["timestamp_us"] - self.latest_imu_timestamp) / 1e6))
            self.latest_imu_timestamp = int(sample["timestamp_us"])
            self.orientation.update_sample(sample, dt_s)
            if self.control_active:
                dx, dy = self.mouse_mapper.displacement(sample, self.control_panel.settings(), dt_s)
                try: self.input_backend.move(dx, dy)
                except Exception as error: self._disable_control(f"SendInput error: {error}")
        self.live_imu.setText(
            f"a=({self.latest_imu['ax']:.3f}, {self.latest_imu['ay']:.3f}, {self.latest_imu['az']:.3f}) g; "
            f"ω=({self.latest_imu['gx']:.2f}, {self.latest_imu['gy']:.2f}, {self.latest_imu['gz']:.2f}) deg/s"
        )
        if self.recorder.active: self.recorder.write_imu(batch, algorithm)

    def _on_event(self, event: dict[str, Any]) -> None:
        self._log(f"{event['timestamp_us'] / 1e6:.3f}s {event['detector']} {event['event']}")
        if self.recorder.active: self.recorder.write_device_event(event)
        if event["event"] == "EMG_CALIBRATION_DONE":
            if self.experiment.calibration_done():
                self._log("Device confirmed EMG_CALIBRATION_DONE; prescribed trials begin")
        if self.control_active and event["event"] == "CONTRACTION_START":
            settings = self.control_panel.settings()
            if event["detector"] == settings.detector:
                try: self.input_backend.click(settings.action, settings.custom_key)
                except Exception as error: self._disable_control(f"SendInput error: {error}")
        if self.time_origin_us is not None:
            event_time = (event["timestamp_us"] - self.time_origin_us) / 1e6
            for plot in self.all_plots: plot.add_event(event_time, f"{event['detector']}:{event['event']}")

    def _on_marker(self, marker: dict[str, Any]) -> None:
        self._log(f"MARKER {marker['marker_id']} {marker['phase']} trial={marker['trial']} prescribed={marker['prescribed_intensity']}")
        if self.recorder.active: self.recorder.write_marker(marker)
        if self.time_origin_us is not None and self.last_marker is not None:
            start_s = (self.last_marker["timestamp_us"] - self.time_origin_us) / 1e6
            end_s = (marker["timestamp_us"] - self.time_origin_us) / 1e6
            for plot in self.all_plots: plot.add_phase(start_s, end_s, self.last_marker["phase"])
        self.last_marker = marker

    def _on_error(self, message: str) -> None:
        self._log("ERROR: " + message); self.statusBar().showMessage(message, 10_000)
        self._disable_control("device error")
        if self.experiment.state in {"waiting_calibration", "running", "paused"}:
            self.experiment.stop("hardware_error")

    def _start_experiment(self, config: ProtocolConfig, participant_id: str, note: str) -> None:
        if self.worker is None:
            QMessageBox.warning(self, "Experiment blocked", "Connect the ESP32-S3 first."); return
        if self.control_active:
            QMessageBox.warning(self, "Experiment blocked", "Disable Computer Control first."); return
        if not self.latest_status.get("ads_ok") or not self.latest_status.get("mpu_ok"):
            QMessageBox.warning(self, "Experiment blocked", "ADS1115 and MPU6050 must both be healthy."); return
        if self.latest_status.get("lo_minus") or self.latest_status.get("lo_plus"):
            QMessageBox.warning(self, "Experiment blocked", "Attach both electrodes (LO−=0, LO+=0)."); return
        try:
            config.validate(); algorithm = self._algorithm_settings()
            paths = self.recorder.start(
                {"participant_id": participant_id, "session_note": note,
                 "protocol": asdict(config), "algorithm": asdict(algorithm),
                 "firmware": {"target": "esp32s3", "protocol_version": 1},
                 "serial_port": self.port_combo.currentText(),
                 "prescribed_intensity_note": "weak/medium/strong are instructions, not measured force"}
            )
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, "Cannot start session", str(error)); return
        self.last_marker = None
        self._send("RECORD START")
        self.experiment.arm(config)
        self._send("CAL EMG")
        self.experiment_panel.set_running(True)
        self._log(f"Session started: {paths.root}; waiting for actual EMG_CALIBRATION_DONE")

    def _send_phase_marker(self, phase: str, trial: int, intensity: str) -> None:
        self.marker_id += 1
        phase_code = PHASE_CODES[phase]; intensity_code = INTENSITY_CODES.get(intensity, 0)
        self._send(f"MARK {self.marker_id} {phase_code} {trial} {intensity_code}")

    def _on_phase_changed(self, phase: dict[str, Any]) -> None:
        QApplication.beep()
        self.experiment_panel.update_progress(phase)
        self._send_phase_marker(str(phase["phase"]), int(phase.get("trial", 0)), str(phase.get("prescribed_intensity", "")))
        self._log(f"Protocol phase: {phase['phase']}, trial={phase.get('trial', 0)}, prescribed={phase.get('prescribed_intensity', '')}")

    def experiment_panel_update(self, snapshot: dict[str, object]) -> None:
        self.experiment_panel.update_progress(snapshot)

    def _toggle_experiment_pause(self) -> None:
        if self.experiment.state == "running":
            self.experiment.pause(); self.experiment_panel.pause.setText("Resume")
        elif self.experiment.state == "paused":
            self.experiment.resume(); self.experiment_panel.pause.setText("Pause")

    def _finish_experiment(self, outcome: str) -> None:
        snapshot = self.experiment.snapshot()
        trial = int(snapshot.get("trial", 0)); intensity = str(snapshot.get("prescribed_intensity", ""))
        self._send_phase_marker(outcome, trial, intensity)
        self._send("RECORD STOP")
        self.experiment_panel.set_running(False); self.experiment_panel.pause.setText("Pause")
        paths = self.recorder.finalize(outcome, transport=self.transport, hardware_status=self.latest_status)
        self._log(f"Session finalized with outcome={outcome}")
        if paths is not None: self._run_analysis(paths)

    def _run_analysis(self, paths: SessionPaths) -> None:
        executable, arguments = SessionRecorder.analysis_command(paths)
        process = QProcess(self); self.analysis_process = process
        process.setProgram(executable); process.setArguments(arguments)
        process.setWorkingDirectory(str(PROJECT_ROOT))
        process.finished.connect(lambda code, status: self._analysis_finished(paths, code))
        process.start(); self._log("Offline scientific analysis started")

    def _analysis_finished(self, paths: SessionPaths, exit_code: int) -> None:
        process = self.analysis_process
        output = bytes(process.readAllStandardOutput()).decode(errors="replace") if process else ""
        errors = bytes(process.readAllStandardError()).decode(errors="replace") if process else ""
        summary = f"Analysis exit code: {exit_code}\n{output}\n{errors}".strip()
        self.results_panel.show_result(paths.root, paths.report, summary)
        self.tabs.setCurrentWidget(self.results_panel)
        self._log(f"Analysis finished with exit code {exit_code}")

    def _enable_control(self) -> None:
        reason = self._control_block_reason()
        if reason:
            self.control_panel.set_enabled_state(False, reason)
            QMessageBox.warning(self, "Computer Control blocked", reason); return
        try: self.control_panel.settings().validate()
        except ValueError as error:
            QMessageBox.warning(self, "Control settings", str(error)); return
        self.mouse_mapper.reset(); self.control_active = True
        self.control_panel.set_enabled_state(True); self._log("Computer Control enabled after countdown")

    def _control_block_reason(self) -> str:
        now = time.monotonic()
        if self.worker is None: return "ESP disconnected"
        if self.recorder.active or self.experiment.state in {"waiting_calibration", "running", "paused"}: return "research session active"
        if not self.latest_status.get("ads_ok") or not self.latest_status.get("mpu_ok"): return "sensor hardware unavailable"
        if self.latest_status.get("lo_minus") or self.latest_status.get("lo_plus"): return "electrode lead-off"
        if not self.latest_status.get("emg_calibrated") or self.latest_status.get("imu_calibrating"): return "calibration unavailable"
        if now - min(self.last_emg_host, self.last_imu_host) > 0.3: return "data stream stale >300 ms"
        return ""

    def _disable_control(self, reason: str) -> None:
        if self.control_active: self._log(f"Computer Control disabled: {reason}")
        self.control_active = False; self.mouse_mapper.reset()
        if hasattr(self, "control_panel"): self.control_panel.set_enabled_state(False, reason)

    def _safety_tick(self) -> None:
        if self.control_active:
            reason = self._control_block_reason()
            if reason: self._disable_control(reason)
        if self.worker is not None and int(time.monotonic() * 10) % 10 == 0: self.worker.send_command("STATUS")

    def _refresh_plots(self) -> None:
        for plot in self.all_plots: plot.refresh()

    def _toggle_expand_plot(self, target: ScientificPlot) -> None:
        expanding = self._expanded_plot is None
        self._expanded_plot = target if expanding else None
        for plot in self.all_plots: plot.setVisible(not expanding or plot is target)

    def _log(self, message: str) -> None:
        if hasattr(self, "event_log"): self.event_log.appendPlainText(message)

    def closeEvent(self, event) -> None:  # noqa: N802
        self._disable_control("application closing")
        self.hotkey.unregister()
        if self.experiment.state in {"waiting_calibration", "running", "paused"}:
            self.experiment.stop("aborted_by_user", emit_finished=False)
            self._send_phase_marker("aborted_by_user", 0, ""); self._send("RECORD STOP")
            self.recorder.finalize("aborted_by_user", transport=self.transport, hardware_status=self.latest_status)
        if self.worker is not None:
            self.worker.stop(); self.worker.wait(2000)
        event.accept()


def main() -> int:
    pg.setConfigOptions(antialias=False, background=QColor("#161a1d"), foreground="w")
    app = QApplication(sys.argv)
    window = MainWindow(); window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
