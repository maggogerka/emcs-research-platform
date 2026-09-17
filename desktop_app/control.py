"""Safe Windows SendInput backend and testable gyro-to-pointer mapping."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import math
import os
from typing import Mapping

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, Signal

from .settings import ControlSettings


class MouseMapper:
    """Map calibrated angular velocity to bounded pointer velocity."""

    def __init__(self) -> None:
        self._filtered_x = 0.0
        self._filtered_y = 0.0
        self._fraction_x = 0.0
        self._fraction_y = 0.0

    def reset(self) -> None:
        self._filtered_x = self._filtered_y = 0.0
        self._fraction_x = self._fraction_y = 0.0

    @staticmethod
    def _dead_zone(value: float, threshold: float) -> float:
        if abs(value) <= threshold:
            return 0.0
        return math.copysign(abs(value) - threshold, value)

    def map_velocity(
        self,
        sample: Mapping[str, float],
        settings: ControlSettings,
    ) -> tuple[float, float]:
        settings.validate()
        x = float(sample[settings.x_axis])
        y = float(sample[settings.y_axis])
        if settings.swap_axes:
            x, y = y, x
        if settings.invert_x:
            x = -x
        if settings.invert_y:
            y = -y
        x = self._dead_zone(x, settings.dead_zone_dps) * settings.sensitivity
        y = self._dead_zone(y, settings.dead_zone_dps) * settings.sensitivity
        magnitude = math.hypot(x, y)
        if magnitude > settings.max_speed_px_s:
            scale = settings.max_speed_px_s / magnitude
            x *= scale
            y *= scale
        keep = settings.smoothing
        self._filtered_x = keep * self._filtered_x + (1.0 - keep) * x
        self._filtered_y = keep * self._filtered_y + (1.0 - keep) * y
        return self._filtered_x, self._filtered_y

    def displacement(
        self,
        sample: Mapping[str, float],
        settings: ControlSettings,
        dt_s: float,
    ) -> tuple[int, int]:
        vx, vy = self.map_velocity(sample, settings)
        self._fraction_x += vx * max(0.0, min(dt_s, 0.1))
        self._fraction_y += vy * max(0.0, min(dt_s, 0.1))
        dx = math.trunc(self._fraction_x)
        dy = math.trunc(self._fraction_y)
        self._fraction_x -= dx
        self._fraction_y -= dy
        return dx, dy


if os.name == "nt":
    ULONG_PTR = wintypes.WPARAM

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = (
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        )

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = (
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        )

    class INPUT_UNION(ctypes.Union):
        _fields_ = (("mi", MOUSEINPUT), ("ki", KEYBDINPUT))

    class INPUT(ctypes.Structure):
        _anonymous_ = ("union",)
        _fields_ = (("type", wintypes.DWORD), ("union", INPUT_UNION))


class SendInputBackend:
    """Minimal backend; construction is harmless and injection is explicit."""

    MOUSE_FLAGS = {
        "LMB": (0x0002, 0x0004),
        "RMB": (0x0008, 0x0010),
        "MMB": (0x0020, 0x0040),
    }

    def __init__(self) -> None:
        self.available = os.name == "nt"

    def _send(self, inputs: list[object]) -> None:
        if not self.available:
            raise RuntimeError("SendInput is only available on Windows")
        array = (INPUT * len(inputs))(*inputs)
        sent = ctypes.windll.user32.SendInput(len(array), array, ctypes.sizeof(INPUT))
        if sent != len(array):
            raise OSError(ctypes.get_last_error(), "SendInput failed")

    def move(self, dx: int, dy: int) -> None:
        if not dx and not dy:
            return
        self._send([INPUT(type=0, mi=MOUSEINPUT(dx, dy, 0, 0x0001, 0, 0))])

    def click(self, action: str, custom_key: str = "") -> None:
        if action in self.MOUSE_FLAGS:
            down, up = self.MOUSE_FLAGS[action]
            self._send(
                [
                    INPUT(type=0, mi=MOUSEINPUT(0, 0, 0, down, 0, 0)),
                    INPUT(type=0, mi=MOUSEINPUT(0, 0, 0, up, 0, 0)),
                ]
            )
            return
        if action == "double click":
            self.click("LMB")
            self.click("LMB")
            return
        key = " " if action == "Space" else "\r" if action == "Enter" else custom_key
        if not key:
            raise ValueError("custom key is empty")
        vk = ctypes.windll.user32.VkKeyScanW(ord(key[0])) & 0xFF
        self._send(
            [
                INPUT(type=1, ki=KEYBDINPUT(vk, 0, 0, 0, 0)),
                INPUT(type=1, ki=KEYBDINPUT(vk, 0, 0x0002, 0, 0)),
            ]
        )


class GlobalEmergencyHotkey(QObject, QAbstractNativeEventFilter):
    activated = Signal()
    HOTKEY_ID = 0xE5C5
    WM_HOTKEY = 0x0312
    VK_F12 = 0x7B

    def __init__(self) -> None:
        QObject.__init__(self)
        QAbstractNativeEventFilter.__init__(self)
        self.registered = False

    def register(self) -> bool:
        if os.name != "nt":
            return False
        self.registered = bool(
            ctypes.windll.user32.RegisterHotKey(None, self.HOTKEY_ID, 0, self.VK_F12)
        )
        return self.registered

    def unregister(self) -> None:
        if self.registered and os.name == "nt":
            ctypes.windll.user32.UnregisterHotKey(None, self.HOTKEY_ID)
        self.registered = False

    def nativeEventFilter(self, event_type, message):  # noqa: N802
        if os.name == "nt":
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == self.WM_HOTKEY and msg.wParam == self.HOTKEY_ID:
                self.activated.emit()
                return True, 0
        return False, 0
