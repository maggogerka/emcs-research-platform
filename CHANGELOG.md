# Changelog

## 1.0.1 — 2026-09-17

- Kept every Signals chart readable in a scrollable layout and stopped rendering hidden plots.
- Bounded live phase/event graphics and replaced overlapping event text with compact markers.
- Buffered lossless CSV recording and drained background-analysis output without blocking the UI.
- Added peak-preserving display downsampling for faster PNG/SVG report generation.
- Fixed the phase payload key and terminal index boundary that prevented calibrated experiments from advancing and finalizing.
- Added guided six-face MPU6050 accel/gyro calibration with a final level reference.
- Smoothed Windows pointer motion by distributing batched IMU data on a precise timer.

## 1.0.0 — 2026-09-16

- Added device-timestamped experiment phase markers and separate event storage.
- Added balanced seeded protocols with explicit terminal outcomes and calibration gating.
- Rebuilt the desktop application as seven focused tabs with persistent safety status.
- Added lossless session recording, linked scientific plots, 3D relative orientation and opt-in Windows control.
- Added marker-synchronized metrics, trial-bootstrap 95% confidence intervals, HTML reports and PNG/SVG figures.
- Added automated tests, GitHub Actions firmware/application builds and PyInstaller packaging.
