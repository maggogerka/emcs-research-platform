"""Reusable real-time graph with research-friendly inspection and export tools."""

from __future__ import annotations

from collections import deque
import csv
from pathlib import Path
from typing import Iterable

import numpy as np
import pyqtgraph as pg
import pyqtgraph.exporters
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


PHASE_BRUSHES = {
    "calibration_rest": (150, 150, 150, 40),
    "prepare": (45, 140, 230, 35),
    "contract": (235, 70, 70, 45),
    "rest": (40, 180, 100, 35),
    "paused": (240, 180, 40, 45),
}


class ScientificPlot(QWidget):
    """A bounded display buffer; callers remain responsible for lossless recording."""

    expand_requested = Signal(object)

    def __init__(
        self,
        title: str,
        y_label: str,
        curves: dict[str, str],
        *,
        max_points: int = 60_000,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._paused = False
        self._window_s = 10
        self._data: dict[str, deque[float]] = {
            "time": deque(maxlen=max_points),
            **{name: deque(maxlen=max_points) for name in curves},
        }
        self._phase_regions: list[pg.LinearRegionItem] = []
        self._event_lines: list[pg.InfiniteLine] = []

        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        self.window_combo = QComboBox()
        for seconds in (5, 10, 30, 60):
            self.window_combo.addItem(f"{seconds} s", seconds)
        self.window_combo.setCurrentText("10 s")
        self.window_combo.currentIndexChanged.connect(
            lambda: setattr(self, "_window_s", int(self.window_combo.currentData()))
        )
        self.pause_button = QPushButton("Pause display")
        self.pause_button.setCheckable(True)
        self.pause_button.toggled.connect(self._set_paused)
        auto_x = QPushButton("Auto X")
        auto_y = QPushButton("Auto Y")
        auto_x.clicked.connect(lambda: self.plot.enableAutoRange(axis="x"))
        auto_y.clicked.connect(lambda: self.plot.enableAutoRange(axis="y"))
        curves_button = QToolButton()
        curves_button.setText("Curves")
        curves_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        curves_button.setMenu(QMenu(curves_button))
        export_button = QToolButton()
        export_button.setText("Export")
        export_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        export_button.setMenu(QMenu(export_button))
        for label, callback in (("PNG", self._export_png), ("SVG", self._export_svg), ("CSV", self._export_csv)):
            action = export_button.menu().addAction(label)
            action.triggered.connect(callback)
        expand = QPushButton("Expand / restore")
        expand.clicked.connect(lambda: self.expand_requested.emit(self))
        for widget in (QLabel("Window:"), self.window_combo, self.pause_button, auto_x, auto_y,
                       curves_button, export_button, expand):
            controls.addWidget(widget)
        controls.addStretch()
        layout.addLayout(controls)

        self.plot = pg.PlotWidget(title=title)
        self.plot.setLabel("bottom", "Device time", units="s")
        self.plot.setLabel("left", y_label)
        self.plot.showGrid(x=True, y=True, alpha=0.2)
        self.plot.setMouseEnabled(x=True, y=True)
        self.curves: dict[str, pg.PlotDataItem] = {}
        for name, color in curves.items():
            item = self.plot.plot(name=name, pen=pg.mkPen(color, width=1.2))
            self.curves[name] = item
            action = QAction(name, curves_button.menu(), checkable=True, checked=True)
            action.toggled.connect(item.setVisible)
            curves_button.menu().addAction(action)
        self.plot.addLegend()
        self.v_crosshair = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen("#aaaaaa"))
        self.h_crosshair = pg.InfiniteLine(angle=0, movable=False, pen=pg.mkPen("#aaaaaa"))
        self.plot.addItem(self.v_crosshair, ignoreBounds=True)
        self.plot.addItem(self.h_crosshair, ignoreBounds=True)
        self.crosshair_label = QLabel("x: —, y: —")
        self.plot.scene().sigMouseMoved.connect(self._mouse_moved)
        self.plot.scene().sigMouseClicked.connect(lambda event: self.expand_requested.emit(self) if event.double() else None)
        layout.addWidget(self.plot, stretch=1)
        layout.addWidget(self.crosshair_label)

    def link_time_axis(self, other: "ScientificPlot") -> None:
        self.plot.setXLink(other.plot)

    def append(self, time_s: float, values: dict[str, float]) -> None:
        self._data["time"].append(float(time_s))
        for name in self.curves:
            self._data[name].append(float(values.get(name, np.nan)))

    def refresh(self, max_display_points: int = 4000) -> None:
        if self._paused or not self._data["time"]:
            return
        time_values = np.asarray(self._data["time"], dtype=float)
        first = np.searchsorted(time_values, time_values[-1] - self._window_s)
        step = max(1, (len(time_values) - first) // max_display_points)
        view_time = time_values[first::step]
        for name, curve in self.curves.items():
            curve.setData(view_time, np.asarray(self._data[name], dtype=float)[first::step])
        self.plot.setXRange(max(time_values[0], time_values[-1] - self._window_s), time_values[-1], padding=0)

    def add_phase(self, start_s: float, end_s: float, phase: str) -> None:
        region = pg.LinearRegionItem(
            values=(start_s, end_s), movable=False,
            brush=pg.mkBrush(PHASE_BRUSHES.get(phase, (120, 120, 120, 30))),
            pen=pg.mkPen(None),
        )
        region.setZValue(-20)
        self.plot.addItem(region)
        self._phase_regions.append(region)

    def add_event(self, time_s: float, label: str, color: str = "#ffcc33") -> None:
        line = pg.InfiniteLine(pos=time_s, angle=90, pen=pg.mkPen(color, width=1), label=label)
        self.plot.addItem(line)
        self._event_lines.append(line)

    def clear(self) -> None:
        for values in self._data.values():
            values.clear()
        for item in self._phase_regions + self._event_lines:
            self.plot.removeItem(item)
        self._phase_regions.clear()
        self._event_lines.clear()

    def _set_paused(self, paused: bool) -> None:
        self._paused = paused
        self.pause_button.setText("Resume display" if paused else "Pause display")

    def _mouse_moved(self, position: object) -> None:
        if not self.plot.sceneBoundingRect().contains(position):
            return
        point = self.plot.plotItem.vb.mapSceneToView(position)
        self.v_crosshair.setPos(point.x())
        self.h_crosshair.setPos(point.y())
        self.crosshair_label.setText(f"x: {point.x():.3f} s, y: {point.y():.6g}")

    def _choose_export(self, extension: str) -> Path | None:
        name, _ = QFileDialog.getSaveFileName(self, "Export graph", f"graph.{extension}", f"*.{extension}")
        return Path(name) if name else None

    def _export_png(self) -> None:
        if path := self._choose_export("png"):
            exporter = pg.exporters.ImageExporter(self.plot.plotItem)
            exporter.parameters()["width"] = 2400
            exporter.export(str(path))

    def _export_svg(self) -> None:
        if path := self._choose_export("svg"):
            pg.exporters.SVGExporter(self.plot.plotItem).export(str(path))

    def _export_csv(self) -> None:
        if not (path := self._choose_export("csv")):
            return
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            names = list(self.curves)
            writer.writerow(["time_s", *names])
            writer.writerows(zip(self._data["time"], *(self._data[name] for name in names), strict=True))
