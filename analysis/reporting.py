"""Publication-oriented figures and a self-contained HTML session report."""

from __future__ import annotations

from html import escape
import json
from pathlib import Path
from typing import Any, Iterable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from analysis.metrics import PhaseInterval


PHASE_COLORS = {
    "calibration_rest": "#d9d9d9",
    "prepare": "#cfe8ff",
    "contract": "#ffd6d6",
    "rest": "#d8f3dc",
    "paused": "#ffe8a1",
}


def _numeric(data: pd.DataFrame, column: str) -> np.ndarray:
    if column not in data:
        return np.array([], dtype=float)
    return pd.to_numeric(data[column], errors="coerce").to_numpy(dtype=float)


def _seconds(data: pd.DataFrame, origin_us: int) -> np.ndarray:
    return (_numeric(data, "timestamp_us") - origin_us) / 1_000_000.0


def _display_subset(
    data: pd.DataFrame,
    value_columns: tuple[str, ...],
    max_points: int = 20_000,
) -> pd.DataFrame:
    """Peak-preserving plot subset; calculations continue to use full data."""
    if len(data) <= max_points or not value_columns:
        return data
    bucket_count = max(1, max_points // (2 * len(value_columns)))
    boundaries = np.linspace(0, len(data), bucket_count + 1, dtype=int)
    selected: set[int] = {0, len(data) - 1}
    arrays = [_numeric(data, column) for column in value_columns]
    for start, end in zip(boundaries[:-1], boundaries[1:], strict=True):
        if end <= start:
            continue
        for values in arrays:
            segment = values[start:end]
            finite = np.flatnonzero(np.isfinite(segment))
            if finite.size:
                finite_values = segment[finite]
                selected.add(start + int(finite[np.argmin(finite_values)]))
                selected.add(start + int(finite[np.argmax(finite_values)]))
    return data.iloc[sorted(selected)]


def _save_pair(figure: plt.Figure, directory: Path, stem: str) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    png = directory / f"{stem}.png"
    svg = directory / f"{stem}.svg"
    figure.savefig(png, dpi=300, bbox_inches="tight")
    figure.savefig(svg, bbox_inches="tight")
    plt.close(figure)
    return [png, svg]


def _shade_phases(axis: plt.Axes, intervals: Iterable[PhaseInterval], origin_us: int) -> None:
    seen: set[str] = set()
    for interval in intervals:
        color = PHASE_COLORS.get(interval.phase)
        if not color:
            continue
        label = interval.phase if interval.phase not in seen else None
        axis.axvspan(
            (interval.start_us - origin_us) / 1_000_000.0,
            (interval.end_us - origin_us) / 1_000_000.0,
            color=color,
            alpha=0.22,
            linewidth=0,
            label=label,
        )
        seen.add(interval.phase)


def _event_times(events: pd.DataFrame, detector: str, origin_us: int) -> np.ndarray:
    if events.empty or not {"event_type", "detector", "device_timestamp_us"}.issubset(events):
        return np.array([], dtype=float)
    rows = events[
        events["event_type"].eq("CONTRACTION_START") & events["detector"].eq(detector)
    ]
    return (_numeric(rows, "device_timestamp_us") - origin_us) / 1_000_000.0


def _signal_figures(
    emg: pd.DataFrame,
    events: pd.DataFrame,
    intervals: list[PhaseInterval],
    directory: Path,
    origin_us: int,
) -> list[Path]:
    paths: list[Path] = []
    overview = _display_subset(
        emg, ("ads_voltage", "ac_voltage", "envelope_voltage"), max_points=20_000
    )
    time_s = _seconds(overview, origin_us)
    figure, axes = plt.subplots(3, 1, figsize=(13, 8), sharex=True)
    axes[0].plot(time_s, _numeric(overview, "ads_voltage"), lw=0.55, color="#315a9b")
    axes[0].set_ylabel("ADS1115, V")
    axes[0].set_title("AD8232 signal: absolute input, AC component and firmware envelope")
    axes[1].plot(time_s, _numeric(overview, "ac_voltage"), lw=0.55, color="#147d64")
    axes[1].set_ylabel("Centered AC, V")
    axes[2].plot(time_s, _numeric(overview, "envelope_voltage"), lw=0.7, label="Envelope")
    for column, label, style in (
        ("fixed_on_voltage", "Fixed ON", "--"),
        ("adaptive_on_voltage", "Adaptive ON", ":"),
    ):
        values = _numeric(overview, column)
        if values.size:
            axes[2].plot(time_s, values, style, lw=0.8, label=label)
    axes[2].set_ylabel("Envelope, V")
    axes[2].set_xlabel("Device time, s")
    for axis in axes:
        _shade_phases(axis, intervals, origin_us)
        axis.grid(alpha=0.2)
    axes[2].legend(loc="upper right", ncols=3)
    paths += _save_pair(figure, directory, "01-emg-signal-and-thresholds")

    contract = next((item for item in intervals if item.phase == "contract"), None)
    if contract is not None:
        padding_us = 500_000
        subset = emg[
            emg["timestamp_us"].between(contract.start_us - padding_us, contract.end_us + padding_us)
        ]
        figure, axis = plt.subplots(figsize=(11, 4.5))
        local_time = (_numeric(subset, "timestamp_us") - contract.start_us) / 1_000_000.0
        axis.plot(local_time, _numeric(subset, "envelope_voltage"), label="Envelope", lw=1.0)
        axis.plot(local_time, _numeric(subset, "fixed_on_voltage"), "--", label="Fixed ON")
        axis.plot(local_time, _numeric(subset, "adaptive_on_voltage"), ":", label="Adaptive ON")
        axis.axvspan(0, contract.duration_s, color=PHASE_COLORS["contract"], alpha=0.25)
        axis.set(title=f"Example prescribed contraction: trial {contract.trial}, {contract.prescribed_intensity}",
                 xlabel="Time from cue, s", ylabel="Envelope, V")
        axis.grid(alpha=0.2)
        axis.legend()
        paths += _save_pair(figure, directory, "02-example-prescribed-contraction")

    figure, axis = plt.subplots(figsize=(13, 4.8))
    axis.plot(time_s, _numeric(overview, "envelope_voltage"), color="#555555", lw=0.55, label="Envelope")
    _shade_phases(axis, intervals, origin_us)
    for detector, color, marker in (("fixed", "#d62728", "^"), ("adaptive", "#2ca02c", "v")):
        for index, event_time in enumerate(_event_times(events, detector, origin_us)):
            axis.axvline(event_time, color=color, lw=0.8, alpha=0.75,
                         label=f"{detector} start" if index == 0 else None)
            axis.scatter([event_time], [axis.get_ylim()[1]], marker=marker, color=color, s=18)
    axis.set(title="Fixed and adaptive detector events against hardware phase markers",
             xlabel="Device time, s", ylabel="Envelope, V")
    axis.grid(alpha=0.2)
    axis.legend(loc="upper right", ncols=4)
    paths += _save_pair(figure, directory, "03-fixed-adaptive-events")
    return paths


def _metric_figures(results: dict[str, Any], directory: Path) -> list[Path]:
    if not all(name in results for name in ("fixed_detector", "adaptive_detector")):
        return []
    paths: list[Path] = []
    names = ["fixed_detector", "adaptive_detector"]
    labels = ["Fixed", "Adaptive"]

    figure, axes = plt.subplots(1, 2, figsize=(9, 4))
    for axis, name, label in zip(axes, names, labels, strict=True):
        metric = results[name]
        matrix = np.array([[metric["tp"], metric["fn"]], [metric["fp"], np.nan]])
        axis.imshow(np.nan_to_num(matrix), cmap="Blues")
        for (row, column), value in np.ndenumerate(matrix):
            axis.text(column, row, "N/A" if np.isnan(value) else str(int(value)),
                      ha="center", va="center")
        axis.set(xticks=[0, 1], xticklabels=["Detected", "Missed"],
                 yticks=[0, 1], yticklabels=["Contract cue", "Negative phase"], title=label)
    figure.suptitle("Event outcome matrix (true-negative count is undefined)")
    paths += _save_pair(figure, directory, "04-event-outcome-matrix")

    x = np.arange(3)
    width = 0.34
    figure, axis = plt.subplots(figsize=(8, 4.5))
    for offset, name, label in ((-width / 2, names[0], labels[0]), (width / 2, names[1], labels[1])):
        axis.bar(x + offset, [results[name][key] for key in ("precision", "recall", "f1")], width, label=label)
    axis.set(xticks=x, xticklabels=["Precision", "Recall", "F1"], ylim=(0, 1.05), ylabel="Score",
             title="Detector recognition metrics")
    axis.legend()
    axis.grid(axis="y", alpha=0.2)
    paths += _save_pair(figure, directory, "05-precision-recall-f1")

    figure, axis = plt.subplots(figsize=(7, 4.5))
    values = [results[name].get("false_positives_per_minute") for name in names]
    axis.bar(labels, [0 if value is None else value for value in values], color=["#d62728", "#2ca02c"])
    axis.set(title="False starts normalized by negative-phase exposure", ylabel="False positives / min")
    axis.grid(axis="y", alpha=0.2)
    paths += _save_pair(figure, directory, "06-false-positives-per-minute")

    figure, axis = plt.subplots(figsize=(8, 4.5))
    latency_data = [results[name].get("cue_to_detection_latencies_ms", []) for name in names]
    if any(latency_data):
        axis.boxplot(latency_data, tick_labels=labels, showmeans=True)
    else:
        axis.text(0.5, 0.5, "No detected contraction starts", ha="center", va="center", transform=axis.transAxes)
        axis.set_xticks([])
    axis.set(title="Cue-to-detection latency", ylabel="Latency, ms")
    axis.grid(axis="y", alpha=0.2)
    paths += _save_pair(figure, directory, "07-cue-to-detection-latency")
    return paths


def _physiology_figures(
    emg: pd.DataFrame,
    imu: pd.DataFrame,
    intervals: list[PhaseInterval],
    directory: Path,
    origin_us: int,
) -> list[Path]:
    paths: list[Path] = []
    figure, axes = plt.subplots(1, 2, figsize=(12, 4.8))
    distribution_data = _display_subset(emg, ("envelope_voltage",), max_points=20_000)
    phase_order = ["calibration_rest", "prepare", "contract", "rest"]
    phase_values = [
        _numeric(distribution_data[distribution_data["phase"].eq(phase)], "envelope_voltage")
        for phase in phase_order
    ]
    valid_phases = [(name, values[np.isfinite(values)]) for name, values in zip(phase_order, phase_values, strict=True)]
    valid_phases = [(name, values) for name, values in valid_phases if values.size]
    if valid_phases:
        axes[0].boxplot([item[1] for item in valid_phases], tick_labels=[item[0] for item in valid_phases])
        axes[0].tick_params(axis="x", rotation=25)
    intensity_order = ["weak", "medium", "strong"]
    intensity_values = [
        _numeric(
            distribution_data[distribution_data["prescribed_intensity"].eq(level)],
            "envelope_voltage",
        )
        for level in intensity_order
    ]
    valid_intensities = [
        (name, values[np.isfinite(values)])
        for name, values in zip(intensity_order, intensity_values, strict=True)
        if np.isfinite(values).any()
    ]
    if valid_intensities:
        axes[1].boxplot([item[1] for item in valid_intensities], tick_labels=[item[0] for item in valid_intensities])
    else:
        axes[1].text(0.5, 0.5, "No prescribed contraction samples", ha="center", va="center",
                     transform=axes[1].transAxes)
        axes[1].set_xticks([])
    axes[0].set(title="Envelope by protocol phase", ylabel="Envelope, V")
    axes[1].set(title="Envelope by prescribed intensity", ylabel="Envelope, V")
    for axis in axes:
        axis.grid(axis="y", alpha=0.2)
    paths += _save_pair(figure, directory, "08-envelope-distributions")

    figure, axes = plt.subplots(2, 1, figsize=(13, 7), sharex=True)
    imu_overview = _display_subset(
        imu, ("ax", "ay", "az", "gx", "gy", "gz"), max_points=18_000
    )
    imu_time = _seconds(imu_overview, origin_us)
    for column in ("ax", "ay", "az"):
        axes[0].plot(imu_time, _numeric(imu_overview, column), lw=0.7, label=column)
    for column in ("gx", "gy", "gz"):
        axes[1].plot(imu_time, _numeric(imu_overview, column), lw=0.7, label=column)
    axes[0].set(title="MPU6050 motion during protocol", ylabel="Acceleration, g")
    axes[1].set(xlabel="Device time, s", ylabel="Angular rate, deg/s")
    for axis in axes:
        _shade_phases(axis, intervals, origin_us)
        axis.legend(ncols=3)
        axis.grid(alpha=0.2)
    paths += _save_pair(figure, directory, "09-imu-motion")

    figure, axes = plt.subplots(2, 1, figsize=(12, 7))
    for axis, data, label, expected_ms in (
        (axes[0], emg, "EMG", 1000.0 / 860.0),
        (axes[1], imu, "IMU", 10.0),
    ):
        timestamps = _numeric(data, "timestamp_us")
        timestamps = timestamps[np.isfinite(timestamps)]
        intervals_ms = np.diff(timestamps) / 1000.0
        if intervals_ms.size:
            display = intervals_ms[::max(1, intervals_ms.size // 5000)]
            axis.plot(display, lw=0.55, label="Observed interval")
            axis.axhline(expected_ms, color="#d62728", ls="--", lw=0.9, label="Nominal interval")
        axis.set(title=f"{label} sampling intervals and visible timing gaps", ylabel="Interval, ms")
        axis.grid(alpha=0.2)
        axis.legend()
    axes[1].set_xlabel("Successive sample number (display-downsampled)")
    paths += _save_pair(figure, directory, "10-sampling-intervals-and-gaps")
    return paths


def create_all_figures(
    emg: pd.DataFrame,
    imu: pd.DataFrame,
    events: pd.DataFrame,
    intervals: list[PhaseInterval],
    results: dict[str, Any],
    directory: Path,
) -> list[Path]:
    """Create 300-DPI PNG and matching SVG figures without fabricating missing metrics."""
    origin_us = int(pd.to_numeric(emg["timestamp_us"], errors="coerce").dropna().min())
    paths = _signal_figures(emg, events, intervals, directory, origin_us)
    paths += _metric_figures(results, directory)
    paths += _physiology_figures(emg, imu, intervals, directory, origin_us)
    return paths


def _format_value(value: Any) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value:.5g}"
    return str(value)


def write_html_report(
    path: Path,
    results: dict[str, Any],
    metadata: dict[str, Any],
    figure_paths: list[Path],
) -> None:
    """Write a local report containing metadata, metrics, limitations and PNG figures."""
    path.parent.mkdir(parents=True, exist_ok=True)
    detector_rows = []
    for key, label in (("fixed_detector", "Fixed"), ("adaptive_detector", "Adaptive")):
        if key not in results:
            continue
        metric = results[key]
        ci = metric.get("bootstrap_95_ci", {})
        detector_rows.append(
            "<tr>"
            f"<td>{label}</td><td>{metric['tp']}</td><td>{metric['fp']}</td><td>{metric['fn']}</td>"
            f"<td>{_format_value(metric['precision'])}</td><td>{_format_value(metric['recall'])}</td>"
            f"<td>{_format_value(metric['f1'])}</td>"
            f"<td>{_format_value(metric.get('false_positives_per_minute'))}</td>"
            f"<td>{_format_value(metric.get('cue_to_detection_latency_ms_mean'))}</td>"
            f"<td><code>{escape(json.dumps(ci, ensure_ascii=False))}</code></td></tr>"
        )
    images = []
    for figure in figure_paths:
        if figure.suffix.lower() != ".png":
            continue
        try:
            source = figure.relative_to(path.parent).as_posix()
        except ValueError:
            source = figure.resolve().as_uri()
        images.append(f'<figure><img src="{escape(source)}"><figcaption>{escape(figure.stem)}</figcaption></figure>')
    sampling_rows = []
    for stream, metric in results.get("sampling", {}).items():
        sampling_rows.append(
            f"<tr><td>{escape(stream)}</td><td>{metric.get('samples', 0)}</td>"
            f"<td>{_format_value(metric.get('rate_hz'))}</td>"
            f"<td>{metric.get('index_gaps', 0)}</td><td>{metric.get('timer_gaps', 0)}</td></tr>"
        )
    raw_metadata = escape(json.dumps(metadata, indent=2, ensure_ascii=False))
    note = escape(results.get("metrics_note", "Metrics use device-timestamped hardware phase markers."))
    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>EMSU scientific session report</title><style>
body{{font:15px system-ui,sans-serif;max-width:1200px;margin:auto;padding:24px;color:#18212b}}
h1,h2{{color:#173f5f}} table{{border-collapse:collapse;width:100%;margin:12px 0 24px}}
th,td{{border:1px solid #b9c3cc;padding:7px;text-align:right}} th:first-child,td:first-child{{text-align:left}}
figure{{margin:24px 0}} img{{max-width:100%;height:auto}} code,pre{{background:#f2f4f5;padding:3px}}
.notice{{padding:12px;border-left:5px solid #e09f3e;background:#fff8e6}}
</style></head><body>
<h1>EMSU scientific session report</h1>
<p>Session: <code>{escape(str(results.get('session_id') or 'unknown'))}</code>; outcome:
<strong>{escape(str(results.get('session_outcome', 'unknown')))}</strong>.</p>
<p class="notice">{note} Prescribed weak/medium/strong labels are protocol instructions, not measured force.</p>
<h2>Sampling integrity</h2><table><thead><tr><th>Stream</th><th>Samples</th><th>Rate, Hz</th>
<th>Index gaps</th><th>Timer gaps</th></tr></thead><tbody>{''.join(sampling_rows)}</tbody></table>
<h2>Detector metrics</h2><table><thead><tr><th>Detector</th><th>TP</th><th>FP</th><th>FN</th>
<th>Precision</th><th>Recall</th><th>F1</th><th>FP/min</th><th>Cue-to-detection, ms</th><th>Trial bootstrap 95% CI</th>
</tr></thead><tbody>{''.join(detector_rows) or '<tr><td colspan="10">No valid contract ground truth.</td></tr>'}</tbody></table>
<h2>Figures</h2>{''.join(images)}
<h2>Session metadata</h2><pre>{raw_metadata}</pre>
<h2>Limitations</h2><p>ADS1115 voltage is not clinical ECG amplitude. MPU6050 yaw is relative and drifts without a magnetometer.
Event metrics count one first detector start per hardware-marked contraction interval. This software is a research prototype,
not a medical device.</p></body></html>"""
    path.write_text(html, encoding="utf-8")
