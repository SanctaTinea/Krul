"""Descriptor-driven real-time plots for Starset.

The historical name ``GrathPlot`` is kept because this widget is exposed under
that name in Starset.  Plotting itself uses QPainter, so the GUI does not need
matplotlib or pyqtgraph at runtime.
"""

from __future__ import annotations

import csv
import math
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QPointF, QRectF, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)


NUMERIC_TYPES = {"integer", "float", "boolean", "unsigned"}
MAX_POINTS_PER_SERIES = 5000
MIN_BUFFER_SECONDS = 30.0
MAX_BUFFER_BYTES = 1024 ** 3
# Conservative accounting for a deque entry, timestamp, dictionary and values.
BUFFER_FRAME_BYTES = 320
BUFFER_VALUE_BYTES = 128


@dataclass(frozen=True)
class Measurement:
    """One scalar value addressable in a command result."""

    key: str
    command: str
    path: tuple[str, ...]
    tab: str
    group: str
    command_title: str
    label: str
    value_type: str

    @property
    def display_name(self) -> str:
        return f"{self.command_title} / {self.label}"


def discover_measurements(
    descriptors: dict[str, dict[str, Any]],
) -> dict[str, Measurement]:
    """Build a flat registry from numeric result fields in DESCRIBE metadata."""

    registry: dict[str, Measurement] = {}

    def visit(
        descriptor: dict[str, Any],
        field: dict[str, Any],
        path: tuple[str, ...] = (),
        labels: tuple[str, ...] = (),
    ) -> None:
        name = field.get("name")
        if not isinstance(name, str) or not name:
            return
        next_path = (*path, name)
        next_labels = (*labels, str(field.get("label") or name))
        field_type = str(field.get("type") or "")
        if field_type == "object":
            for child in field.get("fields", []):
                if isinstance(child, dict):
                    visit(descriptor, child, next_path, next_labels)
            return
        if field_type not in NUMERIC_TYPES:
            return

        command = str(descriptor.get("cmd") or "")
        if not command:
            return
        key = f"{command}:{'.'.join(next_path)}"
        registry[key] = Measurement(
            key=key,
            command=command,
            path=next_path,
            tab=str(descriptor.get("tab") or "Основное"),
            group=str(descriptor.get("group") or "Команды"),
            command_title=str(descriptor.get("title") or command),
            label=" / ".join(next_labels),
            value_type=field_type,
        )

    for descriptor in descriptors.values():
        if (
            descriptor.get("builtin")
            or descriptor.get("nogui")
            or not descriptor.get("autoupdate")
        ):
            continue
        for field in descriptor.get("result", []):
            if isinstance(field, dict):
                visit(descriptor, field)
    return registry


def _value_at_path(result: Any, path: tuple[str, ...]) -> float | None:
    value = result
    for name in path:
        if not isinstance(value, dict) or name not in value:
            return None
        value = value[name]
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    return None


class GraphCanvas(QWidget):
    """Small dependency-free multi-series line chart."""

    COLORS = (
        "#69A2FF",
        "#35C96F",
        "#FF7474",
        "#E0A84B",
        "#B58AF2",
        "#48D1CC",
        "#FF8FB3",
        "#C4D65B",
    )
    LINE_STYLES = {
        "solid": Qt.SolidLine,
        "dash": Qt.DashLine,
        "dot": Qt.DotLine,
        "dash_dot": Qt.DashDotLine,
        "none": Qt.NoPen,
    }

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.series: dict[str, list[tuple[float, float]]] = {}
        self.names: dict[str, str] = {}
        self.event_keys: set[str] = set()
        self.color_indices: dict[str, int] = {}
        self._next_color_index = 0
        self.x_label = "Время, с"
        self.show_points = False
        self.line_style = "solid"
        self.setMinimumSize(500, 320)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_show_points(self, visible: bool) -> None:
        self.show_points = bool(visible)
        self.update()

    def set_line_style(self, style: str) -> None:
        if style not in self.LINE_STYLES:
            raise ValueError(f"Unknown line style: {style}")
        self.line_style = style
        self.update()

    def set_series(self, keys: set[str], names: dict[str, str]) -> None:
        retained = [key for key in self.series if key in keys]
        added = sorted(
            keys.difference(retained),
            key=lambda key: names.get(key, key).casefold(),
        )
        for key in added:
            if key not in self.color_indices:
                self.color_indices[key] = self._next_color_index
                self._next_color_index += 1
        order = [*retained, *added]
        self.series = {key: self.series.get(key, []) for key in order}
        self.names = {key: names.get(key, key) for key in order}
        self.event_keys.intersection_update(keys)
        self.update()

    def set_event_series(self, keys: set[str]) -> None:
        self.event_keys = set(keys).intersection(self.series)
        self.update()

    def series_color(self, key: str) -> QColor:
        color_index = self.color_indices.get(key, 0)
        return QColor(self.COLORS[color_index % len(self.COLORS)])

    def append(self, key: str, x_value: float, y_value: float) -> None:
        if key not in self.series:
            return
        points = self.series[key]
        points.append((float(x_value), float(y_value)))
        if len(points) > MAX_POINTS_PER_SERIES:
            del points[: len(points) - MAX_POINTS_PER_SERIES]
        self.update()

    def prune_time(self, minimum_x: float) -> None:
        for points in self.series.values():
            first = 0
            while first < len(points) and points[first][0] < minimum_x:
                first += 1
            if first:
                del points[:first]

    def clear(self) -> None:
        for points in self.series.values():
            points.clear()
        self.update()

    @staticmethod
    def _expanded_range(low: float, high: float) -> tuple[float, float]:
        if math.isclose(low, high):
            margin = max(abs(low) * 0.05, 1.0)
            return low - margin, high + margin
        margin = (high - low) * 0.05
        return low - margin, high + margin

    def _data_ranges(
            self,
    ) -> tuple[tuple[float, float], tuple[float, float]] | None:
        all_points = [
            point for points in self.series.values() for point in points
        ]
        if not all_points:
            return None
        value_points = [
            point
            for key, points in self.series.items()
            if key not in self.event_keys
            for point in points
        ]
        x_range = self._expanded_range(
            min(point[0] for point in all_points),
            max(point[0] for point in all_points),
        )
        if not value_points:
            return x_range, (-0.05, 1.05)
        return x_range, self._expanded_range(
            min(point[1] for point in value_points),
            max(point[1] for point in value_points),
        )

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor("#17191D"))

        left, top, right, bottom = 72.0, 28.0, 24.0, 54.0
        plot = QRectF(
            left,
            top,
            max(1.0, self.width() - left - right),
            max(1.0, self.height() - top - bottom),
        )
        painter.setPen(QPen(QColor("#5A5D63"), 1))
        painter.drawRect(plot)

        ranges = self._data_ranges()
        if ranges is None:
            painter.setPen(QColor("#AAB2BF"))
            painter.drawText(plot, Qt.AlignCenter, "Выберите величины и дождитесь данных")
            painter.end()
            return

        (x_min, x_max), (y_min, y_max) = ranges

        painter.setPen(QPen(QColor("#343840"), 1))
        for index in range(1, 5):
            x = plot.left() + plot.width() * index / 5.0
            y = plot.top() + plot.height() * index / 5.0
            painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))

        painter.setPen(QColor("#AAB2BF"))
        for index in range(6):
            fraction = index / 5.0
            x_value = x_min + (x_max - x_min) * fraction
            y_value = y_max - (y_max - y_min) * fraction
            x = plot.left() + plot.width() * fraction
            y = plot.top() + plot.height() * fraction
            painter.drawText(QRectF(x - 42, plot.bottom() + 7, 84, 18), Qt.AlignCenter,
                             f"{x_value:.4g}")
            painter.drawText(QRectF(2, y - 9, left - 9, 18), Qt.AlignRight | Qt.AlignVCenter,
                             f"{y_value:.4g}")
        painter.drawText(
            QRectF(plot.left(), self.height() - 28, plot.width(), 20),
            Qt.AlignCenter,
            self.x_label,
        )

        def mapped(point: tuple[float, float]) -> QPointF:
            return QPointF(
                plot.left() + (point[0] - x_min) * plot.width() / (x_max - x_min),
                plot.bottom() - (point[1] - y_min) * plot.height() / (y_max - y_min),
            )

        for key, points in self.series.items():
            if not points:
                continue
            color = self.series_color(key)
            if key in self.event_keys:
                event_pen = QPen(color, 1.5, Qt.DashLine)
                painter.setPen(event_pen)
                for point in points:
                    position = mapped(point)
                    x = position.x()
                    painter.drawLine(
                        QPointF(x, plot.top()), QPointF(x, plot.bottom())
                    )
                    high = point[1] >= 0.5
                    tip_y = plot.top() + 3 if high else plot.bottom() - 3
                    base_y = tip_y + 8 if high else tip_y - 8
                    painter.setPen(QPen(color, 2, Qt.SolidLine))
                    painter.drawLine(QPointF(x, tip_y), QPointF(x - 4, base_y))
                    painter.drawLine(QPointF(x, tip_y), QPointF(x + 4, base_y))
                    label_y = plot.top() + 8 if high else plot.bottom() - 27
                    label_x = min(x + 6, plot.right() - 126)
                    painter.drawText(
                        QRectF(max(plot.left() + 2, label_x), label_y, 124, 18),
                        Qt.AlignLeft | Qt.AlignVCenter,
                        f"{'HIGH' if high else 'LOW'} · {self.names.get(key, key)}",
                    )
                    painter.setPen(event_pen)
                continue
            pen_style = self.LINE_STYLES[self.line_style]
            if pen_style != Qt.NoPen and len(points) > 1:
                line_pen = QPen(color, 2)
                line_pen.setStyle(pen_style)
                painter.setPen(line_pen)
                path = QPainterPath(mapped(points[0]))
                for point in points[1:]:
                    path.lineTo(mapped(point))
                painter.drawPath(path)
            if self.show_points:
                painter.setPen(QPen(color, 1))
                painter.setBrush(color)
                for point in points:
                    painter.drawEllipse(mapped(point), 3, 3)
                painter.setBrush(Qt.NoBrush)

        legend_x = plot.left() + 8
        legend_y = plot.top() + 8
        for key in self.series:
            if legend_y > plot.bottom() - 16:
                break
            color = self.series_color(key)
            if key in self.event_keys:
                painter.setPen(QPen(color, 2, Qt.DashLine))
                painter.drawLine(
                    QPointF(legend_x + 9, legend_y),
                    QPointF(legend_x + 9, legend_y + 13),
                )
                painter.setPen(QPen(color, 2, Qt.SolidLine))
                painter.drawLine(
                    QPointF(legend_x + 9, legend_y),
                    QPointF(legend_x + 5, legend_y + 6),
                )
                painter.drawLine(
                    QPointF(legend_x + 9, legend_y),
                    QPointF(legend_x + 13, legend_y + 6),
                )
                painter.setPen(QColor("#E7EAF0"))
                painter.drawText(
                    QPointF(legend_x + 25, legend_y + 11),
                    self.names.get(key, key),
                )
                legend_y += 18
                continue
            pen_style = self.LINE_STYLES[self.line_style]
            if pen_style != Qt.NoPen:
                legend_pen = QPen(color, 3)
                legend_pen.setStyle(pen_style)
                painter.setPen(legend_pen)
                painter.drawLine(QPointF(legend_x, legend_y + 6),
                                 QPointF(legend_x + 18, legend_y + 6))
            if self.show_points:
                painter.setPen(QPen(color, 1))
                painter.setBrush(color)
                painter.drawEllipse(QPointF(legend_x + 9, legend_y + 6), 3, 3)
                painter.setBrush(Qt.NoBrush)
            painter.setPen(QColor("#E7EAF0"))
            painter.drawText(QPointF(legend_x + 25, legend_y + 11), self.names.get(key, key))
            legend_y += 18
        painter.end()


class GrathPlotWindow(QMainWindow):
    """Real-time plot window fed by Starset command responses."""

    configuration_changed = Signal(dict)

    TIME_MODE = "time"
    XY_MODE = "xy"

    def __init__(
        self,
        descriptors: dict[str, dict[str, Any]] | None = None,
        requester: Callable[[str, dict[str, Any] | None], int | None] | None = None,
        parent: QWidget | None = None,
        gpio_requester: Callable[[dict[str, set[str]]], None] | None = None,
    ) -> None:
        super().__init__(parent, Qt.Window)
        self.setWindowTitle("GrathPlot — графики реального времени")
        self.resize(1300, 720)
        self.requester = requester
        self.gpio_requester = gpio_requester
        self.descriptors: dict[str, dict[str, Any]] = {}
        self.measurements: dict[str, Measurement] = {}
        self.transforms: dict[str, tuple[float, float]] = {}
        self.selected_keys: set[str] = set()
        self.latest_values: dict[str, float] = {}
        self.last_poll: dict[str, float] = {}
        self.in_flight: set[str] = set()
        self.gpio_source_by_key: dict[str, str] = {}
        self.gpio_pin_by_key: dict[str, str] = {}
        self.gpio_last_poll = 0.0
        self.started_at = time.monotonic()
        self.wall_clock_started_at = time.time()
        self.sample_buffer: deque[tuple[float, dict[str, float], int]] = deque()
        self.buffer_bytes = 0
        self.buffer_limit_bytes = MAX_BUFFER_BYTES
        self._tree_items: dict[str, QTreeWidgetItem] = {}
        self._tree_parents: dict[tuple[str, ...], QTreeWidgetItem] = {}
        self._transform_widgets: dict[
            str, tuple[QDoubleSpinBox, QDoubleSpinBox]
        ] = {}
        self._applying_configuration = False
        self._build_ui()
        self.set_descriptors(descriptors or {})

        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self._poll_sources)
        self.poll_timer.start(50)

    def _build_ui(self) -> None:
        central = QWidget()
        outer = QVBoxLayout(central)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Режим:"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Величина от времени", self.TIME_MODE)
        self.mode_combo.addItem("Величина от величины (X/Y)", self.XY_MODE)
        self.mode_combo.currentIndexChanged.connect(self._mode_changed)
        controls.addWidget(self.mode_combo)

        self.x_label = QLabel("Ось X:")
        controls.addWidget(self.x_label)
        self.x_combo = QComboBox()
        self.x_combo.setMinimumWidth(260)
        self.x_combo.currentIndexChanged.connect(self._axis_changed)
        controls.addWidget(self.x_combo)

        controls.addWidget(QLabel("Период:"))
        self.period_spin = QSpinBox()
        self.period_spin.setRange(50, 60000)
        self.period_spin.setValue(500)
        self.period_spin.setSuffix(" мс")
        self.period_spin.valueChanged.connect(self._emit_configuration_changed)
        controls.addWidget(self.period_spin)

        controls.addWidget(QLabel("Окно:"))
        self.history_spin = QSpinBox()
        self.history_spin.setRange(5, 3600)
        self.history_spin.setValue(60)
        self.history_spin.setSuffix(" с")
        self.history_spin.valueChanged.connect(self._history_changed)
        controls.addWidget(self.history_spin)

        self.auto_poll_check = QCheckBox("Автоопрос")
        self.auto_poll_check.setChecked(True)
        self.auto_poll_check.toggled.connect(self._emit_configuration_changed)
        controls.addWidget(self.auto_poll_check)
        self.pause_check = QCheckBox("Пауза")
        controls.addWidget(self.pause_check)
        clear_button = QPushButton("Очистить")
        clear_button.clicked.connect(self.clear)
        controls.addWidget(clear_button)
        controls.addStretch()
        outer.addLayout(controls)

        appearance = QHBoxLayout()
        appearance.addWidget(QLabel("Отображение:"))
        self.points_check = QCheckBox("Показывать точки")
        self.points_check.setChecked(False)
        self.points_check.toggled.connect(self._points_toggled)
        appearance.addWidget(self.points_check)
        appearance.addWidget(QLabel("Стиль линий:"))
        self.line_style_combo = QComboBox()
        self.line_style_combo.addItem("Сплошная", "solid")
        self.line_style_combo.addItem("Штриховая", "dash")
        self.line_style_combo.addItem("Точечная", "dot")
        self.line_style_combo.addItem("Штрих-пунктир", "dash_dot")
        self.line_style_combo.addItem("Без линий", "none")
        self.line_style_combo.currentIndexChanged.connect(self._line_style_changed)
        appearance.addWidget(self.line_style_combo)
        appearance.addStretch()
        self.save_image_button = QPushButton("Сохранить изображение")
        self.save_image_button.clicked.connect(self._save_plot_image_dialog)
        appearance.addWidget(self.save_image_button)
        self.merge_csv_cycles_check = QCheckBox("Разъеденить время измерений")
        self.merge_csv_cycles_check.setChecked(True)
        self.merge_csv_cycles_check.setToolTip(
            "Собирать полный цикл ответов в одну строку CSV с общим временем"
        )
        appearance.addWidget(self.merge_csv_cycles_check)
        self.export_csv_button = QPushButton("Экспорт CSV")
        self.export_csv_button.clicked.connect(self._export_csv_dialog)
        appearance.addWidget(self.export_csv_button)
        outer.addLayout(appearance)

        splitter = QSplitter(Qt.Horizontal)
        selector = QWidget()
        selector_layout = QVBoxLayout(selector)
        selector_layout.setContentsMargins(0, 0, 0, 0)
        selector_layout.addWidget(QLabel("Величины по группам (ось Y):"))
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Фильтр по имени")
        self.filter_edit.textChanged.connect(self._apply_filter)
        selector_layout.addWidget(self.filter_edit)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Величина", "Тип", "Множитель", "База"])
        self.tree.setColumnWidth(0, 250)
        self.tree.setColumnWidth(1, 65)
        self.tree.setColumnWidth(2, 82)
        self.tree.setColumnWidth(3, 82)
        self.tree.setStyleSheet("""
            QTreeWidget QHeaderView::section {
                background-color: #343840;
                color: #F2F4F8;
                border: none;
                border-right: 1px solid #5A5D63;
                border-bottom: 1px solid #5A5D63;
                padding: 4px 5px;
                font-weight: 600;
            }
        """)
        self.tree.headerItem().setToolTip(
            2, "Отображаемое значение = исходное × множитель + база"
        )
        self.tree.headerItem().setToolTip(
            3, "Отображаемое значение = исходное × множитель + база"
        )
        self.tree.itemChanged.connect(self._selection_changed)
        selector_layout.addWidget(self.tree, 1)
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        selector_layout.addWidget(self.status_label)
        splitter.addWidget(selector)

        self.canvas = GraphCanvas()
        splitter.addWidget(self.canvas)
        splitter.setSizes([520, 780])
        outer.addWidget(splitter, 1)
        self.setCentralWidget(central)
        self._mode_changed()

    def _points_toggled(self, visible: bool) -> None:
        self.canvas.set_show_points(visible)
        self._emit_configuration_changed()

    def _line_style_changed(self) -> None:
        style = self.line_style_combo.currentData()
        if isinstance(style, str):
            self.canvas.set_line_style(style)
        self._emit_configuration_changed()

    def set_descriptors(self, descriptors: dict[str, dict[str, Any]]) -> None:
        previous_selection = set(self.selected_keys)
        previous_x = self.x_combo.currentData()
        previous_transforms = dict(self.transforms)
        self.descriptors = dict(descriptors)
        self.measurements = discover_measurements(self.descriptors)
        self.gpio_source_by_key.clear()
        self.gpio_pin_by_key.clear()
        self.selected_keys = previous_selection.intersection(self.measurements)
        self.transforms = {
            key: previous_transforms.get(key, (1.0, 0.0))
            for key in self.measurements
        }
        self.latest_values.clear()
        self.last_poll.clear()
        self.in_flight.clear()
        self.started_at = time.monotonic()
        self.wall_clock_started_at = time.time()
        self._clear_buffer()

        self._rebuild_measurement_tree(previous_x)

    def _rebuild_measurement_tree(self, previous_x: Any = None) -> None:
        previous_selection = set(self.selected_keys)
        previous_transforms = dict(self.transforms)
        self.selected_keys = previous_selection.intersection(self.measurements)
        self.transforms = {
            key: previous_transforms.get(key, (1.0, 0.0))
            for key in self.measurements
        }

        self.tree.blockSignals(True)
        self.tree.clear()
        self._tree_items.clear()
        self._transform_widgets.clear()
        self._tree_parents.clear()
        for measurement in sorted(
            self.measurements.values(),
            key=lambda value: (
                value.tab.casefold(),
                value.group.casefold(),
                value.command_title.casefold(),
                value.label.casefold(),
            ),
        ):
            self._add_measurement_tree_item(measurement)
        self.tree.blockSignals(False)
        self.tree.expandToDepth(1)

        self.x_combo.blockSignals(True)
        self.x_combo.clear()
        for measurement in sorted(
            self.measurements.values(), key=lambda value: value.display_name.casefold()
        ):
            self.x_combo.addItem(measurement.display_name, measurement.key)
        if previous_x in self.measurements:
            self.x_combo.setCurrentIndex(self.x_combo.findData(previous_x))
        self.x_combo.blockSignals(False)
        self._sync_canvas_series(rebuild=True)
        self._update_status()

    def _add_measurement_tree_item(self, measurement: Measurement) -> None:
        tab_key = (measurement.tab,)
        tab_item = self._tree_parents.get(tab_key)
        if tab_item is None:
            tab_item = QTreeWidgetItem(self.tree, [measurement.tab])
            self._tree_parents[tab_key] = tab_item
        group_key = (measurement.tab, measurement.group)
        group_item = self._tree_parents.get(group_key)
        if group_item is None:
            group_item = QTreeWidgetItem(tab_item, [measurement.group])
            self._tree_parents[group_key] = group_item
        command_key = (measurement.tab, measurement.group, measurement.command)
        command_item = self._tree_parents.get(command_key)
        if command_item is None:
            command_item = QTreeWidgetItem(group_item, [measurement.command_title])
            self._tree_parents[command_key] = command_item
        item = QTreeWidgetItem(command_item, [measurement.label, measurement.value_type])
        item.setData(0, Qt.UserRole, measurement.key)
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(
            0, Qt.Checked if measurement.key in self.selected_keys else Qt.Unchecked
        )
        self._tree_items[measurement.key] = item
        multiplier_value, base_value = self.transforms[measurement.key]
        multiplier = self._make_transform_spin(
            multiplier_value,
            "Множитель: отображаемое = исходное × множитель + база",
        )
        base = self._make_transform_spin(
            base_value,
            "База: отображаемое = исходное × множитель + база",
        )
        self._transform_widgets[measurement.key] = (multiplier, base)
        multiplier.valueChanged.connect(
            lambda _value, key=measurement.key: self._transform_changed(key)
        )
        base.valueChanged.connect(
            lambda _value, key=measurement.key: self._transform_changed(key)
        )
        self.tree.setItemWidget(item, 2, multiplier)
        self.tree.setItemWidget(item, 3, base)

    def register_gpio_inputs(self, definitions: list[dict[str, Any]]) -> None:
        """Register runtime GPIO inputs discovered by Starset's IOPanel."""

        previous_x = self.x_combo.currentData()
        old_gpio_keys = set(self.gpio_source_by_key)
        for key in old_gpio_keys:
            self.measurements.pop(key, None)
        self.gpio_source_by_key.clear()
        self.gpio_pin_by_key.clear()
        initial_values: dict[str, float] = {}

        for definition in definitions:
            key = str(definition.get("key") or "")
            source_id = str(definition.get("source_id") or "")
            pin_name = str(definition.get("pin_name") or "")
            if not key or not source_id or not pin_name or key in self.measurements:
                continue
            measurement = Measurement(
                key=key,
                command=f"@gpio:{source_id}",
                path=(pin_name,),
                tab=str(definition.get("tab") or "GPIO"),
                group=str(definition.get("group") or "Входы GPIO"),
                command_title=str(definition.get("source_title") or "GPIO"),
                label=str(definition.get("label") or pin_name),
                value_type="boolean",
            )
            self.measurements[key] = measurement
            self.gpio_source_by_key[key] = source_id
            self.gpio_pin_by_key[key] = pin_name
            if key not in old_gpio_keys:
                try:
                    initial_values[key] = float(bool(int(definition.get("state", 0))))
                except (TypeError, ValueError):
                    initial_values[key] = 0.0

        self._rebuild_measurement_tree(previous_x)
        if initial_values:
            # Baseline GPIO state is needed to detect the first real edge, but
            # it is not a poll cycle and must not create CSV-only columns for
            # pins the user never selected.
            self.latest_values.update(initial_values)

    def ingest_gpio_values(
            self, source_id: str, states: dict[str, int | bool]
    ) -> None:
        if self.pause_check.isChecked():
            return
        updated: dict[str, float] = {}
        for key, registered_source in self.gpio_source_by_key.items():
            pin_name = self.gpio_pin_by_key.get(key, "")
            if registered_source != source_id or pin_name not in states:
                continue
            try:
                updated[key] = float(bool(int(states[pin_name])))
            except (TypeError, ValueError):
                continue
        if updated:
            self._ingest_values(updated, time.monotonic())

    @staticmethod
    def _make_transform_spin(value: float, tooltip: str) -> QDoubleSpinBox:
        editor = QDoubleSpinBox()
        editor.setRange(-1e12, 1e12)
        editor.setDecimals(9)
        editor.setValue(value)
        editor.setSingleStep(0.1)
        editor.setKeyboardTracking(False)
        editor.setButtonSymbols(QDoubleSpinBox.NoButtons)
        editor.setFixedWidth(62)
        editor.setToolTip(tooltip)
        return editor

    def _transform_changed(self, key: str) -> None:
        widgets = self._transform_widgets.get(key)
        if widgets is None:
            return
        self.transforms[key] = (widgets[0].value(), widgets[1].value())
        self._rebuild_canvas_from_buffer()
        self._emit_configuration_changed()

    def _scaled_value(self, key: str, raw_value: float) -> float:
        multiplier, base = self.transforms.get(key, (1.0, 0.0))
        return raw_value * multiplier + base

    def _mode(self) -> str:
        return str(self.mode_combo.currentData())

    def _mode_changed(self) -> None:
        xy_mode = self._mode() == self.XY_MODE
        self.x_label.setVisible(xy_mode)
        self.x_combo.setVisible(xy_mode)
        self.history_spin.setEnabled(not xy_mode)
        self.canvas.x_label = (
            self.x_combo.currentText() if xy_mode else "Время, с"
        )
        self._sync_canvas_series(rebuild=True)
        self._update_status()
        self._emit_configuration_changed()

    def _axis_changed(self) -> None:
        if self._mode() == self.XY_MODE:
            self.canvas.x_label = self.x_combo.currentText() or "X"
            self._rebuild_canvas_from_buffer()
        self._emit_configuration_changed()

    def _history_changed(self) -> None:
        now = time.monotonic()
        self._prune_buffer(now)
        if self._mode() == self.TIME_MODE:
            self._rebuild_canvas_from_buffer(now)
        self._emit_configuration_changed()

    def _selection_changed(self, item: QTreeWidgetItem, _column: int) -> None:
        key = item.data(0, Qt.UserRole)
        if not isinstance(key, str):
            return
        if item.checkState(0) == Qt.Checked:
            self.selected_keys.add(key)
        else:
            self.selected_keys.discard(key)
        self._sync_canvas_series(rebuild=True)
        self._update_status()
        self._emit_configuration_changed()

    def profile_configuration(self) -> dict[str, Any]:
        return {
            "mode": self._mode(),
            "x_axis": self.x_combo.currentData(),
            "period_ms": self.period_spin.value(),
            "history_s": self.history_spin.value(),
            "auto_poll": self.auto_poll_check.isChecked(),
            "show_points": self.points_check.isChecked(),
            "line_style": self.line_style_combo.currentData(),
            "selected": sorted(self.selected_keys),
            "transforms": {
                key: {"multiplier": values[0], "base": values[1]}
                for key, values in self.transforms.items()
            },
        }

    def apply_profile_configuration(self, config: dict[str, Any]) -> None:
        if not isinstance(config, dict):
            return
        self._applying_configuration = True
        try:
            selected = config.get("selected", [])
            if isinstance(selected, list):
                self.selected_keys = {
                    key for key in selected
                    if isinstance(key, str) and key in self.measurements
                }
            transforms = config.get("transforms", {})
            if isinstance(transforms, dict):
                for key, values in transforms.items():
                    if key not in self.measurements or not isinstance(values, dict):
                        continue
                    try:
                        self.transforms[key] = (
                            float(values.get("multiplier", 1.0)),
                            float(values.get("base", 0.0)),
                        )
                    except (TypeError, ValueError, OverflowError):
                        continue

            for control, name in (
                (self.period_spin, "period_ms"),
                (self.history_spin, "history_s"),
            ):
                if name in config:
                    control.setValue(int(config[name]))
            if "auto_poll" in config:
                self.auto_poll_check.setChecked(bool(config["auto_poll"]))
            if "show_points" in config:
                self.points_check.setChecked(bool(config["show_points"]))

            mode_index = self.mode_combo.findData(config.get("mode"))
            if mode_index >= 0:
                self.mode_combo.setCurrentIndex(mode_index)
            style_index = self.line_style_combo.findData(config.get("line_style"))
            if style_index >= 0:
                self.line_style_combo.setCurrentIndex(style_index)

            self._rebuild_measurement_tree(config.get("x_axis"))
            self.canvas.set_show_points(self.points_check.isChecked())
            style = self.line_style_combo.currentData()
            if isinstance(style, str):
                self.canvas.set_line_style(style)
            self._mode_changed()
        except (TypeError, ValueError, OverflowError):
            pass
        finally:
            self._applying_configuration = False

    def _emit_configuration_changed(self, *_args: Any) -> None:
        if not self._applying_configuration:
            self.configuration_changed.emit(self.profile_configuration())

    def _sync_canvas_series(self, rebuild: bool = False) -> None:
        names = {
            key: self.measurements[key].display_name
            for key in self.selected_keys
            if key in self.measurements
        }
        self.canvas.set_series(set(names), names)
        self.canvas.set_event_series(
            self.selected_keys.intersection(self.gpio_source_by_key)
            if self._mode() == self.TIME_MODE
            else set()
        )
        if rebuild:
            self._rebuild_canvas_from_buffer()

    def _apply_filter(self, text: str) -> None:
        needle = text.casefold().strip()

        def visit(item: QTreeWidgetItem) -> bool:
            own_match = not needle or needle in item.text(0).casefold()
            child_match = False
            for index in range(item.childCount()):
                child_match = visit(item.child(index)) or child_match
            visible = own_match or child_match
            item.setHidden(not visible)
            return visible

        for index in range(self.tree.topLevelItemCount()):
            visit(self.tree.topLevelItem(index))

    def _update_status(self) -> None:
        if not self.measurements:
            self.status_label.setText(
                "Числовые величины появятся после подключения и чтения DESCRIBE."
            )
            return
        pollable = {
            self.measurements[key].command
            for key in self._source_keys()
            if key in self.measurements
            and self._automatic_params(self.measurements[key].command) is not None
        }
        self.status_label.setText(
            f"Выбрано: {len(self.selected_keys)}. "
            f"Автоматически опрашиваемых команд: {len(pollable)}. "
            f"Буфер: {self.buffer_bytes / (1024 * 1024):.1f} МиБ / "
            f"{self.buffer_limit_bytes / (1024 * 1024):.0f} МиБ. "
        )

    def _buffer_retention_seconds(self) -> float:
        return max(MIN_BUFFER_SECONDS, float(self.history_spin.value()))

    @staticmethod
    def _estimate_frame_bytes(value_count: int) -> int:
        return BUFFER_FRAME_BYTES + BUFFER_VALUE_BYTES * value_count

    def _append_buffer_frame(
            self, timestamp: float, values: dict[str, float]
    ) -> None:
        if not values:
            return
        estimated_bytes = self._estimate_frame_bytes(len(values))
        if estimated_bytes > self.buffer_limit_bytes:
            return
        self._prune_buffer(timestamp)
        self.sample_buffer.append((timestamp, dict(values), estimated_bytes))
        self.buffer_bytes += estimated_bytes
        self._prune_buffer(timestamp)

    def _prune_buffer(self, now: float) -> None:
        cutoff = now - self._buffer_retention_seconds()
        while self.sample_buffer and self.sample_buffer[0][0] < cutoff:
            _, _, estimated_bytes = self.sample_buffer.popleft()
            self.buffer_bytes -= estimated_bytes
        while self.sample_buffer and self.buffer_bytes > self.buffer_limit_bytes:
            _, _, estimated_bytes = self.sample_buffer.popleft()
            self.buffer_bytes -= estimated_bytes

    def _clear_buffer(self) -> None:
        self.sample_buffer.clear()
        self.buffer_bytes = 0

    def _rebuild_canvas_from_buffer(self, now: float | None = None) -> None:
        self.canvas.clear()
        if not self.selected_keys or not self.sample_buffer:
            return
        current_time = time.monotonic() if now is None else now
        self._prune_buffer(current_time)
        if not self.sample_buffer:
            return
        if self._mode() == self.TIME_MODE:
            cutoff = current_time - float(self.history_spin.value())
            previous_gpio: dict[str, float] = {}
            for timestamp, values, _ in self.sample_buffer:
                selected = self.selected_keys.intersection(values)
                for key in selected.intersection(self.gpio_source_by_key):
                    raw_value = values[key]
                    changed = (
                        key in previous_gpio
                        and raw_value != previous_gpio[key]
                    )
                    previous_gpio[key] = raw_value
                    if timestamp >= cutoff and changed:
                        self.canvas.append(
                            key,
                            timestamp - self.started_at,
                            raw_value,
                        )
                if timestamp < cutoff:
                    continue
                for key in selected.difference(self.gpio_source_by_key):
                    self.canvas.append(
                        key,
                        timestamp - self.started_at,
                        self._scaled_value(key, values[key]),
                    )
            return

        x_key = self.x_combo.currentData()
        if not isinstance(x_key, str):
            return
        latest: dict[str, float] = {}
        for _timestamp, values, _ in self.sample_buffer:
            latest.update(values)
            if x_key not in latest:
                continue
            if x_key not in values and not self.selected_keys.intersection(values):
                continue
            for key in self.selected_keys:
                if key in latest:
                    self.canvas.append(
                        key,
                        self._scaled_value(x_key, latest[x_key]),
                        self._scaled_value(key, latest[key]),
                    )

    def _source_keys(self) -> set[str]:
        keys = set(self.selected_keys)
        if self._mode() == self.XY_MODE:
            x_key = self.x_combo.currentData()
            if isinstance(x_key, str):
                keys.add(x_key)
        return keys

    def _automatic_params(self, command: str) -> dict[str, Any] | None:
        descriptor = self.descriptors.get(command, {})
        if not descriptor.get("autoupdate"):
            return None
        params: dict[str, Any] = {}
        for field in descriptor.get("params", []):
            if not isinstance(field, dict) or not isinstance(field.get("name"), str):
                return None
            if "default" not in field:
                return None
            params[str(field["name"])] = field["default"]
        return params

    def _poll_sources(self) -> None:
        if (
            self.pause_check.isChecked()
            or not self.auto_poll_check.isChecked()
            or (self.requester is None and self.gpio_requester is None)
        ):
            return
        source_keys = self._source_keys()
        commands = {
            self.measurements[key].command
            for key in source_keys
            if key in self.measurements and key not in self.gpio_source_by_key
        }
        now = time.monotonic()
        requested_ms = self.period_spin.value()
        for command in commands if self.requester is not None else ():
            if command in self.in_flight:
                continue
            params = self._automatic_params(command)
            if params is None:
                continue
            auto = self.descriptors.get(command, {}).get("autoupdate", {})
            minimum = int(auto.get("min_period", requested_ms))
            maximum = int(auto.get("max_period", max(minimum, requested_ms)))
            period_ms = min(max(requested_ms, minimum), maximum)
            if now - self.last_poll.get(command, 0.0) < period_ms / 1000.0:
                continue
            transaction = self.requester(command, params or None)
            if transaction is not None:
                self.in_flight.add(command)
                self.last_poll[command] = now

        gpio_requests: dict[str, set[str]] = {}
        for key in source_keys:
            source_id = self.gpio_source_by_key.get(key)
            pin_name = self.gpio_pin_by_key.get(key)
            if source_id is not None and pin_name is not None:
                gpio_requests.setdefault(source_id, set()).add(pin_name)
        if (
            gpio_requests
            and self.gpio_requester is not None
            and now - self.gpio_last_poll >= requested_ms / 1000.0
        ):
            self.gpio_requester(gpio_requests)
            self.gpio_last_poll = now

    def ingest_response(self, command: str, message: dict[str, Any]) -> None:
        """Receive every correlated Starset response, including manual ones."""

        self.in_flight.discard(command)
        now = time.monotonic()
        self.last_poll[command] = now
        if self.pause_check.isChecked() or not message.get("success"):
            return
        result = message.get("result")
        if not isinstance(result, dict):
            return

        updated: dict[str, float] = {}
        for key, measurement in self.measurements.items():
            if measurement.command != command:
                continue
            value = _value_at_path(result, measurement.path)
            if value is not None:
                updated[key] = value
        if not updated:
            return
        self._ingest_values(updated, now)

    def _ingest_values(self, updated: dict[str, float], now: float) -> None:
        previous_values = {
            key: self.latest_values[key]
            for key in updated
            if key in self.latest_values
        }
        self.latest_values.update(updated)
        self._append_buffer_frame(now, updated)
        self._update_status()

        if self._mode() == self.TIME_MODE:
            elapsed = now - self.started_at
            for key in self.selected_keys.intersection(updated):
                if key in self.gpio_source_by_key and (
                    key not in previous_values
                    or previous_values[key] == updated[key]
                ):
                    continue
                self.canvas.append(
                    key,
                    elapsed,
                    (
                        updated[key]
                        if key in self.gpio_source_by_key
                        else self._scaled_value(key, self.latest_values[key])
                    ),
                )
            self.canvas.prune_time(elapsed - self.history_spin.value())
            return

        x_key = self.x_combo.currentData()
        if not isinstance(x_key, str) or x_key not in self.latest_values:
            return
        if x_key not in updated and not self.selected_keys.intersection(updated):
            return
        x_value = self.latest_values[x_key]
        for key in self.selected_keys:
            if key in self.latest_values:
                self.canvas.append(
                    key,
                    self._scaled_value(x_key, x_value),
                    self._scaled_value(key, self.latest_values[key]),
                )

    def clear(self) -> None:
        self.latest_values.clear()
        self.started_at = time.monotonic()
        self.wall_clock_started_at = time.time()
        self._clear_buffer()
        self.canvas.clear()
        self._update_status()

    @staticmethod
    def _path_with_suffix(path: str, default_suffix: str) -> Path:
        target = Path(path)
        return target if target.suffix else target.with_suffix(default_suffix)

    def save_plot_image(self, path: str) -> Path:
        """Render the current plot canvas to PNG, JPEG or BMP."""

        target = self._path_with_suffix(path, ".png")
        formats = {
            ".png": "PNG",
            ".jpg": "JPEG",
            ".jpeg": "JPEG",
            ".bmp": "BMP",
        }
        image_format = formats.get(target.suffix.lower())
        if image_format is None:
            raise ValueError("Поддерживаются изображения PNG, JPEG и BMP")
        pixmap = self.canvas.grab()
        if pixmap.isNull() or not pixmap.save(str(target), image_format):
            raise OSError(f"Не удалось сохранить изображение: {target}")
        return target

    def _csv_frames(
            self, merge_cycles: bool
    ) -> list[tuple[float, dict[str, float]]]:
        frames = [
            (timestamp, dict(values))
            for timestamp, values, _estimated_bytes in self.sample_buffer
        ]
        if not merge_cycles:
            return frames

        expected_keys = {
            key for _timestamp, values in frames for key in values
        }
        merged: list[tuple[float, dict[str, float]]] = []
        cycle_timestamp: float | None = None
        cycle_values: dict[str, float] = {}
        for timestamp, values in frames:
            if cycle_timestamp is None:
                cycle_timestamp = timestamp
            # A fast source may answer again before the slowest source in the
            # same polling set. Keep its newest value in the pending cycle;
            # only publish rows containing the complete measurement set.
            cycle_values.update(values)
            if expected_keys.issubset(cycle_values):
                merged.append((cycle_timestamp, dict(cycle_values)))
                cycle_timestamp = None
                cycle_values.clear()
        return merged

    def export_buffer_csv(
            self, path: str, merge_cycles: bool | None = None
    ) -> tuple[Path, int]:
        """Write untransformed buffered frames as a UTF-8 CSV table."""

        self._prune_buffer(time.monotonic())
        if merge_cycles is None:
            merge_cycles = self.merge_csv_cycles_check.isChecked()
        keys = {
            key
            for _timestamp, values, _estimated_bytes in self.sample_buffer
            for key in values
        }
        if not keys:
            raise ValueError("Буфер данных пуст")
        ordered_keys = sorted(
            keys,
            key=lambda key: (
                self.measurements[key].display_name.casefold()
                if key in self.measurements else key.casefold()
            ),
        )
        target = self._path_with_suffix(path, ".csv")
        row_count = 0
        with target.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["timestamp", "time_s", *ordered_keys])
            for timestamp, values in self._csv_frames(merge_cycles):
                wall_timestamp = self.wall_clock_started_at + (
                    timestamp - self.started_at
                )
                writer.writerow([
                    datetime.fromtimestamp(wall_timestamp).astimezone().isoformat(
                        timespec="milliseconds"
                    ),
                    f"{timestamp - self.started_at:.6f}",
                    *(
                        values[key] if key in values else ""
                        for key in ordered_keys
                    ),
                ])
                row_count += 1
        return target, row_count

    def _save_plot_image_dialog(self) -> None:
        path, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Сохранить график",
            "graph.png",
            "PNG (*.png);;JPEG (*.jpg *.jpeg);;BMP (*.bmp)",
        )
        if not path:
            return
        if not Path(path).suffix:
            if selected_filter.startswith("JPEG"):
                path += ".jpg"
            elif selected_filter.startswith("BMP"):
                path += ".bmp"
            else:
                path += ".png"
        try:
            target = self.save_plot_image(path)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Сохранение графика", str(exc))
            return
        QMessageBox.information(self, "Сохранение графика", f"Сохранено: {target}")

    def _export_csv_dialog(self) -> None:
        path, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "Экспортировать данные",
            "graph_data.csv",
            "CSV (*.csv)",
        )
        if not path:
            return
        try:
            target, row_count = self.export_buffer_csv(path)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Экспорт CSV", str(exc))
            return
        QMessageBox.information(
            self,
            "Экспорт CSV",
            f"Сохранено строк: {row_count}\n{target}",
        )

    def showEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().showEvent(event)
        if hasattr(self, "poll_timer") and not self.poll_timer.isActive():
            self.poll_timer.start(50)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.poll_timer.stop()
        event.accept()


# Public widget name requested by Starset integrations.
GrathPlot = GrathPlotWindow
