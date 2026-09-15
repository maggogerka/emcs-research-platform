# EMCS Research Platform

EMCS Research Platform is an open research implementation of an
electromyographic control-system acquisition and evaluation pipeline based on
ESP32-S3. It combines a high-rate ADS1115/AD8232 signal path, MPU6050 motion
measurement, embedded fixed and adaptive contraction detectors, a Windows
desktop application and reproducible offline analysis.

The platform is intended for algorithm development and controlled laboratory
experiments. It is not a clinical instrument, does not provide diagnostic
measurements and does not currently implement a production assistive-device
actuator or USB/Bluetooth HID output.

> **Safety warning:** this is a research prototype, not a medical device.
> When electrodes are attached to a person, use an autonomous,
> appropriately isolated battery-powered setup. Do not connect a participant
> to a mains-powered experimental system.

## Hardware architecture

The reference build uses an ESP32-S3 N16R8. AD8232 OUT is sampled by ADS1115
AIN0 relative to ground. The ADS1115 and MPU6050 share the 100 kHz I2C bus.
AD8232 lead-off outputs are read independently and override the contraction
detectors.

| Device signal | ESP32-S3 or peripheral connection | Configuration |
|---|---|---|
| ADS1115 SDA | GPIO8 | I2C address 0x48 |
| ADS1115 SCL | GPIO9 | 100 kHz |
| ADS1115 AIN0 | AD8232 OUT | AIN0–GND, ±4.096 V, continuous 860 SPS |
| AD8232 LO− | GPIO4 | Digital input |
| AD8232 LO+ | GPIO5 | Digital input |
| MPU6050 SDA | GPIO8 | I2C address 0x68 |
| MPU6050 SCL | GPIO9 | 100 Hz output, ±2 g, ±250 deg/s |
| All grounds | Common GND | 3.3 V-compatible logic |

Detailed wiring notes are in docs/hardware.md.

## Figure placeholders

The repository does not use invented images. The following visible placeholders
identify material required for a future paper or release:

- **Prototype photograph:** docs/images/device-prototype.jpg
- **System architecture:** docs/images/system-architecture.png
- **Electrical schematic:** docs/images/wiring-diagram.png
- **Electrode placement:** docs/images/electrode-placement.png
- **GUI screenshot:** docs/images/gui-screenshot.png
- **Algorithm flowchart:** docs/images/algorithm-flow.png
- **Reviewed experimental graphs:** docs/images/experimental-results.png

## Firmware build and execution

ESP-IDF 6.0 or a compatible release is required.

    cd C:\dev\emsu-researching
    idf.py set-target esp32s3
    idf.py build
    idf.py -p COM13 flash
    idf.py -p COM13 monitor

At startup the firmware scans addresses 0x08–0x77, verifies 0x48 and 0x68,
checks MPU6050 WHO_AM_I, configures both devices and performs a stationary
two-second gyroscope-bias calibration. It does not reboot repeatedly after an
I2C failure. Runtime errors are reported and acquisition retries continue.

The binary stream is disabled at boot. The desktop application starts it after
connection. Protocol commands and frame layouts are documented in
docs/protocol.md.

## Desktop application

On Windows:

    install_ui.bat
    run_ui.bat

The PySide6 interface defaults to COM13. Serial I/O and CRC validation run in a
dedicated thread. The GUI displays hardware, electrodes, movement and detector
state; plots raw EMG, RMS envelope, thresholds and all IMU axes; performs
calibration commands; records CSV; and controls the experiment scenario.

Local CSV recordings are saved under data/recordings/ by default and are
excluded from Git. Typed `emg` and `imu` rows preserve every received native-rate
sample. They include detector results, lead state, settings and the current
experiment label, so the two streams can be analyzed without duplicating a
stale IMU value into every EMG row.

## Recognition algorithm

ADS1115 operates continuously at its 860 SPS setting. A microsecond timer
requests samples at a nominal 1163 us period. Because ADS1115 and MPU6050 share
a 100 kHz bus, the actual recoverable ADS rate must be measured for each build;
timer gaps and sample indices explicitly expose missed acquisition slots.
MPU6050 is configured for 100 Hz and its gyroscope bias is estimated from 200
stationary samples.

The embedded EMG path performs:

1. Slow DC baseline removal.
2. Full-wave magnitude through the squared signal.
3. A 43-sample RMS envelope, approximately 50 ms.
4. Ten seconds of quiet calibration.
5. Robust noise estimation: sigma = 1.4826 × MAD.
6. Ton = median + 6 × sigma and Toff = median + 3 × sigma by default.
7. 50 ms contraction confirmation, 100 ms release confirmation and 350 ms
   refractory interval.

The finite states are LEADS_OFF, CALIBRATING, REST, CANDIDATE, ACTIVE and
REFRACTORY. Lead-off immediately blocks detection and contraction events. The
fixed detector retains calibration thresholds. The adaptive detector uses a
rolling rest-only baseline and slowly approaches new robust thresholds.
Adaptation is frozen during contractions, lead-off and strong movement.

## Experiment protocol

The built-in protocol starts with ten seconds of quiet baseline and continues
with 30 contractions. Every trial has preparation, contraction and rest phases;
weak, medium and strong labels cycle across trials. See docs/experiment.md.

Successful algorithm validation requires connected electrodes, both lead-off
inputs low and a labelled human-subject recording collected under an approved
protocol. A hardware-only or lead-off capture must not be reported as
recognition validation.

## Reproducible analysis

Capture without the GUI:

    python analysis/capture_session.py --port COM13 --duration 60

Analyze a recorded CSV:

    python analysis/analyze_session.py data/recordings/session.csv

The analysis creates 300 dpi PNG files and metrics.json in figures/generated/.
For labelled experiments it reports TP, FP, FN, precision, recall, F1, false
positives per minute, recognition latency, rest/contraction envelope
statistics, and fixed-versus-adaptive results. Unlabelled data produce
descriptive plots only; the software does not fabricate performance metrics.
The measured bring-up and 60-second integrity check are documented in
[docs/verification.md](docs/verification.md).

## Repository structure

| Path | Purpose |
|---|---|
| main/ | ESP-IDF firmware |
| desktop_app/ | PySide6 acquisition and experiment GUI |
| analysis/ | Capture, integrity checking and scientific analysis |
| docs/ | Hardware, protocol and experiment documentation |
| docs/images/ | Labelled locations for future reviewed graphics |
| data/ | Data policy; local recordings are ignored |
| figures/ | Figure policy; generated results are ignored |

## Limitations

- ADS1115 has no sample FIFO; shared-bus scheduling can reduce the recoverable
  rate below its configured 860 SPS.
- Thresholds and motion criteria require validation for each electrode
  placement and population.
- Lead-off signals indicate connection state but do not quantify contact
  impedance.
- No medical, safety-critical or autonomous actuation claim is made.

## Citation

A peer-reviewed citation and DOI are not yet available. For the future article:

> Felix Bembiev et al. “EMCS Research Platform.” Journal, year.
> DOI: to be assigned.

Until then, cite the repository URL and the exact Git commit used in the
experiment.

## License

MIT License. Copyright (c) 2026 Felix Bembiev.
