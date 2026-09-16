"""Scientific marker synchronization and detector metrics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd


NEGATIVE_PHASES = {"calibration_rest", "prepare", "rest"}


@dataclass(frozen=True, slots=True)
class PhaseInterval:
    start_us: int
    end_us: int
    phase: str
    trial: int
    prescribed_intensity: str

    @property
    def duration_s(self) -> float:
        return max(0, self.end_us - self.start_us) / 1_000_000.0


def marker_rows(events: pd.DataFrame) -> pd.DataFrame:
    if events.empty or "event_type" not in events:
        return pd.DataFrame()
    markers = events[events["event_type"].eq("PHASE_MARKER")].copy()
    if markers.empty:
        return markers
    markers["device_timestamp_us"] = pd.to_numeric(
        markers["device_timestamp_us"], errors="coerce"
    )
    return markers.dropna(subset=["device_timestamp_us"]).sort_values(
        ["device_timestamp_us", "marker_id"], na_position="last"
    )


def label_by_markers(data: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    """Label arbitrary device timestamps from the latest preceding hardware marker."""
    result = data.copy()
    markers = marker_rows(events)
    for column in ("phase", "prescribed_intensity"):
        if column not in result:
            result[column] = pd.Series("", index=result.index, dtype=object)
        else:
            result[column] = result[column].fillna("").astype(str)
    if "trial" not in result:
        result["trial"] = 0
    if result.empty or markers.empty:
        return result
    timestamps = pd.to_numeric(result["timestamp_us"], errors="coerce").to_numpy()
    marker_timestamps = markers["device_timestamp_us"].astype(np.int64).to_numpy()
    indices = np.searchsorted(marker_timestamps, timestamps, side="right") - 1
    valid = indices >= 0
    if valid.any():
        result.loc[valid, "phase"] = markers["phase"].fillna("").to_numpy()[indices[valid]]
        result.loc[valid, "trial"] = (
            pd.to_numeric(markers["trial"], errors="coerce").fillna(0).astype(int).to_numpy()[indices[valid]]
        )
        result.loc[valid, "prescribed_intensity"] = (
            markers["prescribed_intensity"].fillna("").to_numpy()[indices[valid]]
        )
    return result


def phase_intervals(events: pd.DataFrame, end_timestamp_us: int) -> list[PhaseInterval]:
    markers = marker_rows(events)
    intervals: list[PhaseInterval] = []
    terminal = {"completed", "aborted_by_user", "lead_off", "hardware_error"}
    for position, (_, marker) in enumerate(markers.iterrows()):
        phase = str(marker.get("phase", ""))
        if not phase or phase in terminal:
            continue
        start = int(marker["device_timestamp_us"])
        end = (
            int(markers.iloc[position + 1]["device_timestamp_us"])
            if position + 1 < len(markers)
            else int(end_timestamp_us)
        )
        if end > start:
            intervals.append(
                PhaseInterval(
                    start,
                    end,
                    phase,
                    int(float(marker.get("trial", 0) or 0)),
                    str(marker.get("prescribed_intensity", "") or ""),
                )
            )
    return intervals


def _safe_ratio(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def _summary(tp: int, fp: int, fn: int, negative_s: float, latencies: list[float]) -> dict[str, Any]:
    precision = _safe_ratio(tp, tp + fp)
    recall = _safe_ratio(tp, tp + fn)
    f1 = _safe_ratio(2 * precision * recall, precision + recall)
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "negative_phase_duration_s": negative_s,
        "false_positives_per_minute": fp / (negative_s / 60.0) if negative_s > 0 else None,
        "cue_to_detection_latency_ms_mean": float(np.mean(latencies)) if latencies else None,
        "cue_to_detection_latency_ms_median": float(np.median(latencies)) if latencies else None,
        "cue_to_detection_latencies_ms": latencies,
    }


def bootstrap_confidence_intervals(
    per_trial: list[dict[str, Any]],
    *,
    seed: int,
    iterations: int = 2000,
) -> dict[str, list[float] | None]:
    """Trial-level percentile bootstrap; no CI is invented without trials."""
    if not per_trial:
        return {name: None for name in ("precision", "recall", "f1", "cue_to_detection_latency_ms_mean")}
    rng = np.random.default_rng(seed)
    values: dict[str, list[float]] = {
        "precision": [], "recall": [], "f1": [], "cue_to_detection_latency_ms_mean": []
    }
    count = len(per_trial)
    for _ in range(iterations):
        selection = [per_trial[index] for index in rng.integers(0, count, count)]
        tp = sum(int(item["tp"]) for item in selection)
        fp = sum(int(item["fp"]) for item in selection)
        fn = sum(int(item["fn"]) for item in selection)
        precision = _safe_ratio(tp, tp + fp)
        recall = _safe_ratio(tp, tp + fn)
        values["precision"].append(precision)
        values["recall"].append(recall)
        values["f1"].append(_safe_ratio(2 * precision * recall, precision + recall))
        latencies = [item["cue_to_detection_latency_ms"] for item in selection if item["cue_to_detection_latency_ms"] is not None]
        if latencies:
            values["cue_to_detection_latency_ms_mean"].append(float(np.mean(latencies)))
    result: dict[str, list[float] | None] = {}
    for name, samples in values.items():
        result[name] = (
            [float(value) for value in np.percentile(samples, [2.5, 97.5])]
            if samples else None
        )
    return result


def detector_metrics(
    events: pd.DataFrame,
    intervals: list[PhaseInterval],
    detector: str,
    *,
    seed: int = 0,
    bootstrap_iterations: int = 2000,
) -> dict[str, Any]:
    starts = events[
        events.get("event_type", pd.Series(dtype=str)).eq("CONTRACTION_START")
        & events.get("detector", pd.Series(dtype=str)).eq(detector)
    ].copy()
    starts["device_timestamp_us"] = pd.to_numeric(
        starts.get("device_timestamp_us"), errors="coerce"
    )
    predictions = sorted(starts["device_timestamp_us"].dropna().astype(int).tolist())
    contracts = [interval for interval in intervals if interval.phase == "contract"]
    negative_s = sum(
        interval.duration_s for interval in intervals if interval.phase in NEGATIVE_PHASES
    )
    matched: set[int] = set()
    per_trial: list[dict[str, Any]] = []
    latencies: list[float] = []
    tp = 0
    for interval in contracts:
        candidates = [
            (index, timestamp)
            for index, timestamp in enumerate(predictions)
            if index not in matched and interval.start_us <= timestamp < interval.end_us
        ]
        latency: float | None = None
        if candidates:
            index, timestamp = candidates[0]
            matched.add(index)
            latency = (timestamp - interval.start_us) / 1000.0
            latencies.append(latency)
            tp += 1
        trial_predictions = [
            index for index, timestamp in enumerate(predictions)
            if any(
                phase.trial == interval.trial
                and phase.start_us <= timestamp < phase.end_us
                for phase in intervals
            )
        ]
        trial_fp = sum(index not in matched for index in trial_predictions)
        per_trial.append(
            {
                "trial": interval.trial,
                "prescribed_intensity": interval.prescribed_intensity,
                "tp": int(latency is not None),
                "fp": trial_fp,
                "fn": int(latency is None),
                "detected": latency is not None,
                "cue_to_detection_latency_ms": latency,
            }
        )
    fn = len(contracts) - tp
    fp = len(predictions) - len(matched)
    result = _summary(tp, fp, fn, negative_s, latencies)
    result["per_trial"] = per_trial
    result["bootstrap_95_ci"] = bootstrap_confidence_intervals(
        per_trial, seed=seed, iterations=bootstrap_iterations
    )
    return result


def legacy_events_and_markers(samples: pd.DataFrame) -> pd.DataFrame:
    """Translate v1 mixed CSV annotations into v2 event rows for compatibility."""
    rows: list[dict[str, Any]] = []
    previous: tuple[str, int, str] | None = None
    marker_id = 0
    for _, row in samples.sort_values("timestamp_us").iterrows():
        phase_value = row.get("phase", "")
        intensity_value = row.get("intensity", "")
        repetition_value = row.get("repetition", 0)
        phase = "" if pd.isna(phase_value) else str(phase_value).strip()
        intensity = "" if pd.isna(intensity_value) else str(intensity_value).strip()
        trial = 0 if pd.isna(repetition_value) else int(float(repetition_value or 0))
        label = (
            phase,
            trial,
            intensity,
        )
        if label[0] and label != previous:
            marker_id += 1
            rows.append(
                {
                    "device_timestamp_us": int(row["timestamp_us"]),
                    "event_type": "PHASE_MARKER",
                    "detector": "system",
                    "marker_id": marker_id,
                    "phase": label[0],
                    "trial": label[1],
                    "prescribed_intensity": label[2],
                }
            )
            previous = label
        for detector, column in (("fixed", "fixed_event"), ("adaptive", "adaptive_event")):
            if str(row.get(column, "")).strip().lower() in {"1", "true"}:
                rows.append(
                    {
                        "device_timestamp_us": int(row["timestamp_us"]),
                        "event_type": "CONTRACTION_START",
                        "detector": detector,
                    }
                )
    return pd.DataFrame(rows)
