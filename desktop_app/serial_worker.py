"""Serial acquisition worker isolated from the Qt GUI thread."""

from __future__ import annotations

import queue
import time

from PySide6.QtCore import QThread, Signal
import serial

from .protocol import FrameParser, decode_frame


class SerialWorker(QThread):
    connected = Signal(str)
    disconnected = Signal(str)
    status_received = Signal(dict)
    emg_received = Signal(dict)
    imu_received = Signal(dict)
    event_received = Signal(dict)
    marker_received = Signal(dict)
    message_received = Signal(str)
    error_received = Signal(str)
    transport_stats = Signal(dict)

    def __init__(self, port: str, baudrate: int = 460800) -> None:
        super().__init__()
        self.port = port
        self.baudrate = baudrate
        self._commands: queue.Queue[str] = queue.Queue()
        self._stop_requested = False
        self._serial: serial.Serial | None = None

    def send_command(self, command: str) -> None:
        self._commands.put(command.strip())

    def stop(self) -> None:
        self._stop_requested = True
        self._commands.put("STREAM STOP")

    def run(self) -> None:
        parser = FrameParser()
        last_sequence: int | None = None
        sequence_gaps = 0
        try:
            self._serial = serial.Serial(
                self.port,
                self.baudrate,
                timeout=0.05,
                write_timeout=0.5,
            )
            self.connected.emit(self.port)
            time.sleep(3.0)
            self._serial.reset_input_buffer()
            self._write("STATUS")
            self._write("STREAM START")

            last_stats = time.monotonic()
            while not self._stop_requested:
                while True:
                    try:
                        self._write(self._commands.get_nowait())
                    except queue.Empty:
                        break

                waiting = self._serial.in_waiting
                data = self._serial.read(max(waiting, 1))
                for frame in parser.feed(data):
                    if last_sequence is not None:
                        expected = (last_sequence + 1) & 0xFFFFFFFF
                        if frame.sequence != expected:
                            sequence_gaps += (frame.sequence - expected) & 0xFFFFFFFF
                    last_sequence = frame.sequence
                    kind, decoded = decode_frame(frame)
                    if kind == "status":
                        self.status_received.emit(decoded)
                    elif kind == "emg":
                        self.emg_received.emit(decoded)
                    elif kind == "imu":
                        self.imu_received.emit(decoded)
                    elif kind == "event":
                        self.event_received.emit(decoded)
                    elif kind == "marker":
                        self.marker_received.emit(decoded)
                    elif kind == "error":
                        self.error_received.emit(decoded)
                    elif kind == "ack":
                        self.message_received.emit(decoded)

                if time.monotonic() - last_stats >= 1.0:
                    self.transport_stats.emit(
                        {
                            "crc_errors": parser.crc_errors,
                            "sequence_gaps": sequence_gaps,
                            "discarded_bytes": parser.discarded_bytes,
                        }
                    )
                    last_stats = time.monotonic()
        except Exception as exc:
            self.error_received.emit(f"Serial connection failed: {exc}")
        finally:
            if self._serial is not None:
                try:
                    self._write("STREAM STOP")
                    self._serial.close()
                except Exception:
                    pass
            self._serial = None
            self.disconnected.emit(self.port)

    def _write(self, command: str) -> None:
        if self._serial is None or not self._serial.is_open:
            return
        self._serial.write((command + "\n").encode("ascii"))
