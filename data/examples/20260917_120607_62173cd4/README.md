# Reference experiment 20260917_120607_62173cd4

This directory is a reviewed, anonymous export of a completed real-hardware
experiment captured with EMCS Research Platform 1.0.1 on COM13. It is the
repository reference dataset for reproducing the signal, detector and sampling
figures shown in the main README.

## Experimental status

- Outcome: `completed`
- Anonymous participant code: `P001`
- Protocol: 30 trials; 3 s prepare, 2 s contract cue and 3 s rest
- Ground truth: device-timestamped `PHASE_MARKER` events
- Prescribed intensities: balanced weak/medium/strong instructions
- Transport: 0 CRC errors, 0 discarded bytes and 0 sequence gaps
- Hardware at completion: ADS1115 OK, MPU6050 OK, both lead-off inputs low

The intensity labels describe the requested action and are not measurements of
force. This is a single-participant prototype experiment, not clinical evidence
or a population-level performance claim.

## Sampling integrity

| Stream | Samples | Span | Measured rate | Index gaps | Timer gaps |
|---|---:|---:|---:|---:|---:|
| EMG / ADS1115 | 217,408 | 253.208 s | 858.609 Hz | 313 | 310 |
| IMU / MPU6050 | 25,320 | 253.190 s | 100.000 Hz | 0 | 0 |

## Detector results

One true positive is the first detector-start event inside a hardware-marked
contract interval. Additional starts and starts in negative protocol phases are
false positives; a contract interval without a start is a false negative.

| Detector | TP | FP | FN | Precision | Recall | F1 | FP/min | Mean cue latency |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Fixed | 27 | 11 | 3 | 0.711 | 0.900 | 0.794 | 3.439 | 597.8 ms |
| Adaptive | 29 | 11 | 1 | 0.725 | 0.967 | 0.829 | 3.439 | 583.3 ms |

Trial-bootstrap 95% confidence intervals and per-trial values are preserved in
`metrics.json` and `report.html`.

## Files

| Path | Description |
|---|---|
| `samples.csv.gz` | Complete losslessly compressed 61,642,264-byte sample table |
| `samples-preview.csv` | Header plus every 100th observation for direct GitHub inspection |
| `events.csv` | Complete detector events and 91 device phase markers |
| `metadata.json` | Acquisition, protocol, hardware and terminal outcome metadata |
| `metrics.json` | Sampling, envelope and detector results with per-trial values |
| `report.html` | Portable report linking all PNG figures |
| `figures/01-*.{png,svg}` … `figures/10-*.{png,svg}` | Ten complete raster/vector figure pairs |
| `SHA256SUMS.txt` | Integrity hashes for every published artifact |

Project photographs, diagrams and GUI captures are in
[`docs/images/`](../../../docs/images/).

## Reproduce the analysis

Decompress the complete sample table from this directory:

```powershell
python -c "import gzip,shutil; shutil.copyfileobj(gzip.open('samples.csv.gz','rb'),open('samples.csv','wb'))"
```

From the repository root, run:

```powershell
python analysis/analyze_session.py data/examples/20260917_120607_62173cd4
```

The analysis uses all recorded samples. Peak-preserving plot downsampling only
reduces rendering cost and never changes saved samples or metric calculations.
