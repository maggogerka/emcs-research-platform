"""Capture a real EMCS binary stream to CSV and report transport integrity."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime
import json
from pathlib import Path
import sys
import time
from typing import Any

import serial

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from desktop_app.protocol import FrameParser, decode_frame  # noqa: E402


FIELDS = [
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


def send(port: serial.Serial, command: str) -> None:
    port.write((command + "\n").encode("ascii"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM13")
    parser.add_argument("--duration", type=float, default=60.0)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output = args.output or (
        PROJECT_ROOT / "data" / "recordings" / f"hardware_{datetime.now():%Y%m%d_%H%M%S}.csv"
    )
    output.parent.mkdir(parents=True, exist_ok=True)

    parser = FrameParser()
    latest_imu = {key: float("nan") for key in ("ax", "ay", "az", "gx", "gy", "gz")}
    latest_status: dict[str, Any] = {}
    frame_sequence: int | None = None
    frame_gaps = 0
    emg_count = 0
    imu_count = 0
    emg_reported_drops = 0
    imu_reported_drops = 0
    first_emg_timestamp: int | None = None
    last_emg_timestamp: int | None = None
    first_imu_timestamp: int | None = None
    last_imu_timestamp: int | None = None
    last_emg_index: int | None = None
    last_imu_index: int | None = None
    emg_index_gaps = 0
    imu_index_gaps = 0
    leads_seen: set[int] = set()
    errors: list[str] = []

    print(f"Opening {args.port}; the board may reset while the serial port is opened.")
    with serial.Serial(args.port, 460800, timeout=0.05, write_timeout=0.5) as port:
        time.sleep(3.5)
        port.reset_input_buffer()
        send(port, "STATUS")
        send(port, "RECORD START")
        send(port, "STREAM START")
        capture_started = time.monotonic()

        with output.open("w", newline="", encoding="utf-8") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=FIELDS, extrasaction="ignore")
            writer.writeheader()

            while time.monotonic() - capture_started < args.duration:
                data = port.read(max(port.in_waiting, 1))
                for frame in parser.feed(data):
                    if frame_sequence is not None:
                        expected = (frame_sequence + 1) & 0xFFFFFFFF
                        if frame.sequence != expected:
                            frame_gaps += (frame.sequence - expected) & 0xFFFFFFFF
                    frame_sequence = frame.sequence
                    kind, decoded = decode_frame(frame)
                    if kind == "status":
                        latest_status = decoded
                    elif kind == "error":
                        errors.append(decoded)
                    elif kind == "imu":
                        imu_reported_drops += decoded["dropped_samples"]
                        rows = []
                        for sample in decoded["samples"]:
                            if last_imu_index is not None and sample["sample_index"] != last_imu_index + 1:
                                imu_index_gaps += max(0, sample["sample_index"] - last_imu_index - 1)
                            last_imu_index = sample["sample_index"]
                            latest_imu = {key: sample[key] for key in latest_imu}
                            first_imu_timestamp = first_imu_timestamp or sample["timestamp_us"]
                            last_imu_timestamp = sample["timestamp_us"]
                            imu_count += 1
                            rows.append(
                                {
                                    "record_type": "imu",
                                    "timestamp_us": sample["timestamp_us"],
                                    "imu_sample_index": sample["sample_index"],
                                    "imu_timer_gap": sample["timer_gap"],
                                    **latest_imu,
                                    "phase": "",
                                    "repetition": 0,
                                    "intensity": "",
                                    "on_coefficient": latest_status.get(
                                        "on_coefficient", 6.0
                                    ),
                                    "off_coefficient": latest_status.get(
                                        "off_coefficient", 3.0
                                    ),
                                    "motion_gyro_dps": latest_status.get(
                                        "motion_gyro_dps", 20.0
                                    ),
                                    "motion_accel_delta_g": latest_status.get(
                                        "motion_accel_delta_g", 0.25
                                    ),
                                }
                            )
                        writer.writerows(rows)
                    elif kind == "emg":
                        emg_reported_drops += decoded["dropped_samples"]
                        leads_seen.add(decoded["leads"])
                        rows = []
                        for sample in decoded["samples"]:
                            if last_emg_index is not None and sample["sample_index"] != last_emg_index + 1:
                                emg_index_gaps += max(0, sample["sample_index"] - last_emg_index - 1)
                            last_emg_index = sample["sample_index"]
                            first_emg_timestamp = first_emg_timestamp or sample["timestamp_us"]
                            last_emg_timestamp = sample["timestamp_us"]
                            emg_count += 1
                            rows.append(
                                {
                                    **sample,
                                    "record_type": "emg",
                                    "emg_timer_gap": sample["timer_gap"],
                                    "adaptive_on_voltage": decoded["adaptive_on"] * 0.000125,
                                    "adaptive_off_voltage": decoded["adaptive_off"] * 0.000125,
                                    "fixed_on_voltage": decoded["fixed_on"] * 0.000125,
                                    "fixed_off_voltage": decoded["fixed_off"] * 0.000125,
                                    "detector_state": decoded["detector_state_name"],
                                    "leads": decoded["leads"],
                                    "lo_minus": int(decoded["lo_minus"]),
                                    "lo_plus": int(decoded["lo_plus"]),
                                    "motion": int(decoded["motion"]),
                                    **latest_imu,
                                    "phase": "",
                                    "repetition": 0,
                                    "intensity": "",
                                    "on_coefficient": latest_status.get("on_coefficient", 6.0),
                                    "off_coefficient": latest_status.get("off_coefficient", 3.0),
                                    "motion_gyro_dps": latest_status.get("motion_gyro_dps", 20.0),
                                    "motion_accel_delta_g": latest_status.get(
                                        "motion_accel_delta_g", 0.25
                                    ),
                                }
                            )
                        writer.writerows(rows)
            csv_file.flush()

        send(port, "STATUS")
        time.sleep(0.3)
        for frame in parser.feed(port.read(port.in_waiting)):
            kind, decoded = decode_frame(frame)
            if kind == "status":
                latest_status = decoded
            elif kind == "error":
                errors.append(decoded)
        send(port, "STREAM STOP")
        send(port, "RECORD STOP")

    emg_span_s = (
        (last_emg_timestamp - first_emg_timestamp) / 1_000_000
        if first_emg_timestamp is not None and last_emg_timestamp is not None
        else 0.0
    )
    imu_span_s = (
        (last_imu_timestamp - first_imu_timestamp) / 1_000_000
        if first_imu_timestamp is not None and last_imu_timestamp is not None
        else 0.0
    )
    summary = {
        "file": str(output),
        "requested_duration_s": args.duration,
        "emg_samples": emg_count,
        "emg_span_s": emg_span_s,
        "emg_rate_hz": (emg_count - 1) / emg_span_s if emg_span_s > 0 else 0.0,
        "emg_reported_drops": emg_reported_drops,
        "emg_index_gaps": emg_index_gaps,
        "imu_samples": imu_count,
        "imu_span_s": imu_span_s,
        "imu_rate_hz": (imu_count - 1) / imu_span_s if imu_span_s > 0 else 0.0,
        "imu_reported_drops": imu_reported_drops,
        "imu_index_gaps": imu_index_gaps,
        "frame_sequence_gaps": frame_gaps,
        "crc_errors": parser.crc_errors,
        "discarded_boot_bytes": parser.discarded_bytes,
        "leads_values_seen": sorted(leads_seen),
        "firmware_status": latest_status,
        "runtime_errors": errors,
    }
    summary_path = output.with_suffix(".summary.json")
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"CSV: {output}")
    print(f"Summary: {summary_path}")
    return 0 if emg_count and imu_count and parser.crc_errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
