# EMCS serial protocol

The device uses 460800 baud, 8 data bits, no parity and one stop bit. Commands
sent to the ESP32-S3 are newline-terminated ASCII. Responses and measurements
are little-endian binary frames.

## Frame

| Field | Type | Meaning |
|---|---:|---|
| magic | uint16 | 0xA55A |
| version | uint8 | 1 |
| type | uint8 | message type |
| payload length | uint16 | bytes |
| sequence | uint32 | monotonically increasing frame number |
| timestamp | uint64 | ESP timer microseconds |
| payload | bytes | type-specific packed data |
| CRC | uint16 | CRC-16/CCITT over version through payload |

The parser searches for the magic word and validates both length and CRC, so it
can recover after a board reset or an incomplete read.

Message types are STATUS (1), EMG_BATCH (2), IMU_BATCH (3), EVENT (4), ERROR
(5), ACK (6) and PHASE_MARKER (7). EMG batches contain 32 samples; IMU batches contain 10
samples. Each sample carries a timer-gap field. Together with frame sequence,
sample index and dropped counters this permits explicit loss detection rather
than silent interpolation.

## Commands

    STATUS
    PING
    STREAM START
    STREAM STOP
    RECORD START
    RECORD STOP
    CAL EMG
    CAL IMU
    MARK <marker_id> <phase_code> <trial> <prescribed_intensity_code>
    SET THRESH <Ton coefficient> <Toff coefficient>
    SET IMU <gyro threshold deg/s> <acceleration delta g>

Recording is performed by the desktop application. RECORD START and RECORD
STOP annotate the firmware stream so saved data retain the device-side state.

`MARK` causes the firmware to emit a packed `<uint32 marker_id, uint16 trial,
uint8 phase, uint8 prescribed_intensity>` frame with the ESP timer timestamp
assigned when the command is handled. Phase codes are idle 0, calibration 1,
prepare 2, contract 3, rest 4, completed 5, aborted-by-user 6, lead-off 7,
hardware-error 8 and paused 9. Prescribed-intensity codes are none 0, weak 1,
medium 2 and strong 3. Offline labels always use this device timestamp rather
than host receive time.
