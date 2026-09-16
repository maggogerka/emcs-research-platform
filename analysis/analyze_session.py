"""Analyze a v2 session directory or a compatible legacy EMCS CSV."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from analysis.metrics import (  # noqa: E402
    detector_metrics,
    label_by_markers,
    legacy_events_and_markers,
    phase_intervals,
)
from analysis.reporting import create_all_figures, write_html_report  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="session directory or legacy CSV")
    parser.add_argument("--output", type=Path, help="override figure/output directory")
    parser.add_argument("--bootstrap", type=int, default=2000, help="trial bootstrap iterations")
    return parser.parse_args()


def read_source(path: Path) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any], Path, Path, Path]:
    if path.is_dir():
        samples_path = path / "samples.csv"
        events_path = path / "events.csv"
        metadata_path = path / "metadata.json"
        if not samples_path.exists() or not events_path.exists():
            raise SystemExit("Session directory must contain samples.csv and events.csv")
        samples = pd.read_csv(samples_path, low_memory=False)
        events = pd.read_csv(events_path, low_memory=False)
        metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.exists() else {}
        return samples, events, metadata, path / "figures", path / "metrics.json", path / "report.html"
    samples = pd.read_csv(path, low_memory=False)
    events_path = path.with_name("events.csv")
    events = pd.read_csv(events_path, low_memory=False) if events_path.exists() else legacy_events_and_markers(samples)
    output = Path("figures/generated")
    return samples, events, {}, output, output / "metrics.json", output / "report.html"


def numeric(data: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    for column in columns:
        if column in data:
            data[column] = pd.to_numeric(data[column], errors="coerce")
    return data


def envelope_statistics(data: pd.DataFrame) -> dict[str, Any]:
    result: dict[str, Any] = {}
    groups = {
        "negative_phases": data["phase"].isin(["calibration_rest", "prepare", "rest"]),
        "contract": data["phase"].eq("contract"),
    }
    for name, selector in groups.items():
        values = pd.to_numeric(data.loc[selector, "envelope_voltage"], errors="coerce").dropna()
        result[name] = {
            "count": int(len(values)),
            "mean_v": float(values.mean()) if len(values) else None,
            "median_v": float(values.median()) if len(values) else None,
            "std_v": float(values.std(ddof=0)) if len(values) else None,
        }
    by_intensity: dict[str, Any] = {}
    for intensity, group in data[data["phase"].eq("contract")].groupby("prescribed_intensity"):
        values = pd.to_numeric(group["envelope_voltage"], errors="coerce").dropna()
        by_intensity[str(intensity)] = {
            "count": int(len(values)),
            "median_v": float(values.median()) if len(values) else None,
            "iqr_v": float(values.quantile(0.75) - values.quantile(0.25)) if len(values) else None,
        }
    result["by_prescribed_intensity"] = by_intensity
    return result


def sampling_metrics(data: pd.DataFrame, kind: str, index_column: str, gap_column: str) -> dict[str, Any]:
    if data.empty:
        return {"samples": 0, "rate_hz": None, "index_gaps": 0}
    timestamps = pd.to_numeric(data["timestamp_us"], errors="coerce").dropna().astype(np.int64)
    span_s = (timestamps.iloc[-1] - timestamps.iloc[0]) / 1_000_000.0 if len(timestamps) > 1 else 0.0
    indices = pd.to_numeric(
        data[index_column] if index_column in data else pd.Series(dtype=float), errors="coerce"
    ).dropna()
    gaps = int(np.maximum(indices.diff().fillna(1).to_numpy() - 1, 0).sum()) if len(indices) else 0
    reported = pd.to_numeric(
        data[gap_column] if gap_column in data else pd.Series(1, index=data.index), errors="coerce"
    ).fillna(1)
    return {
        "samples": int(len(data)),
        "span_s": span_s,
        "rate_hz": (len(timestamps) - 1) / span_s if span_s > 0 else None,
        "index_gaps": gaps,
        "timer_gaps": int(np.maximum(reported.to_numpy() - 1, 0).sum()),
        "stream": kind,
    }


def main() -> int:
    args = parse_args()
    samples, events, metadata, default_figures, metrics_path, report_path = read_source(args.input)
    if samples.empty:
        raise SystemExit("The recording contains no samples")
    required = {"record_type", "timestamp_us"}
    missing = sorted(required - set(samples.columns))
    if missing:
        # v1 files before record_type stored an EMG row with a repeated latest IMU sample.
        if missing == ["record_type"] and "ads_voltage" in samples:
            samples["record_type"] = "emg"
        else:
            raise SystemExit(f"Missing required sample columns: {', '.join(missing)}")
    samples = numeric(
        samples,
        [
            "timestamp_us", "ads_voltage", "ac_voltage", "envelope_voltage",
            "adaptive_on_voltage", "adaptive_off_voltage", "fixed_on_voltage",
            "fixed_off_voltage", "ax", "ay", "az", "gx", "gy", "gz",
        ],
    )
    emg = samples[samples["record_type"].eq("emg")].copy()
    imu = samples[samples["record_type"].eq("imu")].copy()
    if emg.empty:
        raise SystemExit("The recording contains no EMG samples")
    if "ac_voltage" not in emg or emg["ac_voltage"].isna().all():
        # Compatibility-only desktop estimate; firmware envelope remains authoritative.
        baseline = emg["ads_voltage"].ewm(alpha=0.001162, adjust=False).mean()
        emg["ac_voltage"] = emg["ads_voltage"] - baseline
    emg = label_by_markers(emg, events)
    imu = label_by_markers(imu, events)
    end_timestamp = int(max(emg["timestamp_us"].max(), imu["timestamp_us"].max() if not imu.empty else 0))
    intervals = phase_intervals(events, end_timestamp)
    has_ground_truth = any(interval.phase == "contract" for interval in intervals)
    seed = int(metadata.get("protocol", {}).get("seed", 0) or 0)
    results: dict[str, Any] = {
        "source": str(args.input),
        "session_id": metadata.get("session_id"),
        "session_outcome": metadata.get("outcome", "unknown"),
        "ground_truth_available": has_ground_truth,
        "ground_truth_source": "device phase markers" if intervals else "none",
        "sampling": {
            "emg": sampling_metrics(emg, "emg", "sample_index", "emg_timer_gap"),
            "imu": sampling_metrics(imu, "imu", "imu_sample_index", "imu_timer_gap"),
        },
        "envelope": envelope_statistics(emg),
    }
    if has_ground_truth:
        results["fixed_detector"] = detector_metrics(
            events, intervals, "fixed", seed=seed, bootstrap_iterations=args.bootstrap
        )
        results["adaptive_detector"] = detector_metrics(
            events, intervals, "adaptive", seed=seed, bootstrap_iterations=args.bootstrap
        )
    else:
        results["metrics_note"] = (
            "No device-timestamped contract markers are present; TP/FP/FN and "
            "detector performance were not calculated."
        )
    figures_dir = args.output or default_figures
    if args.output is not None:
        metrics_path = figures_dir / "metrics.json"
        report_path = figures_dir / "report.html"
    figures_dir.mkdir(parents=True, exist_ok=True)
    figure_paths = create_all_figures(emg, imu, events, intervals, results, figures_dir)
    results["figures"] = [str(path) for path in figure_paths]
    metrics_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    write_html_report(report_path, results, metadata, figure_paths)
    print(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"Metrics: {metrics_path}")
    print(f"Report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
