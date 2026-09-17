# Analysis

The capture tool records the real binary stream without requiring the GUI:

    python analysis/capture_session.py --port COM13 --duration 60

Analyze a v2 session directory:

    python analysis/analyze_session.py data/recordings/<session-id>

The analysis generates `metrics.json`, `report.html`, and publication-oriented
300 dpi PNG plus SVG figures. Legacy CSV is accepted for compatibility. Metrics
requiring contraction ground truth are omitted unless device-timestamped phase
markers (or explicit legacy labels) exist; hardware-only data never produce
invented recognition performance.
