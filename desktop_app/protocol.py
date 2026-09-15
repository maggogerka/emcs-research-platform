"""Binary protocol shared by the EMCS GUI and acquisition tools."""

from __future__ import annotations

from dataclasses import dataclass
import struct
from typing import Any

FRAME_MAGIC = 0xA55A
FRAME_VERSION = 1
MAGIC_BYTES = struct.pack("<H", FRAME_MAGIC)

FRAME_STATUS = 1
FRAME_EMG_BATCH = 2
FRAME_IMU_BATCH = 3
FRAME_EVENT = 4
FRAME_ERROR = 5
FRAME_ACK = 6

ADS_PERIOD_US = 1163
IMU_PERIOD_US = 10_000
ADS_VOLTS_PER_BIT = 0.000125
ACCEL_LSB_PER_G = 16384.0
GYRO_LSB_PER_DPS = 131.0

STATE_NAMES = {
    0: "LEADS_OFF",
    1: "CALIBRATING",
    2: "REST",
    3: "CANDIDATE",
    4: "ACTIVE",
    5: "REFRACTORY",
}

EVENT_NAMES = {
    1: "CONTRACTION_START",
    2: "CONTRACTION_RELEASE",
    3: "EMG_CALIBRATION_DONE",
    4: "IMU_CALIBRATION_DONE",
    5: "LEADS_CHANGED",
}

DETECTOR_NAMES = {0: "fixed", 1: "adaptive", 2: "system"}

HEADER = struct.Struct("<HBBHIQ")
CRC = struct.Struct("<H")
STATUS = struct.Struct("<9B5I11f")
EMG_META = struct.Struct("<IHHffffBBBB")
EMG_SAMPLE = struct.Struct("<hfBB")
IMU_META = struct.Struct("<IHH")
IMU_SAMPLE = struct.Struct("<hhhhhhB")
EVENT = struct.Struct("<BBBBf")


def crc16_ccitt(data: bytes | bytearray | memoryview) -> int:
    crc = 0xFFFF
    for value in data:
        crc ^= value << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


@dataclass(slots=True)
class Frame:
    type: int
    sequence: int
    timestamp_us: int
    payload: bytes


class FrameParser:
    """Incremental, resynchronizing parser for a mixed boot-log/binary stream."""

    def __init__(self) -> None:
        self.buffer = bytearray()
        self.crc_errors = 0
        self.discarded_bytes = 0

    def feed(self, data: bytes) -> list[Frame]:
        self.buffer.extend(data)
        frames: list[Frame] = []
        while True:
            magic_index = self.buffer.find(MAGIC_BYTES)
            if magic_index < 0:
                keep = 1 if self.buffer.endswith(MAGIC_BYTES[:1]) else 0
                discarded = len(self.buffer) - keep
                self.discarded_bytes += discarded
                if discarded:
                    del self.buffer[:discarded]
                break
            if magic_index:
                self.discarded_bytes += magic_index
                del self.buffer[:magic_index]
            if len(self.buffer) < HEADER.size:
                break

            magic, version, frame_type, payload_length, sequence, timestamp_us = HEADER.unpack_from(
                self.buffer
            )
            if magic != FRAME_MAGIC or version != FRAME_VERSION or payload_length > 4096:
                del self.buffer[0]
                self.discarded_bytes += 1
                continue

            frame_size = HEADER.size + payload_length + CRC.size
            if len(self.buffer) < frame_size:
                break
            expected_crc = CRC.unpack_from(self.buffer, HEADER.size + payload_length)[0]
            actual_crc = crc16_ccitt(memoryview(self.buffer)[2 : HEADER.size + payload_length])
            if expected_crc != actual_crc:
                self.crc_errors += 1
                del self.buffer[0]
                continue

            payload = bytes(self.buffer[HEADER.size : HEADER.size + payload_length])
            frames.append(Frame(frame_type, sequence, timestamp_us, payload))
            del self.buffer[:frame_size]
        return frames


def decode_status(payload: bytes) -> dict[str, Any]:
    if len(payload) != STATUS.size:
        raise ValueError(f"invalid status payload length: {len(payload)}")
    values = STATUS.unpack(payload)
    byte_names = (
        "ads_ok",
        "mpu_ok",
        "leads",
        "detector_state",
        "stream_enabled",
        "recording",
        "emg_calibrated",
        "imu_calibrating",
        "motion",
    )
    integer_names = ("ads_samples", "ads_errors", "mpu_samples", "mpu_errors", "tx_drops")
    float_names = (
        "fixed_on",
        "fixed_off",
        "adaptive_on",
        "adaptive_off",
        "on_coefficient",
        "off_coefficient",
        "gyro_bias_x",
        "gyro_bias_y",
        "gyro_bias_z",
        "motion_gyro_dps",
        "motion_accel_delta_g",
    )
    result = dict(zip(byte_names, values[:9], strict=True))
    result.update(zip(integer_names, values[9:14], strict=True))
    result.update(zip(float_names, values[14:], strict=True))
    result["detector_state_name"] = STATE_NAMES.get(result["detector_state"], "UNKNOWN")
    result["lo_minus"] = bool(result["leads"] & 0x01)
    result["lo_plus"] = bool(result["leads"] & 0x02)
    return result


def decode_emg(frame: Frame) -> dict[str, Any]:
    if len(frame.payload) < EMG_META.size:
        raise ValueError("short EMG payload")
    (
        first_index,
        count,
        dropped,
        adaptive_on,
        adaptive_off,
        fixed_on,
        fixed_off,
        state,
        leads,
        motion,
        recording,
    ) = EMG_META.unpack_from(frame.payload)
    expected = EMG_META.size + count * EMG_SAMPLE.size
    if len(frame.payload) != expected:
        raise ValueError(f"invalid EMG payload length: {len(frame.payload)} != {expected}")

    samples: list[dict[str, Any]] = []
    timestamp_us = frame.timestamp_us
    sample_index = first_index
    offset = EMG_META.size
    for position in range(count):
        raw, envelope, flags, timer_gap = EMG_SAMPLE.unpack_from(frame.payload, offset)
        offset += EMG_SAMPLE.size
        if position:
            timestamp_us += timer_gap * ADS_PERIOD_US
            sample_index += timer_gap
        samples.append(
            {
                "timestamp_us": timestamp_us,
                "sample_index": sample_index,
                "timer_gap": timer_gap,
                "ads_raw": raw,
                "ads_voltage": raw * ADS_VOLTS_PER_BIT,
                "envelope_raw": envelope,
                "envelope_voltage": envelope * ADS_VOLTS_PER_BIT,
                "fixed_active": bool(flags & 0x01),
                "adaptive_active": bool(flags & 0x02),
                "fixed_event": bool(flags & 0x04),
                "adaptive_event": bool(flags & 0x08),
                "fixed_release": bool(flags & 0x10),
                "adaptive_release": bool(flags & 0x20),
            }
        )
    return {
        "samples": samples,
        "dropped_samples": dropped,
        "adaptive_on": adaptive_on,
        "adaptive_off": adaptive_off,
        "fixed_on": fixed_on,
        "fixed_off": fixed_off,
        "detector_state": state,
        "detector_state_name": STATE_NAMES.get(state, "UNKNOWN"),
        "leads": leads,
        "lo_minus": bool(leads & 0x01),
        "lo_plus": bool(leads & 0x02),
        "motion": bool(motion),
        "recording": bool(recording),
    }


def decode_imu(frame: Frame) -> dict[str, Any]:
    if len(frame.payload) < IMU_META.size:
        raise ValueError("short IMU payload")
    first_index, count, dropped = IMU_META.unpack_from(frame.payload)
    expected = IMU_META.size + count * IMU_SAMPLE.size
    if len(frame.payload) != expected:
        raise ValueError(f"invalid IMU payload length: {len(frame.payload)} != {expected}")

    samples: list[dict[str, Any]] = []
    timestamp_us = frame.timestamp_us
    sample_index = first_index
    offset = IMU_META.size
    for position in range(count):
        ax, ay, az, gx, gy, gz, timer_gap = IMU_SAMPLE.unpack_from(frame.payload, offset)
        offset += IMU_SAMPLE.size
        if position:
            timestamp_us += timer_gap * IMU_PERIOD_US
            sample_index += timer_gap
        samples.append(
            {
                "timestamp_us": timestamp_us,
                "sample_index": sample_index,
                "timer_gap": timer_gap,
                "ax": ax / ACCEL_LSB_PER_G,
                "ay": ay / ACCEL_LSB_PER_G,
                "az": az / ACCEL_LSB_PER_G,
                "gx": gx / GYRO_LSB_PER_DPS,
                "gy": gy / GYRO_LSB_PER_DPS,
                "gz": gz / GYRO_LSB_PER_DPS,
            }
        )
    return {"samples": samples, "dropped_samples": dropped}


def decode_event(frame: Frame) -> dict[str, Any]:
    if len(frame.payload) != EVENT.size:
        raise ValueError(f"invalid event payload length: {len(frame.payload)}")
    detector, event, state, _, envelope = EVENT.unpack(frame.payload)
    return {
        "timestamp_us": frame.timestamp_us,
        "detector": DETECTOR_NAMES.get(detector, "unknown"),
        "event": EVENT_NAMES.get(event, "UNKNOWN"),
        "state": STATE_NAMES.get(state, "UNKNOWN"),
        "envelope_raw": envelope,
        "envelope_voltage": envelope * ADS_VOLTS_PER_BIT,
    }


def decode_frame(frame: Frame) -> tuple[str, Any]:
    if frame.type == FRAME_STATUS:
        return "status", decode_status(frame.payload)
    if frame.type == FRAME_EMG_BATCH:
        return "emg", decode_emg(frame)
    if frame.type == FRAME_IMU_BATCH:
        return "imu", decode_imu(frame)
    if frame.type == FRAME_EVENT:
        return "event", decode_event(frame)
    if frame.type == FRAME_ERROR:
        return "error", frame.payload.decode("utf-8", errors="replace")
    if frame.type == FRAME_ACK:
        return "ack", frame.payload.decode("utf-8", errors="replace")
    return "unknown", frame.payload
