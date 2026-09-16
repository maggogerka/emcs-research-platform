from __future__ import annotations

import struct
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

import pandas as pd
from PySide6.QtCore import QSettings

from analysis.metrics import PhaseInterval, detector_metrics, label_by_markers
from analysis.reporting import create_all_figures, write_html_report
from desktop_app.control import MouseMapper
from desktop_app.experiment import ExperimentSchedule, ProtocolConfig
from desktop_app.protocol import (
    CRC, FRAME_MAGIC, FRAME_PHASE_MARKER, FRAME_VERSION, HEADER, PHASE_MARKER,
    FrameParser, crc16_ccitt, decode_frame,
)
from desktop_app.settings import AlgorithmSettings, ControlSettings, SettingsStore
from desktop_app.session import SessionPaths, SessionRecorder


class ProtocolTests(unittest.TestCase):
    def test_phase_marker_survives_fragmented_transport(self) -> None:
        payload = PHASE_MARKER.pack(42, 7, 3, 2)
        header = HEADER.pack(FRAME_MAGIC, FRAME_VERSION, FRAME_PHASE_MARKER, len(payload), 11, 987654)
        encoded = header + payload + CRC.pack(crc16_ccitt(header[2:] + payload))
        parser = FrameParser(); frames = []
        for position in range(0, len(encoded), 3):
            frames.extend(parser.feed(encoded[position:position + 3]))
        kind, marker = decode_frame(frames[0])
        self.assertEqual(kind, "marker")
        self.assertEqual(marker["timestamp_us"], 987654)
        self.assertEqual((marker["trial"], marker["phase"], marker["prescribed_intensity"]), (7, "contract", "medium"))


class ScheduleTests(unittest.TestCase):
    def test_schedule_is_balanced_and_seeded(self) -> None:
        config = ProtocolConfig(repetitions=31, seed=77)
        first = ExperimentSchedule(config); second = ExperimentSchedule(config)
        self.assertEqual(first.intensities, second.intensities)
        counts = first.intensity_counts()
        self.assertLessEqual(max(counts.values()) - min(counts.values()), 1)
        self.assertEqual(len(first.phases), 93)


class MarkerAndMetricTests(unittest.TestCase):
    def setUp(self) -> None:
        self.events = pd.DataFrame([
            {"device_timestamp_us": 0, "event_type": "PHASE_MARKER", "detector": "system", "marker_id": 1,
             "phase": "prepare", "trial": 1, "prescribed_intensity": "weak"},
            {"device_timestamp_us": 1_000_000, "event_type": "PHASE_MARKER", "detector": "system", "marker_id": 2,
             "phase": "contract", "trial": 1, "prescribed_intensity": "weak"},
            {"device_timestamp_us": 3_000_000, "event_type": "PHASE_MARKER", "detector": "system", "marker_id": 3,
             "phase": "rest", "trial": 1, "prescribed_intensity": "weak"},
            {"device_timestamp_us": 1_250_000, "event_type": "CONTRACTION_START", "detector": "fixed"},
            {"device_timestamp_us": 3_500_000, "event_type": "CONTRACTION_START", "detector": "fixed"},
        ])

    def test_device_timestamp_labels_samples(self) -> None:
        samples = pd.DataFrame({"timestamp_us": [500_000, 1_500_000, 3_500_000]})
        labelled = label_by_markers(samples, self.events)
        self.assertEqual(labelled["phase"].tolist(), ["prepare", "contract", "rest"])

    def test_metrics_use_negative_phase_time_and_cue_latency(self) -> None:
        intervals = [
            PhaseInterval(0, 1_000_000, "prepare", 1, "weak"),
            PhaseInterval(1_000_000, 3_000_000, "contract", 1, "weak"),
            PhaseInterval(3_000_000, 4_000_000, "rest", 1, "weak"),
        ]
        result = detector_metrics(self.events, intervals, "fixed", seed=1, bootstrap_iterations=100)
        self.assertEqual((result["tp"], result["fp"], result["fn"]), (1, 1, 0))
        self.assertAlmostEqual(result["cue_to_detection_latency_ms_mean"], 250.0)
        self.assertAlmostEqual(result["false_positives_per_minute"], 30.0)
        self.assertIsNotNone(result["bootstrap_95_ci"]["recall"])


class SettingsAndMappingTests(unittest.TestCase):
    def test_named_settings_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(QSettings(str(Path(directory) / "settings.ini"), QSettings.Format.IniFormat))
            expected = AlgorithmSettings(7.0, 2.5, 25.0, 0.4)
            store.save_profile("algorithm", "lab", expected)
            self.assertEqual(store.load_profile("algorithm", "lab", AlgorithmSettings), expected)

    def test_mapping_is_bounded_without_real_input_injection(self) -> None:
        mapper = MouseMapper()
        settings = ControlSettings(sensitivity=100.0, dead_zone_dps=0.0, smoothing=0.0, max_speed_px_s=500.0)
        vx, vy = mapper.map_velocity({"gx": 0, "gy": 100, "gz": 100}, settings)
        self.assertLessEqual((vx * vx + vy * vy) ** 0.5, 500.0001)
        mapper.reset()
        self.assertEqual(mapper.displacement({"gx": 0, "gy": 0, "gz": 0}, settings, 0.01), (0, 0))

    def test_packaged_analysis_reenters_executable(self) -> None:
        root = Path("session")
        paths = SessionPaths(root, root / "samples.csv", root / "events.csv", root / "metadata.json",
                             root / "metrics.json", root / "report.html", root / "figures")
        with patch("desktop_app.session.sys.frozen", True, create=True), patch(
            "desktop_app.session.sys.executable", "EMCSResearchPlatform.exe"
        ):
            self.assertEqual(
                SessionRecorder.analysis_command(paths),
                ("EMCSResearchPlatform.exe", ["--analyze", "session"]),
            )


class ReportingTests(unittest.TestCase):
    def test_complete_marker_session_creates_ten_png_svg_pairs_and_html(self) -> None:
        timestamps = list(range(0, 4_000_000, 40_000))
        emg = pd.DataFrame({
            "timestamp_us": timestamps,
            "ads_voltage": [1.5 + (index % 10) * 0.001 for index in range(len(timestamps))],
            "ac_voltage": [(index % 10 - 5) * 0.001 for index in range(len(timestamps))],
            "envelope_voltage": [0.01 + (0.08 if 25 <= index < 75 else 0) for index in range(len(timestamps))],
            "fixed_on_voltage": [0.05] * len(timestamps), "adaptive_on_voltage": [0.06] * len(timestamps),
            "phase": ["prepare" if value < 1_000_000 else "contract" if value < 3_000_000 else "rest" for value in timestamps],
            "prescribed_intensity": ["weak"] * len(timestamps),
        })
        imu = pd.DataFrame({"timestamp_us": timestamps, **{name: [0.0] * len(timestamps) for name in ("ax", "ay", "gx", "gy", "gz")}, "az": [1.0] * len(timestamps)})
        events = pd.DataFrame([
            {"device_timestamp_us": 1_250_000, "event_type": "CONTRACTION_START", "detector": "fixed"},
            {"device_timestamp_us": 1_300_000, "event_type": "CONTRACTION_START", "detector": "adaptive"},
        ])
        intervals = [PhaseInterval(0, 1_000_000, "prepare", 1, "weak"), PhaseInterval(1_000_000, 3_000_000, "contract", 1, "weak"), PhaseInterval(3_000_000, 4_000_000, "rest", 1, "weak")]
        fixed = detector_metrics(events, intervals, "fixed", bootstrap_iterations=20)
        adaptive = detector_metrics(events, intervals, "adaptive", bootstrap_iterations=20)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            paths = create_all_figures(emg, imu, events, intervals, {"fixed_detector": fixed, "adaptive_detector": adaptive}, output)
            self.assertEqual(len(paths), 20)
            self.assertTrue(all(path.exists() for path in paths))
            report = output / "report.html"
            write_html_report(report, {"fixed_detector": fixed, "adaptive_detector": adaptive}, {}, paths)
            self.assertIn("Cue-to-detection", report.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
