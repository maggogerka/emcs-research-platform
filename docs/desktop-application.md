# Desktop application guide

## Start and connect

1. Install dependencies with `install_ui.bat` or `python -m pip install -r requirements.txt`.
2. Connect the ESP32-S3 and start `run_ui.bat` or `python -m desktop_app`.
3. On **Dashboard**, select COM13 and press **Connect**.
4. Confirm the persistent strip shows ESP, ADS1115 and MPU6050 healthy, both
   electrode lead-off inputs clear, calibration ready, and zero CRC/gap counts.

## Research experiment

1. Open **Experiment**, enter an anonymous code (never a name), and configure
   repetitions, phase durations and seed. Hover any parameter for units, range,
   default and effect.
2. Attach electrodes using the laboratory's isolated battery-powered setup.
3. Press **Start calibration and experiment** and remain still. The trial timer
   will not begin until the hardware reports `EMG_CALIBRATION_DONE`.
4. Follow the Russian phase card and beep. Weak/medium/strong are prescribed
   effort instructions, not measured force.
5. Pause/resume if necessary or abort explicitly. Lead-off and hardware errors
   terminate with their own outcomes instead of reporting completion.
6. The application runs analysis after finalization and opens **Results**.
   Use its buttons to open the session directory and `report.html`.

Every session directory contains `samples.csv`, `events.csv`, `metadata.json`,
`metrics.json`, `report.html` and `figures/`. Do not commit participant-derived
session directories.

## Signals and orientation

Mouse-wheel zoom and drag pan are native to every graph. Choose a time window,
pause only the display, toggle curves, inspect the crosshair, export PNG/SVG/CSV,
or double-click/press **Expand / restore**. Scroll vertically to inspect the
five readable compact graphs. All graphs share time. F↑ and A↑ denote fixed
and adaptive contraction starts; release events use dashed lines and full event
names are available as tooltips. Recorded data remain full-rate even when the
display is downsampled or paused.

The 3D view is a single 30 FPS cuboid. Select mounting orientation, reset the
relative attitude or disable rendering. Roll and pitch use accel/gyro fusion;
yaw is relative and drifts because MPU6050 has no magnetometer. For calibrated
roll/pitch, press **Начать / повторить калибровку**, follow all stationary
level/side/nose/upside-down prompts, and press **Захватить положение** at each
step. Changing Mounting invalidates the desktop calibration intentionally.

## Computer Control safety

Computer Control is OFF at startup and cannot coexist with a research session.
Review axes, inversions, sensitivity, dead zone, smoothing, maximum speed,
detector and one-shot action, then use the three-second countdown. Press global
F12 at any time to stop. Control also stops automatically on disconnect,
lead-off, missing calibration, sensor error or data older than 300 ms. Named
profiles are stored locally with QSettings. Tests exercise mapping only and
never inject real clicks.

## Windows package

Build the standalone executable after installing developer requirements:

    python -m pip install -r requirements-dev.txt
    python -m PyInstaller --clean --noconfirm emcs.spec

The result is `dist/EMCSResearchPlatform.exe`.
