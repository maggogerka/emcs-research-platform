"""Crash-tolerant scientific session recording with separate samples and events."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Iterable
import uuid

from .protocol import ADS_VOLTS_PER_BIT
from .version import APP_VERSION


SAMPLE_FIELDS = [
    "record_type", "timestamp_us", "sample_index", "emg_timer_gap",
    "imu_sample_index", "imu_timer_gap", "ads_raw", "ads_voltage",
    "ac_voltage", "envelope_raw", "envelope_voltage", "adaptive_on_voltage",
    "adaptive_off_voltage", "fixed_on_voltage", "fixed_off_voltage",
    "detector_state", "leads", "lo_minus", "lo_plus", "motion",
    "fixed_active", "adaptive_active", "fixed_event", "adaptive_event",
    "fixed_release", "adaptive_release", "ax", "ay", "az", "gx", "gy", "gz",
    # Retained empty for compatibility; authoritative labels are in events.csv.
    "phase", "repetition", "intensity", "on_coefficient", "off_coefficient",
    "motion_gyro_dps", "motion_accel_delta_g",
]

EVENT_FIELDS = [
    "device_timestamp_us", "host_timestamp_utc", "event_type", "detector",
    "state", "envelope_voltage", "marker_id", "phase", "trial",
    "prescribed_intensity", "message",
]


@dataclass(frozen=True, slots=True)
class SessionPaths:
    root: Path
    samples: Path
    events: Path
    metadata: Path
    metrics: Path
    report: Path
    figures: Path


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def git_commit(project_root: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(project_root), "rev-parse", "HEAD"],
            text=True,
            timeout=2,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return "unknown"


def validate_participant_id(value: str) -> str:
    value = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
        raise ValueError("Participant ID must be an anonymous 1–64 character code")
    return value


class SessionRecorder:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self.paths: SessionPaths | None = None
        self.metadata: dict[str, Any] = {}
        self._sample_file = None
        self._event_file = None
        self._sample_writer: csv.DictWriter | None = None
        self._event_writer: csv.DictWriter | None = None
        self.sample_counts = {"emg": 0, "imu": 0}
        self.first_timestamps: dict[str, int] = {}
        self.last_timestamps: dict[str, int] = {}

    @property
    def active(self) -> bool:
        return self._sample_writer is not None

    def start(self, metadata: dict[str, Any], base_dir: Path | None = None) -> SessionPaths:
        if self.active:
            raise RuntimeError("a recording session is already active")
        participant_id = validate_participant_id(str(metadata.get("participant_id", "")))
        session_id = f"{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:8]}"
        root = (base_dir or self.project_root / "data" / "recordings") / session_id
        root.mkdir(parents=True, exist_ok=False)
        figures = root / "figures"
        figures.mkdir()
        self.paths = SessionPaths(
            root, root / "samples.csv", root / "events.csv", root / "metadata.json",
            root / "metrics.json", root / "report.html", figures,
        )
        self.metadata = {
            **metadata,
            "session_id": session_id,
            "participant_id": participant_id,
            "application_version": APP_VERSION,
            "git_commit": git_commit(self.project_root),
            "started_utc": utc_now(),
            "outcome": "in_progress",
            "format_version": 2,
            "label_source": "device_phase_markers",
        }
        self._sample_file = self.paths.samples.open("w", newline="", encoding="utf-8")
        self._event_file = self.paths.events.open("w", newline="", encoding="utf-8")
        self._sample_writer = csv.DictWriter(
            self._sample_file, fieldnames=SAMPLE_FIELDS, extrasaction="ignore"
        )
        self._event_writer = csv.DictWriter(
            self._event_file, fieldnames=EVENT_FIELDS, extrasaction="ignore"
        )
        self._sample_writer.writeheader()
        self._event_writer.writeheader()
        self._write_metadata()
        return self.paths

    def _write_metadata(self) -> None:
        if self.paths is not None:
            self.paths.metadata.write_text(
                json.dumps(self.metadata, indent=2, ensure_ascii=False), encoding="utf-8"
            )

    def _track(self, kind: str, timestamp_us: int) -> None:
        self.sample_counts[kind] += 1
        self.first_timestamps.setdefault(kind, timestamp_us)
        self.last_timestamps[kind] = timestamp_us

    def _write_samples(self, rows: Iterable[dict[str, Any]]) -> None:
        if self._sample_writer is None or self._sample_file is None:
            return
        self._sample_writer.writerows(rows)
        self._sample_file.flush()

    def write_emg(
        self,
        batch: dict[str, Any],
        ac_voltages: list[float],
        algorithm: dict[str, float],
    ) -> None:
        rows: list[dict[str, Any]] = []
        for sample, ac_voltage in zip(batch["samples"], ac_voltages, strict=True):
            timestamp = int(sample["timestamp_us"])
            self._track("emg", timestamp)
            rows.append(
                {
                    **sample,
                    "record_type": "emg",
                    "emg_timer_gap": sample["timer_gap"],
                    "ac_voltage": ac_voltage,
                    "adaptive_on_voltage": batch["adaptive_on"] * ADS_VOLTS_PER_BIT,
                    "adaptive_off_voltage": batch["adaptive_off"] * ADS_VOLTS_PER_BIT,
                    "fixed_on_voltage": batch["fixed_on"] * ADS_VOLTS_PER_BIT,
                    "fixed_off_voltage": batch["fixed_off"] * ADS_VOLTS_PER_BIT,
                    "detector_state": batch["detector_state_name"],
                    "leads": batch["leads"],
                    "lo_minus": int(batch["lo_minus"]),
                    "lo_plus": int(batch["lo_plus"]),
                    "motion": int(batch["motion"]),
                    "phase": "",
                    "repetition": "",
                    "intensity": "",
                    **algorithm,
                }
            )
        self._write_samples(rows)

    def write_imu(self, batch: dict[str, Any], algorithm: dict[str, float]) -> None:
        rows: list[dict[str, Any]] = []
        for sample in batch["samples"]:
            timestamp = int(sample["timestamp_us"])
            self._track("imu", timestamp)
            rows.append(
                {
                    "record_type": "imu",
                    "timestamp_us": timestamp,
                    "imu_sample_index": sample["sample_index"],
                    "imu_timer_gap": sample["timer_gap"],
                    **{name: sample[name] for name in ("ax", "ay", "az", "gx", "gy", "gz")},
                    "phase": "",
                    "repetition": "",
                    "intensity": "",
                    **algorithm,
                }
            )
        self._write_samples(rows)

    def write_event(self, event: dict[str, Any]) -> None:
        if self._event_writer is None or self._event_file is None:
            return
        row = {"host_timestamp_utc": utc_now(), **event}
        self._event_writer.writerow(row)
        self._event_file.flush()

    def write_device_event(self, event: dict[str, Any]) -> None:
        self.write_event(
            {
                "device_timestamp_us": event["timestamp_us"],
                "event_type": event["event"],
                "detector": event["detector"],
                "state": event["state"],
                "envelope_voltage": event["envelope_voltage"],
            }
        )

    def write_marker(self, marker: dict[str, Any]) -> None:
        self.write_event(
            {
                "device_timestamp_us": marker["timestamp_us"],
                "event_type": "PHASE_MARKER",
                "detector": "system",
                "marker_id": marker["marker_id"],
                "phase": marker["phase"],
                "trial": marker["trial"],
                "prescribed_intensity": marker["prescribed_intensity"],
            }
        )

    def finalize(
        self,
        outcome: str,
        *,
        transport: dict[str, int] | None = None,
        hardware_status: dict[str, Any] | None = None,
        error: str = "",
    ) -> SessionPaths | None:
        if self.paths is None:
            return None
        paths = self.paths
        if self._sample_file is not None:
            self._sample_file.close()
        if self._event_file is not None:
            self._event_file.close()
        self._sample_file = self._event_file = None
        self._sample_writer = self._event_writer = None
        actual_rates: dict[str, float | None] = {}
        for kind in ("emg", "imu"):
            count = self.sample_counts[kind]
            span = self.last_timestamps.get(kind, 0) - self.first_timestamps.get(kind, 0)
            actual_rates[f"{kind}_hz"] = (
                (count - 1) * 1_000_000.0 / span if count > 1 and span > 0 else None
            )
        self.metadata.update(
            {
                "finished_utc": utc_now(),
                "outcome": outcome,
                "error": error or None,
                "sample_counts": dict(self.sample_counts),
                "actual_sample_rates": actual_rates,
                "transport": transport or {},
                "hardware_status": hardware_status or {},
            }
        )
        self._write_metadata()
        self.paths = None
        return paths

    @staticmethod
    def analysis_command(paths: SessionPaths) -> tuple[str, list[str]]:
        script = Path(__file__).resolve().parents[1] / "analysis" / "analyze_session.py"
        return sys.executable, [str(script), str(paths.root)]
