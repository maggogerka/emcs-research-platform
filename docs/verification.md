# Hardware verification

This report records the real bench check performed on 2026-09-16 with the
ESP32-S3 connected as COM13. It is not a simulated or synthetic result.

## Firmware bring-up

The ESP-IDF project was built for `esp32s3`, flashed through COM13 and observed
on the physical serial interface. The startup diagnostics reported:

- I2C bus on SDA GPIO8 and SCL GPIO9 at 100 kHz;
- ADS1115 found at `0x48` and configured for continuous AIN0-to-GND conversion,
  860 SPS and the +/-4.096 V range;
- MPU6050 found at `0x68`, `WHO_AM_I = 0x68`, configured for 100 Hz, +/-2 g and
  +/-250 degrees/s;
- I2C scan result: two expected devices and zero scan errors;
- gyroscope stationary-bias calibration completed from 200 samples.

## Continuous acquisition check

The final capture ran for 60 seconds and was written locally as
`data/recordings/hardware_20260916_014401.csv`. Recordings are intentionally
excluded from Git because they can contain participant-derived data.

| Check | Measured result |
|---|---:|
| EMG samples | 51,520 |
| EMG timestamp span | 59.956141 s |
| Recoverable EMG rate | 859.278 samples/s |
| EMG timer gaps | 34 (0.066% of requested slots) |
| MPU6050 samples | 5,990 |
| MPU6050 timestamp span | 59.890081 s |
| MPU6050 rate | 99.9999 samples/s |
| MPU6050 timer gaps | 0 |
| Binary-frame sequence gaps | 0 |
| CRC errors | 0 |
| Runtime I2C errors | 0 |

The final status frame reported both sensors healthy, no UART transmit drops
and no runtime error flags. The analysis script generated the two 300 dpi
descriptive figures locally. No recognition metrics were calculated because
the recording contained no labelled contraction protocol.

## What remains to be validated

During this check the AD8232 electrodes were disconnected: LO- was asserted and
LO+ was clear. The firmware correctly remained in `LEADS_OFF`, did not complete
EMG calibration and produced no contraction events. Recognition accuracy,
fixed-versus-adaptive detector metrics, electrode contact quality and the
30-contraction protocol therefore remain to be verified with safely connected
electrodes. No recognition-success claim is made from this capture.
