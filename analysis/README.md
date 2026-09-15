# Analysis

The capture_session.py tool records the real binary stream without requiring the GUI:

    python analysis/capture_session.py --port COM13 --duration 60

The analyze_session.py tool processes a CSV produced by the GUI or capture tool and
writes 300 dpi PNG figures and metrics.json:

    python analysis/analyze_session.py data/recordings/session.csv

TP, FP, FN, precision, recall, F1 and recognition latency are calculated only
when the CSV contains experiment phase labels. Unlabelled captures produce
descriptive plots but no synthetic performance result.
