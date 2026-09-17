# Real pilot recording 20260917_001221_25ad4ea0

This directory is a reviewed, anonymous export of a real hardware recording
captured with the EMCS Research Platform on COM13. It is published so that the
signal, detector events, sampling integrity and generated figures can be
inspected and reproduced.

## Scientific status

This is an **interrupted pilot recording**, not a recognition-validation
dataset. It was captured with desktop application 1.0.0 before the experiment
phase/finalization bugs fixed in 1.0.1. The authoritative metadata therefore
retains the outcome `in_progress`.

The stream contains 145 detector starts, 144 detector releases and a valid
calibration event, but only one hardware phase marker
(`calibration_rest`). There are no device-timestamped `contract` markers.
Consequently TP, FP, FN, precision, recall, F1, false positives per negative
minute and cue-to-detection latency cannot be calculated. Figures 04–07 state
that these metrics are unavailable; they do not contain fabricated values.

## Recorded data

| Stream | Samples | Span | Measured rate | Index gaps |
|---|---:|---:|---:|---:|
| EMG / ADS1115 | 397,568 | 471.215 s | 843.706 Hz | 7,605 |
| IMU / MPU6050 | 46,310 | 471.190 s | 98.281 Hz | 810 |

The anonymous participant code is `P001`. No participant name, contact
information or consent document is stored in this repository.

## Files

| Path | Description |
|---|---|
| `samples.csv.gz` | Complete, losslessly compressed 111,273,754-byte `samples.csv` |
| `samples-preview.csv` | Header plus every 100th data row for direct GitHub inspection |
| `events.csv` | Complete detector events and device phase markers |
| `metadata.json` | Original acquisition configuration and outcome |
| `metrics.json` | Descriptive sampling/envelope results and limitations |
| `report.html` | Self-contained local report referencing the PNG figures |
| `figures/*.png` | 300-DPI raster figures |
| `figures/*.svg` | Matching editable vector figures |
| `SHA256SUMS.txt` | Integrity hashes for every published data artifact |

Project photographs, GUI captures and the combined results sheet are in
[`docs/images/`](../../../docs/images/).

## Reproduce the analysis

Decompress the full CSV:

```powershell
python -c "import gzip,shutil; shutil.copyfileobj(gzip.open('samples.csv.gz','rb'),open('samples.csv','wb'))"
```

From the repository root, run:

```powershell
python analysis/analyze_session.py data/examples/20260917_001221_25ad4ea0
```

The analysis uses all samples for metrics. Plot downsampling is
peak-preserving and affects only rendered figures.
