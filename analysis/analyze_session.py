"""Analyze a recorded EMCS CSV and create reproducible 300 dpi figures."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="CSV created by the EMCS desktop application")
    parser.add_argument("--output", type=Path, default=Path("figures/generated"))
    return parser.parse_args()


def contraction_intervals(data: pd.DataFrame) -> list[tuple[int, int, int, str]]:
    contract = data[data["phase"] == "contract"]
    intervals: list[tuple[int, int, int, str]] = []
    if contract.empty:
        return intervals
    for repetition, group in contract.groupby("repetition", sort=True):
        intervals.append(
            (
                int(group["timestamp_us"].min()),
                int(group["timestamp_us"].max()),
                int(repetition),
                str(group["intensity"].iloc[0]),
            )
        )
    return intervals


def detector_metrics(
    data: pd.DataFrame, event_column: str, intervals: list[tuple[int, int, int, str]]
) -> dict[str, Any]:
    event_mask = data[event_column].map(
        lambda value: str(value).strip().lower() in {"1", "true"}
    )
    predicted = data.loc[event_mask, "timestamp_us"].astype(int).tolist()
    matched_predictions: set[int] = set()
    latencies_ms: list[float] = []
    tp = 0
    for start, end, _, _ in intervals:
        candidates = [
            (index, timestamp)
            for index, timestamp in enumerate(predicted)
            if index not in matched_predictions and start <= timestamp <= end
        ]
        if candidates:
            index, timestamp = candidates[0]
            matched_predictions.add(index)
            tp += 1
            latencies_ms.append((timestamp - start) / 1000.0)
    fn = len(intervals) - tp
    fp = len(predicted) - len(matched_predictions)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    duration_min = (
        (data["timestamp_us"].max() - data["timestamp_us"].min()) / 60_000_000.0
        if len(data) > 1
        else 0.0
    )
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "false_positives_per_minute": fp / duration_min if duration_min > 0 else None,
        "recognition_latency_ms_mean": float(np.mean(latencies_ms)) if latencies_ms else None,
        "recognition_latency_ms_median": float(np.median(latencies_ms)) if latencies_ms else None,
        "latencies_ms": latencies_ms,
    }


def describe_envelope(data: pd.DataFrame) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, selector in {
        "rest": data["phase"].isin(["baseline_rest", "rest"]),
        "contraction": data["phase"] == "contract",
    }.items():
        values = data.loc[selector, "envelope_voltage"].dropna().to_numpy()
        result[name] = (
            {
                "count": int(values.size),
                "mean_v": float(np.mean(values)),
                "median_v": float(np.median(values)),
                "std_v": float(np.std(values)),
            }
            if values.size
            else {"count": 0, "mean_v": None, "median_v": None, "std_v": None}
        )
    return result


def downsample(data: pd.DataFrame, maximum: int = 100_000) -> pd.DataFrame:
    step = max(1, len(data) // maximum)
    return data.iloc[::step]


def boolean_values(series: pd.Series) -> pd.Series:
    """Normalize boolean CSV values written as 0/1 or true/false."""
    return series.map(lambda value: str(value).strip().lower() in {"1", "true"}).astype(int)


def create_figures(
    data: pd.DataFrame,
    imu_data: pd.DataFrame,
    output: Path,
    has_ground_truth: bool,
) -> list[str]:
    output.mkdir(parents=True, exist_ok=True)
    plot_data = downsample(data)
    time_s = (plot_data["timestamp_us"] - plot_data["timestamp_us"].iloc[0]) / 1_000_000.0
    files: list[str] = []

    fig, axes = plt.subplots(2, 1, figsize=(12, 7), sharex=True, constrained_layout=True)
    axes[0].plot(time_s, plot_data["ads_voltage"], linewidth=0.45, color="#1565c0")
    axes[0].set_ylabel("AIN0 voltage (V)")
    axes[0].set_title("Recorded EMG front-end signal")
    axes[0].grid(alpha=0.25)
    axes[1].plot(time_s, plot_data["envelope_voltage"], label="RMS envelope", linewidth=0.8)
    axes[1].plot(time_s, plot_data["adaptive_on_voltage"], label="Adaptive Ton", linewidth=0.8)
    axes[1].plot(time_s, plot_data["adaptive_off_voltage"], label="Adaptive Toff", linewidth=0.8)
    axes[1].plot(
        time_s, plot_data["fixed_on_voltage"], "--", label="Fixed Ton", linewidth=0.7
    )
    axes[1].plot(
        time_s, plot_data["fixed_off_voltage"], "--", label="Fixed Toff", linewidth=0.7
    )
    axes[1].set_xlabel("Time (s)")
    axes[1].set_ylabel("Envelope (V)")
    axes[1].grid(alpha=0.25)
    axes[1].legend(ncol=3, fontsize=8)
    path = output / "emg-signal-and-thresholds.png"
    fig.savefig(path, dpi=300)
    plt.close(fig)
    files.append(str(path))

    imu_columns = ["ax", "ay", "az", "gx", "gy", "gz"]
    if not imu_data.empty and all(column in imu_data for column in imu_columns):
        imu_plot_data = downsample(imu_data)
        imu_time_s = (
            imu_plot_data["timestamp_us"] - imu_plot_data["timestamp_us"].iloc[0]
        ) / 1_000_000.0
        fig, axes = plt.subplots(2, 1, figsize=(12, 7), sharex=True, constrained_layout=True)
        for axis in ("ax", "ay", "az"):
            axes[0].plot(imu_time_s, imu_plot_data[axis], label=axis, linewidth=0.7)
        axes[0].set_ylabel("Acceleration (g)")
        axes[0].grid(alpha=0.25)
        axes[0].legend()
        for axis in ("gx", "gy", "gz"):
            axes[1].plot(imu_time_s, imu_plot_data[axis], label=axis, linewidth=0.7)
        axes[1].set_xlabel("Time (s)")
        axes[1].set_ylabel("Angular velocity (deg/s)")
        axes[1].grid(alpha=0.25)
        axes[1].legend()
        path = output / "imu-motion.png"
        fig.savefig(path, dpi=300)
        plt.close(fig)
        files.append(str(path))

    if has_ground_truth:
        fig, ax = plt.subplots(figsize=(12, 4), constrained_layout=True)
        ax.step(time_s, plot_data["phase"].eq("contract").astype(int), where="post", label="Label")
        ax.step(
            time_s,
            boolean_values(plot_data["fixed_active"]) + 1.2,
            where="post",
            label="Fixed detector",
        )
        ax.step(
            time_s,
            boolean_values(plot_data["adaptive_active"]) + 2.4,
            where="post",
            label="Adaptive detector",
        )
        ax.set_yticks([0.5, 1.7, 2.9], ["Ground truth", "Fixed", "Adaptive"])
        ax.set_xlabel("Time (s)")
        ax.set_title("Detector comparison")
        ax.grid(alpha=0.25)
        path = output / "detector-comparison.png"
        fig.savefig(path, dpi=300)
        plt.close(fig)
        files.append(str(path))
    return files


def main() -> int:
    args = parse_args()
    all_data = pd.read_csv(args.input)
    required = {
        "timestamp_us",
        "ads_voltage",
        "envelope_voltage",
        "adaptive_on_voltage",
        "adaptive_off_voltage",
        "fixed_on_voltage",
        "fixed_off_voltage",
        "fixed_event",
        "adaptive_event",
        "phase",
        "repetition",
        "intensity",
    }
    missing = sorted(required - set(all_data.columns))
    if missing:
        raise SystemExit(f"Missing required CSV columns: {', '.join(missing)}")
    if all_data.empty:
        raise SystemExit("The CSV contains no samples.")

    if "record_type" in all_data.columns:
        data = all_data[all_data["record_type"] == "emg"].copy()
        imu_data = all_data[all_data["record_type"] == "imu"].copy()
    else:
        data = all_data
        imu_data = all_data
    if data.empty:
        raise SystemExit("The CSV contains no EMG samples.")

    intervals = contraction_intervals(data)
    has_ground_truth = bool(intervals)
    results: dict[str, Any] = {
        "source": str(args.input),
        "samples": int(len(data)),
        "imu_samples": int(len(imu_data)),
        "duration_s": float(
            (data["timestamp_us"].max() - data["timestamp_us"].min()) / 1_000_000.0
        ),
        "ground_truth_available": has_ground_truth,
        "envelope": describe_envelope(data),
    }
    if has_ground_truth:
        results["fixed_detector"] = detector_metrics(data, "fixed_event", intervals)
        results["adaptive_detector"] = detector_metrics(data, "adaptive_event", intervals)
    else:
        results["metrics_note"] = (
            "No experimental phase labels are present; TP/FP/FN and detector performance "
            "were not calculated."
        )

    figures = create_figures(data, imu_data, args.output, has_ground_truth)
    results["figures"] = figures
    args.output.mkdir(parents=True, exist_ok=True)
    metrics_path = args.output / "metrics.json"
    metrics_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results, indent=2))
    print(f"Metrics: {metrics_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
