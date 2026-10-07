"""Reusable descriptor-driven controls and command widgets for Starset."""

from __future__ import annotations

import json
import math
import time
from collections import defaultdict
from typing import Any, Callable

from PySide6.QtCore import QEvent, QRectF, QSize, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractSpinBox,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from starset_theme import *
from starset_transport import DISCOVERY_TIMEOUT_S

class Indicator(QWidget):
    def __init__(self) -> None:
        super().__init__()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        # layout.setStyleSheet(f"""border: 1px solid""")

        self.led = QLabel()
        self.led.setFixedSize(14, 14)

        self.text = QLabel()
        self.text.setMinimumWidth(35)

        layout.addWidget(self.led)
        layout.addWidget(self.text)

        self.set_state(False)

    def set_state(self, state: bool) -> None:
        color = theme_color("success") if state else theme_color("inactive")
        border = theme_color("success_border") if state else theme_color("inactive_border")

        self.led.setStyleSheet(f"""
            QLabel {{
                background-color: {color};
                border: 1px solid {border};
                border-radius: 7px;
            }}
        """)

        self.text.setText("HIGH" if state else "LOW")
        self.text.setStyleSheet(f"""
            QLabel {{
                color: {color};
                font-weight: 600;
                font-size: 11px;
            }}
        """)


class PinSwitch(QAbstractButton):
    """GPIO output switch whose position follows confirmed pin state."""

    state_requested = Signal(int)

    def __init__(self, state: int = 0) -> None:
        super().__init__()
        self._state = bool(state)
        self._pending = False
        self.setFixedSize(40, 20)
        self.setCursor(Qt.PointingHandCursor)
        self.setAccessibleName("Состояние выхода")
        self.clicked.connect(self._request_opposite_state)
        self._refresh_tooltip()

    def set_state(self, state: int) -> None:
        confirmed_state = bool(state)
        self._state = confirmed_state
        self.update()
        self._refresh_tooltip()

    def state(self) -> int:
        return int(self._state)

    def set_pending(self, pending: bool) -> None:
        self._pending = pending
        self._refresh_tooltip()
        self.update()

    def is_pending(self) -> bool:
        return self._pending

    def _request_opposite_state(self) -> None:
        if self._pending:
            return
        self.state_requested.emit(0 if self._state else 1)

    def _refresh_tooltip(self) -> None:
        if self._pending:
            self.setToolTip("Ожидание ответа…")
        else:
            self.setToolTip("Выключить" if self._state else "Включить")

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        if self._pending:
            color_name = "pending"
            border_name = "pending_border"
        else:
            color_name = "success" if self._state else "inactive"
            border_name = "success_border" if self._state else "inactive_border"
        track_color = QColor(theme_color(color_name))
        border_color = QColor(theme_color(border_name))
        if self.isDown():
            track_color = track_color.darker(112)
            border_color = border_color.darker(112)
        if not self.isEnabled():
            track_color.setAlpha(110)
            border_color.setAlpha(110)

        track = QRectF(1.0, 1.0, self.width() - 2.0, self.height() - 2.0)
        radius = track.height() / 2.0
        painter.setPen(border_color)
        painter.setBrush(track_color)
        painter.drawRoundedRect(track, radius, radius)

        knob_size = self.height() - 6.0
        knob_x = self.width() - knob_size - 3.0 if self._state else 3.0
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawEllipse(QRectF(knob_x, 3.0, knob_size, knob_size))


class PinCard(QFrame):
    set_requested = Signal(object, int)

    def __init__(self, name: str, pin_type: str, state: int,
                 wire_value: Any | None = None, read_only: bool = False):
        super().__init__()
        self.name = name
        self.wire_value = name if wire_value is None else wire_value
        self.pin_type = pin_type
        self.setFrameShape(QFrame.StyledPanel)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(
            0, PIN_CARD_VERTICAL_MARGIN, 0, PIN_CARD_VERTICAL_MARGIN
        )
        # layout.setContentsMargins(
        #     0, 0, 0, 0
        # )

        self.indicator = Indicator()
        layout.addWidget(self.indicator)

        label = QLabel(name)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        label.setStyleSheet(f"""
                            QLabel {{
                                font-weight: regular;
                                letter-spacing: 1px;
                            }}""")
        layout.addWidget(label, 1)

        self.switch: PinSwitch | None = None
        if pin_type == "OUT" and not read_only:
            self.switch = PinSwitch(state)
            self.switch.state_requested.connect(
                lambda requested: self.set_requested.emit(
                    self.wire_value, requested)
            )
            layout.addWidget(self.switch)
        self.set_state(state)

    def set_state(self, state: int) -> None:
        self.state = int(bool(state))
        self.indicator.set_state(bool(self.state))
        if self.switch is not None:
            self.switch.set_state(self.state)

    def set_pending(self, pending: bool) -> None:
        if self.switch is not None:
            self.switch.set_pending(pending)


class ResultIntLabel(QLabel):
    def __init__(self, compact: bool = False, emphasized: bool = True):
        super().__init__("—")
        self.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.setMinimumWidth(80)

        font = self.font()
        font.setFamily("Consolas")
        self.setFont(font)

        self.setSizePolicy(
            QSizePolicy.Maximum,
            QSizePolicy.Preferred
        )

        margin = 0 if compact else 100
        font_weight = 600 if emphasized else 400
        self.setStyleSheet(f"""
            QLabel {{
                margin-left: {margin}px;
                padding: 3px 8px;
                font-weight: {font_weight};
            }}
        """)


class WidgetCompatibilityError(ValueError):
    """Descriptor cannot be handled by a registered widget class."""


class ParameterWidget(QWidget):
    """Base contract for registered input widgets."""

    def __init__(self, field: dict[str, Any]) -> None:
        super().__init__()
        self.field = field

    @classmethod
    def validate_descriptor(cls, field: dict[str, Any]) -> None:
        if not isinstance(field, dict):
            raise WidgetCompatibilityError("дескриптор поля должен быть object")

    def value(self) -> Any:
        raise NotImplementedError


class SpecialDacParameterWidget(ParameterWidget):
    """Synchronized full-width slider and numeric editor for a DAC code."""

    @classmethod
    def validate_descriptor(cls, field: dict[str, Any]) -> None:
        super().validate_descriptor(field)
        if field.get("type") != "integer":
            raise WidgetCompatibilityError(
                "special_dac поддерживает только поле типа integer"
            )
        constraints = field.get("constraints")
        if not isinstance(constraints, dict):
            raise WidgetCompatibilityError(
                "special_dac требует числовые ограничения minimum/maximum"
            )
        try:
            minimum = int(constraints["minimum"])
            maximum = int(constraints["maximum"])
        except (KeyError, TypeError, ValueError):
            raise WidgetCompatibilityError(
                "special_dac требует числовые ограничения minimum/maximum"
            ) from None
        if minimum >= maximum:
            raise WidgetCompatibilityError(
                "special_dac требует minimum меньше maximum"
            )

    def __init__(self, field: dict[str, Any]) -> None:
        super().__init__(field)
        constraints = field["constraints"]
        minimum = int(constraints["minimum"])
        maximum = int(constraints["maximum"])
        default = int(field.get("default", minimum))

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setObjectName("dacSlider")
        self.slider.setRange(minimum, maximum)
        self.slider.setValue(default)
        self.spinbox = QSpinBox()
        self.spinbox.setObjectName("dacValue")
        self.spinbox.setRange(minimum, maximum)
        self.spinbox.setValue(default)
        self.spinbox.setMinimumWidth(90)
        self.slider.valueChanged.connect(self.spinbox.setValue)
        self.spinbox.valueChanged.connect(self.slider.setValue)
        layout.addWidget(self.slider, 1)
        layout.addWidget(self.spinbox)

    def value(self) -> int:
        return self.spinbox.value()


class ResultWidget(QWidget):
    """Base contract for registered result widgets."""

    bold_label = False

    def __init__(self, field: dict[str, Any]) -> None:
        super().__init__()
        self.field = field

    @classmethod
    def validate_descriptor(cls, field: dict[str, Any]) -> None:
        if not isinstance(field, dict):
            raise WidgetCompatibilityError("дескриптор поля должен быть object")

    def setValue(self, value: Any) -> None:
        raise NotImplementedError


class SpecialAdcResultWidget(ResultWidget):
    """Raw integer ADC value with a client-side correction coefficient."""

    @classmethod
    def validate_descriptor(cls, field: dict[str, Any]) -> None:
        super().validate_descriptor(field)
        if field.get("type") != "integer":
            raise WidgetCompatibilityError(
                "special_adc поддерживает только поле типа integer"
            )

    def __init__(self, _field: dict[str, Any]) -> None:
        super().__init__(_field)
        self._raw_value: int | None = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.raw_output = ResultIntLabel(compact=True, emphasized=False)
        self.raw_output.setObjectName("adcRawValue")
        self.raw_output.setToolTip("RAW значение АЦП")
        layout.addWidget(self.raw_output)

        layout.addWidget(QLabel("Коэф.:"))
        self.correction_coefficient = QDoubleSpinBox()
        self.correction_coefficient.setObjectName("adcCorrectionCoefficient")
        self.correction_coefficient.setDecimals(6)
        self.correction_coefficient.setRange(-1e9, 1e9)
        self.correction_coefficient.setSingleStep(0.1)
        self.correction_coefficient.setValue(1.0)
        self.correction_coefficient.setToolTip("Поправочный коэффициент АЦП")
        self.correction_coefficient.setFixedWidth(120)
        self.correction_coefficient.setButtonSymbols(QDoubleSpinBox.NoButtons)
        layout.addWidget(self.correction_coefficient)

        layout.addWidget(QLabel("="))
        self.scaled_output = QLabel("—")
        self.scaled_output.setObjectName("adcScaledValue")
        self.scaled_output.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.scaled_output.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.scaled_output.setMinimumWidth(90)
        self.scaled_output.setStyleSheet(
            "QLabel { padding: 3px 8px; font-weight: 600; }"
        )
        self.scaled_output.setToolTip("RAW × коэффициент")
        layout.addWidget(self.scaled_output)
        layout.addStretch()

        self.correction_coefficient.valueChanged.connect(
            self._update_scaled_value
        )

    def setValue(self, value: Any) -> None:
        try:
            self._raw_value = int(value)
        except (TypeError, ValueError, OverflowError):
            self._raw_value = None

        self.raw_output.setText(
            "—" if self._raw_value is None else str(self._raw_value)
        )
        self._update_scaled_value()

    def profile_controls(self) -> dict[str, QDoubleSpinBox]:
        return {"correction_coefficient": self.correction_coefficient}

    def profile_configuration(self) -> dict[str, float]:
        return {"correction_coefficient": self.correction_coefficient.value()}

    def apply_profile_configuration(self, config: dict[str, Any]) -> None:
        controls = self.profile_controls()
        for name, control in controls.items():
            if name not in config:
                continue
            blocked = control.blockSignals(True)
            try:
                control.setValue(config[name])
            except (TypeError, ValueError, OverflowError):
                pass
            finally:
                control.blockSignals(blocked)
        self._update_scaled_value()

    def _update_scaled_value(self) -> None:
        if self._raw_value is None:
            self.scaled_output.setText("—")
            return

        scaled = self._raw_value * self.correction_coefficient.value()
        self.scaled_output.setText(f"{scaled:.9g}")


class SpecialAdcGroupResultWidget(ResultWidget):
    """ADC object whose integer fields share one correction coefficient."""

    bold_label = True

    @classmethod
    def validate_descriptor(cls, field: dict[str, Any]) -> None:
        super().validate_descriptor(field)
        if field.get("type") != "object":
            raise WidgetCompatibilityError(
                "special_adc_group поддерживает только поле типа object"
            )
        children = field.get("fields")
        if not isinstance(children, list) or not children:
            raise WidgetCompatibilityError(
                "special_adc_group требует непустой список fields"
            )
        invalid = [
            str(child.get("name") or "?")
            for child in children
            if not isinstance(child, dict)
            or child.get("type") != "integer"
            or not child.get("name")
        ]
        if invalid:
            raise WidgetCompatibilityError(
                "все fields special_adc_group должны быть именованными integer: "
                + ", ".join(invalid)
            )

    def __init__(self, field: dict[str, Any]) -> None:
        super().__init__(field)
        self._raw_values: dict[str, int | None] = {}
        self.raw_outputs: dict[str, ResultIntLabel] = {}
        self.scaled_outputs: dict[str, QLabel] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 0, 8)
        layout.setSpacing(6)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Коэф.:"))
        self.correction_coefficient = QDoubleSpinBox()
        self.correction_coefficient.setObjectName("adcGroupCorrectionCoefficient")
        self.correction_coefficient.setDecimals(6)
        self.correction_coefficient.setRange(-1e9, 1e9)
        self.correction_coefficient.setSingleStep(0.1)
        self.correction_coefficient.setValue(1.0)
        self.correction_coefficient.setToolTip(
            "Единый поправочный коэффициент группы"
        )
        self.correction_coefficient.setFixedWidth(120)
        self.correction_coefficient.setButtonSymbols(QDoubleSpinBox.NoButtons)
        controls.addWidget(self.correction_coefficient)
        controls.addStretch()
        layout.addLayout(controls)

        values = QGridLayout()
        values.setContentsMargins(0, 0, 0, 0)
        values.setHorizontalSpacing(12)
        values.addWidget(QLabel("Канал"), 0, 0)
        values.addWidget(QLabel("RAW"), 0, 1)
        values.addWidget(QLabel("Результат"), 0, 2)

        row = 1
        for child in field.get("fields", []):
            if child.get("type") != "integer" or not child.get("name"):
                continue
            name = str(child["name"])
            label = str(child.get("label") or name)
            raw_output = ResultIntLabel(compact=True, emphasized=False)
            raw_output.setObjectName(f"adcGroupRaw_{name}")
            scaled_output = QLabel("—")
            scaled_output.setObjectName(f"adcGroupScaled_{name}")
            scaled_output.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            scaled_output.setTextInteractionFlags(Qt.TextSelectableByMouse)
            scaled_output.setMinimumWidth(90)
            scaled_output.setStyleSheet(
                "QLabel { padding: 3px 8px; font-weight: 600; }"
            )
            scaled_output.setToolTip("RAW × коэффициент")

            self._raw_values[name] = None
            self.raw_outputs[name] = raw_output
            self.scaled_outputs[name] = scaled_output
            values.addWidget(QLabel(label), row, 0)
            values.addWidget(raw_output, row, 1)
            values.addWidget(scaled_output, row, 2)
            row += 1

        values.setColumnStretch(3, 1)
        layout.addLayout(values)

        self.correction_coefficient.valueChanged.connect(
            self._update_scaled_values
        )

    def setValue(self, value: Any) -> None:
        result = value if isinstance(value, dict) else {}
        for name, raw_output in self.raw_outputs.items():
            raw_value = result.get(name)
            try:
                parsed_value = int(raw_value)
            except (TypeError, ValueError, OverflowError):
                parsed_value = None
            self._raw_values[name] = parsed_value
            raw_output.setText("—" if parsed_value is None else str(parsed_value))
        self._update_scaled_values()

    def profile_controls(self) -> dict[str, QDoubleSpinBox]:
        return {"correction_coefficient": self.correction_coefficient}

    def profile_configuration(self) -> dict[str, float]:
        return {"correction_coefficient": self.correction_coefficient.value()}

    def apply_profile_configuration(self, config: dict[str, Any]) -> None:
        for name, control in self.profile_controls().items():
            if name not in config:
                continue
            blocked = control.blockSignals(True)
            try:
                control.setValue(config[name])
            except (TypeError, ValueError, OverflowError):
                pass
            finally:
                control.blockSignals(blocked)
        self._update_scaled_values()

    def _update_scaled_values(self) -> None:
        coefficient = self.correction_coefficient.value()
        for name, scaled_output in self.scaled_outputs.items():
            raw_value = self._raw_values[name]
            if raw_value is None:
                scaled_output.setText("—")
                continue
            scaled_output.setText(f"{raw_value * coefficient:.9g}")


PARAM_WIDGETS: dict[str, type[ParameterWidget]] = {
    "special_dac": SpecialDacParameterWidget,
}

RESULT_WIDGETS: dict[str, type[ResultWidget]] = {
    "special_adc": SpecialAdcResultWidget,
    "special_adc_group": SpecialAdcGroupResultWidget,
}


class ResultBoolLabel(QCheckBox):
    """Read-only boolean result rendered as a checkbox."""

    def __init__(self):
        super().__init__()
        self.setChecked(False)
        self.setTristate(True)
        self.setCheckState(Qt.PartiallyChecked)
        self.setFocusPolicy(Qt.NoFocus)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def setValue(self, value: Any) -> None:
        if value is None or value == "":
            self.setCheckState(Qt.PartiallyChecked)
            return
        self.setCheckState(
            Qt.Checked if value else Qt.Unchecked
        )


class ResultEnumLabel(QLabel):
    """Read-only enum result rendered with its descriptor title."""

    def __init__(self, field: dict[str, Any]):
        super().__init__("—")
        self._titles: dict[str, str] = {}
        constraints = field.get("constraints", {})
        for item in constraints.get("values", []):
            if not isinstance(item, dict) or "value" not in item:
                continue
            value = str(item["value"])
            self._titles[value] = str(item.get("title", value))
        self.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.setWordWrap(True)
        self.setAutoFillBackground(False)
        self.setStyleSheet(
            "QLabel { border: none; background: transparent; }"
        )

    def setValue(self, value: Any) -> None:
        if value is None:
            self.setText("—")
            self.setToolTip("")
            return
        raw_value = str(value)
        title = self._titles.get(raw_value, raw_value)
        self.setText(title)
        self.setToolTip(
            f"Значение протокола: {raw_value}" if title != raw_value else ""
        )


class ResponsiveCardGrid(QWidget):
    """Width-aware masonry grid for command and command-group cards."""

    def __init__(self, min_column_width: int, max_columns: int,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.min_column_width = max(1, min_column_width)
        self.max_columns = max(1, max_columns)
        self.cards: list[QWidget] = []
        self._columns = 1
        self._content_height = 0
        self._card_spans: dict[QWidget, int] = {}
        # Last (column, top, span) per card, so a resize in height never makes
        # a card jump sideways: existing cards keep their column and only slide
        # vertically, while the column count stays the same.
        self._card_slots: dict[QWidget, tuple[int, float, int]] = {}
        self._relayout_timer = QTimer(self)
        self._relayout_timer.setSingleShot(True)
        self._relayout_timer.timeout.connect(self._relayout)
        self.setObjectName("responsiveCardGrid")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def add_card(self, card: QWidget) -> None:
        card.setParent(self)
        card.installEventFilter(self)
        card.show()
        self.cards.append(card)
        self._schedule_relayout()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched in self.cards and event.type() in {
            QEvent.LayoutRequest,
            QEvent.Show,
            QEvent.Hide,
        }:
            self._schedule_relayout()
        return super().eventFilter(watched, event)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._relayout()

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        visible = [card for card in self.cards if not card.isHidden()]
        if not visible:
            return QSize(self.min_column_width, 0)
        recommended_columns = min(
            self.max_columns, max(1, math.ceil(math.sqrt(len(visible))))
        )
        base_width = (
            recommended_columns * self.min_column_width
            + (recommended_columns - 1) * COMMAND_GRID_HORIZONTAL_SPACING
        )
        widest = max(self._preferred_width(card) for card in visible)
        maximum_width = (
            self.max_columns * self.min_column_width
            + (self.max_columns - 1) * COMMAND_GRID_HORIZONTAL_SPACING
        )
        width = max(base_width, min(widest, maximum_width))
        height = self._content_height or max(
            card.sizeHint().height() for card in visible
        )
        return QSize(width, height)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return QSize(self.min_column_width, self._content_height)

    def _schedule_relayout(self) -> None:
        if not self._relayout_timer.isActive():
            self._relayout_timer.start(0)

    def request_relayout(self) -> None:
        self._schedule_relayout()

    @staticmethod
    def _preferred_width(card: QWidget) -> int:
        return max(
            1,
            card.minimumSizeHint().width(),
            card.sizeHint().width(),
        )

    @staticmethod
    def _height_for_width(card: QWidget, width: int) -> int:
        height = card.heightForWidth(width) if card.hasHeightForWidth() else -1
        if height < 0:
            height = card.sizeHint().height()
        return max(1, card.minimumSizeHint().height(), height)

    def _span_for_card(self, card: QWidget, columns: int,
                       column_width: float) -> int:
        if card.property("gridSpanMode") == "full":
            return columns
        minimum_width = max(1, card.minimumSizeHint().width())
        preferred_width = max(minimum_width, card.sizeHint().width())
        # A sizeHint can become very wide because of labels or a temporary
        # layout state. Treat it as a soft preference; only minimumSizeHint is
        # allowed to force an aggressively wide card.
        required_width = max(
            minimum_width,
            min(preferred_width, round(self.min_column_width * 1.35)),
        )
        natural_span = math.ceil(
            required_width / max(1.0, column_width * 1.25)
        )
        declared_span = card.property("gridPreferredSpan")
        if not isinstance(declared_span, int):
            declared_span = 1
        return min(columns, max(1, natural_span, declared_span))

    def _relayout(self) -> None:
        available_width = max(1, self.contentsRect().width())
        spacing_x = COMMAND_GRID_HORIZONTAL_SPACING
        spacing_y = COMMAND_GRID_VERTICAL_SPACING
        columns = max(
            1,
            min(
                self.max_columns,
                (available_width + spacing_x)
                // (self.min_column_width + spacing_x),
            ),
        )
        column_width = (
            available_width - spacing_x * (columns - 1)
        ) / columns
        if columns != self._columns:
            # The column count changed, so previously recorded slots no longer
            # apply and every card is assigned a column from scratch.
            self._card_slots.clear()
        self._columns = columns
        heights = [0.0] * columns
        self._card_spans.clear()
        slots: dict[QWidget, tuple[int, float, int]] = {}

        for card in self.cards:
            if card.isHidden():
                previous = self._card_slots.get(card)
                if previous is not None:
                    slots[card] = previous
                continue

            previous = self._card_slots.get(card)
            start: int | None = None
            span: int | None = None
            if previous is not None:
                previous_start, _previous_top, previous_span = previous
                if 0 < previous_span <= columns and \
                        previous_start + previous_span <= columns:
                    start = previous_start
                    span = previous_span
            if span is None:
                span = self._span_for_card(card, columns, column_width)
            if start is None:
                start = self._shortest_column(heights, span)

            width = column_width * span + spacing_x * (span - 1)
            height = self._height_for_width(card, round(width))
            # Order-preserving masonry: a card sits below everything already
            # placed in its columns. Its top therefore depends only on the
            # cards before it, so growing a card (unfolding a command) never
            # moves it or the cards above it - only those after it shift down.
            top = max(heights[start:start + span])
            left = start * (column_width + spacing_x)
            card.setGeometry(round(left), round(top), round(width), height)
            bottom = top + height + spacing_y
            for column in range(start, start + span):
                heights[column] = bottom
            self._card_spans[card] = span
            slots[card] = (start, top, span)

        self._card_slots = slots
        content_height = max(0, round(max(heights, default=0.0) - spacing_y))
        if self._content_height != content_height:
            self._content_height = content_height
            self.setFixedHeight(content_height)
            self.updateGeometry()

    @staticmethod
    def _shortest_column(heights: list[float], span: int) -> int:
        """Leftmost column range whose current bottom is the smallest."""
        best_start = 0
        best_top = max(heights[0:span])
        for start in range(1, len(heights) - span + 1):
            top = max(heights[start:start + span])
            if top < best_top:
                best_top = top
                best_start = start
        return best_start


class CollapseHeader(QToolButton):
    """Clickable header that folds a bound body widget open and closed.

    ``size`` is a scale factor for the arrow icon only; the label keeps the
    normal font size. The arrow points down while the body is expanded and
    right while the body is folded.
    """

    def __init__(self, title: str, object_name: str,
                 expanded: bool = True,
                 size: float = COLLAPSE_HEADER_SCALE) -> None:
        super().__init__()
        self.setObjectName(object_name)
        self.setText(title)
        self.setCheckable(True)
        self.setChecked(expanded)
        self.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        self.setCursor(Qt.PointingHandCursor)
        self.setAutoRaise(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._apply_size(size)
        self._body: QWidget | None = None
        self.toggled.connect(self._apply)

    def _apply_size(self, size: float) -> None:
        scale = max(0.1, float(size))
        arrow = max(4, round(COLLAPSE_HEADER_ARROW * scale))
        self.setIconSize(QSize(arrow, arrow))

    def fold(self, body: QWidget) -> None:
        self._body = body
        self._apply(self.isChecked())

    def is_expanded(self) -> bool:
        return self.isChecked()

    def set_expanded(self, expanded: bool) -> None:
        self.setChecked(expanded)

    def _apply(self, expanded: bool) -> None:
        self.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        if self._body is None:
            return
        self._body.setVisible(expanded)
        parent = self._body.parentWidget()
        while parent is not None:
            if isinstance(parent, ResponsiveCardGrid):
                parent.request_relayout()
                return
            parent = parent.parentWidget()


class ResponsivePinGrid(QWidget):
    def __init__(self):
        super().__init__()
        self.cards: list[PinCard] = []
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(PIN_GRID_HORIZONTAL_SPACING)
        self.grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.grid.setVerticalSpacing(PIN_GRID_VERTICAL_SPACING)
        self._columns = 0
        self._filter_text = ""

    def add_card(self, card: PinCard) -> None:
        self.cards.append(card)
        self._relayout(force=True)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._relayout()

    def _relayout(self, force: bool = False) -> None:
        columns = max(1, self.width() // 260)
        if columns == self._columns and not force:
            return
        self._columns = columns
        for card in self.cards:
            self.grid.removeWidget(card)
        visible = [
            card for card in self.cards
            if not self._filter_text or self._filter_text in card.name.casefold()
        ]
        for index, card in enumerate(visible):
            self.grid.addWidget(card, index // columns, index % columns)

    def apply_filter(self, text: str) -> None:
        self._filter_text = text.casefold().strip()
        for card in self.cards:
            card.setVisible(
                not self._filter_text or self._filter_text in card.name.casefold()
            )
        self._relayout(force=True)


class IOPanel(QWidget):
    def __init__(self, pins: list[dict[str, Any]], sender: Callable,
                 target: Any | None = None,
                 get_command: str = "PIN_GET",
                 set_command: str | None = "PIN_SET",
                 all_value: Any = "ALL",
                 update_observer: Callable | None = None,
                 use_internal_scroll: bool = True,
                 target_label: str | None = None):
        super().__init__()
        self.sender = sender
        self.target = target
        self.target_label = target_label or (
            str(target) if target is not None else None
        )
        self.get_command = get_command
        self.set_command = set_command
        self.read_only = set_command is None
        self.all_value = all_value
        self.update_observer = update_observer
        self.cards: dict[str, PinCard] = {}
        self._cards_by_wire: dict[Any, PinCard] = {}
        self._in_flight = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        filter_row = QHBoxLayout()
        filter_row.setContentsMargins(0, 0, 0, 0)
        filter_row.setSpacing(IO_FILTER_FIELD_SPACING)
        filter_row.addWidget(QLabel("Фильтр:"))
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Имя пина")
        filter_row.addWidget(self.filter_edit, 1)
        outer.addLayout(filter_row)

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        body.setObjectName("GPIO_outer")
        self.input_grid = ResponsivePinGrid()
        self.output_grid = ResponsivePinGrid()

        inputs = QGroupBox("Входы")
        inputs.setObjectName("gpioSection")
        input_layout = QVBoxLayout(inputs)
        input_layout.setContentsMargins(0, IO_SECTION_TOP_MARGIN, 0, 0)
        self.read_inputs_button = QPushButton("Прочитать")
        self.read_inputs_button.setFocusPolicy(Qt.NoFocus)
        self.read_inputs_button.clicked.connect(
            lambda _checked=False: self.poll_inputs(self.read_inputs_button)
        )
        input_layout.addWidget(self.input_grid)
        input_layout.addWidget(self.read_inputs_button, 0, Qt.AlignRight)

        outputs = QGroupBox("Выходы")
        outputs.setObjectName("gpioSection")
        output_layout = QVBoxLayout(outputs)
        output_layout.setContentsMargins(0, IO_SECTION_TOP_MARGIN, 0, 0)
        all_row = QHBoxLayout()
        all_row.setContentsMargins(0, 0, 0, 0)

        all_row.addStretch()

        output_layout.addWidget(self.output_grid)
        output_layout.addSpacing(IO_OUTPUT_ACTIONS_TOP_SPACING)
        output_layout.addLayout(all_row)

        body_layout.addWidget(inputs)
        body_layout.addWidget(outputs)

        self.activate_all_button = QPushButton("Активировать все")
        self.deactivate_all_button = QPushButton("Деактивировать все")
        self.activate_all_button.setFocusPolicy(Qt.NoFocus)
        self.deactivate_all_button.setFocusPolicy(Qt.NoFocus)
        self.activate_all_button.clicked.connect(
            lambda _checked=False: self._set_all(1, self.activate_all_button)
        )
        self.deactivate_all_button.clicked.connect(
            lambda _checked=False: self._set_all(0, self.deactivate_all_button)
        )
        self.read_outputs_button = QPushButton("Прочитать")
        self.read_outputs_button.setFocusPolicy(Qt.NoFocus)
        self.read_outputs_button.clicked.connect(
            lambda _checked=False: self.poll_outputs(self.read_outputs_button)
        )
        all_row.addWidget(self.read_outputs_button)
        if not self.read_only:
            all_row.addWidget(self.activate_all_button)
            all_row.addWidget(self.deactivate_all_button)
        body_layout.addStretch()
        if use_internal_scroll:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(body)
            outer.addWidget(scroll)
        else:
            # The command tab already owns a vertical QScrollArea.  Let this
            # panel expose its complete size hint so many pins enlarge the
            # page instead of being squeezed into a nested scroll viewport.
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
            body.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
            outer.addWidget(body)

        for pin in sorted(pins, key=lambda value: str(value.get("name", "")).casefold()):
            name = str(pin.get("name", ""))
            pin_type = str(pin.get("direction", pin.get("type", "IN"))).upper()
            if not name or pin_type not in {"IN", "OUT"}:
                continue
            wire_name = pin.get("_wire_name", pin.get("name"))
            card = PinCard(
                name, pin_type, int(pin.get("state", 0)), wire_name,
                read_only=self.read_only,
            )
            card.set_requested.connect(self._set_one)
            self.cards[name] = card
            self._cards_by_wire[wire_name] = card
            (self.output_grid if pin_type == "OUT" else self.input_grid).add_card(card)
        self.filter_edit.textChanged.connect(self._filter)

    def _filter(self, text: str) -> None:
        self.input_grid.apply_filter(text)
        self.output_grid.apply_filter(text)

    def _set_one(self, name: Any, state: int) -> None:
        card = self._cards_by_wire.get(name)
        if card is not None:
            card.set_pending(True)
        if self.target is None:
            params = {"pins": [{"name": name, "state": state}]}
        else:
            params = {"target": self.target, "name": name, "state": state}

        def updated(message: dict[str, Any]) -> None:
            if card is not None:
                card.set_pending(False)
            self._updated(message)

        transaction = self.sender(self.set_command, params, updated)
        if transaction is None and card is not None:
            card.set_pending(False)

    def _set_all(self, state: int,
                 action_button: QPushButton | None = None) -> None:
        output_cards = [
            card for card in self.cards.values() if card.pin_type == "OUT"
        ]
        if not output_cards:
            return
        for card in output_cards:
            card.set_pending(True)
        if action_button is not None:
            set_button_pending(action_button, True)
            action_button.setEnabled(False)
        if self.target is None:
            params = {"pins": [{"name": self.all_value, "state": state}]}
        else:
            params = {"target": self.target, "name": "ALL", "state": state}

        def updated(message: dict[str, Any]) -> None:
            for card in output_cards:
                card.set_pending(False)
            if action_button is not None:
                set_button_pending(action_button, False)
                action_button.setEnabled(True)
            self._updated(message)

        transaction = self.sender(self.set_command, params, updated)
        if transaction is None:
            for card in output_cards:
                card.set_pending(False)
            if action_button is not None:
                set_button_pending(action_button, False)
                action_button.setEnabled(True)

    def poll_inputs(self, action_button: QPushButton | None = None) -> None:
        if self._in_flight:
            return
        self._in_flight = True
        if action_button is not None:
            set_button_pending(action_button, True)
            action_button.setEnabled(False)
        if self.target is None:
            names = [card.wire_value for card in self.cards.values()
                     if card.pin_type == "IN"]
            params = {"pins": names}
        else:
            params = {"target": self.target, "name": "IN"}

        def updated(message: dict[str, Any]) -> None:
            if action_button is not None:
                set_button_pending(action_button, False)
                action_button.setEnabled(True)
            self._updated(message)

        transaction = self.sender(self.get_command, params, updated)
        if transaction is None:
            self._in_flight = False
            if action_button is not None:
                set_button_pending(action_button, False)
                action_button.setEnabled(True)

    def poll_outputs(self, action_button: QPushButton | None = None) -> None:
        if self._in_flight or not any(
                card.pin_type == "OUT" for card in self.cards.values()):
            return
        self._in_flight = True
        if action_button is not None:
            set_button_pending(action_button, True)
            action_button.setEnabled(False)
        if self.target is None:
            names = [
                card.wire_value for card in self.cards.values()
                if card.pin_type == "OUT"
            ]
            params = {"pins": names}
        else:
            params = {"target": self.target, "name": "OUT"}

        def updated(message: dict[str, Any]) -> None:
            if action_button is not None:
                set_button_pending(action_button, False)
                action_button.setEnabled(True)
            self._updated(message)

        transaction = self.sender(self.get_command, params, updated)
        if transaction is None:
            self._in_flight = False
            if action_button is not None:
                set_button_pending(action_button, False)
                action_button.setEnabled(True)

    def poll_pins(self, names: set[str]) -> None:
        """Poll an arbitrary graph-selected subset of input/output pins."""

        selected = [
            card.wire_value for card in self.cards.values()
            if card.name in names
        ]
        if self._in_flight or not selected:
            return
        self._in_flight = True
        if self.target is None:
            params = {"pins": selected}
        else:
            directions = {self.cards[name].pin_type for name in selected}
            selector = directions.pop() if len(directions) == 1 else "ALL"
            params = {"target": self.target, "name": selector}
        self.sender(self.get_command, params, self._updated)

    def _updated(self, message: dict[str, Any]) -> None:
        self._in_flight = False
        if not message.get("success"):
            return
        self.apply_update(message)
        if self.update_observer is not None:
            self.update_observer(self, message)

    def apply_update(self, message: dict[str, Any]) -> None:
        result = message.get("result", {})
        pins = result.get("pins", [])
        if not pins and result.get("name"):
            pins = [result]
        for pin in pins:
            if not isinstance(pin, dict) or "state" not in pin:
                continue
            wire_name = pin.get("name")
            if wire_name == self.all_value:
                try:
                    state = int(pin["state"])
                except (TypeError, ValueError):
                    continue
                for card in self.cards.values():
                    if card.pin_type == "OUT":
                        card.set_state(state)
                continue
            card = self._cards_by_wire.get(wire_name)
            if card is not None:
                try:
                    state = int(pin["state"])
                except (TypeError, ValueError):
                    continue
                card.set_state(state)


class CommandWidget:
    """Base contract for UI handlers that consume whole commands."""

    def __init__(self, window: "MainWindow",
                 descriptors: list[dict[str, Any]]) -> None:
        self.window = window
        self.descriptors = descriptors

    @classmethod
    def validate_descriptors(
            cls, descriptors: list[dict[str, Any]]) -> None:
        if not descriptors:
            raise WidgetCompatibilityError("список команд пуст")

    def handled_command_names(self) -> set[str]:
        return {str(descriptor["cmd"]) for descriptor in self.descriptors}

    def hidden_command_names(self) -> set[str]:
        return self.handled_command_names()

    def create_widget(self, _descriptor: dict[str, Any]) -> QWidget | None:
        """Create the command's content for one normal tab/group placement."""
        return None

    def build(self) -> None:
        raise NotImplementedError

    def poll(self, _period_ms: int) -> None:
        return


class SpecialGpioCommandWidget(CommandWidget):
    """Renders GPIO commands inside the normal descriptor-driven layout."""

    @staticmethod
    def _field_contains_name(field: dict[str, Any], name: str) -> bool:
        if field.get("name") == name:
            return True
        items = field.get("items")
        if (
            isinstance(items, dict)
            and SpecialGpioCommandWidget._field_contains_name(items, name)
        ):
            return True
        return any(
            isinstance(child, dict)
            and SpecialGpioCommandWidget._field_contains_name(child, name)
            for child in (field.get("fields") or [])
        )

    @classmethod
    def _split_descriptors(
            cls, descriptors: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        set_commands = [
            descriptor for descriptor in descriptors
            if any(
                cls._field_contains_name(field, "state")
                for field in descriptor.get("params", [])
            )
        ]
        get_commands = [
            descriptor for descriptor in descriptors
            if descriptor not in set_commands
        ]
        if not get_commands or len(set_commands) > 1:
            raise WidgetCompatibilityError(
                "special_gpio ожидает хотя бы одну команду чтения и не более "
                "одной команды записи; роли определяются по параметру state"
            )
        return get_commands, (set_commands[0] if set_commands else None)

    @classmethod
    def validate_descriptors(
            cls, descriptors: list[dict[str, Any]]) -> None:
        super().validate_descriptors(descriptors)
        get_descriptors, _ = cls._split_descriptors(descriptors)
        for get_descriptor in get_descriptors:
            names = {
                str(field.get("name"))
                for field in get_descriptor.get("params", [])
            }
            if "pins" not in names and not {"target", "name"}.issubset(names):
                raise WidgetCompatibilityError(
                    f"команда чтения {get_descriptor.get('cmd', '?')} должна "
                    "принимать pins либо пару target/name"
                )

    def __init__(self, window: "MainWindow",
                 descriptors: list[dict[str, Any]]) -> None:
        super().__init__(window, descriptors)
        self.get_descriptors, self.set_descriptor = self._split_descriptors(
            descriptors
        )
        self.set_command = (
            str(self.set_descriptor["cmd"]) if self.set_descriptor else None
        )
        self.panels: list[IOPanel] = []
        self.hosts: dict[str, list[QVBoxLayout]] = defaultdict(list)
        self._poll_slot: int | None = None

    def hidden_command_names(self) -> set[str]:
        # Read commands keep their normal tab/group/order placement.  The write
        # command, when present, is an implementation detail of the GPIO control.
        return {self.set_command} if self.set_command else set()

    def create_widget(self, descriptor: dict[str, Any]) -> QWidget | None:
        command = str(descriptor.get("cmd", ""))
        if not any(str(item.get("cmd", "")) == command
                   for item in self.get_descriptors):
            return None

        host = QGroupBox()
        host.setObjectName("commandForm")
        host.setProperty("gridSpanMode", "full")
        layout = QVBoxLayout(host)
        layout.setSpacing(COMMAND_FORM_SPACING)

        title = QLabel(str(descriptor.get("title") or command))
        title.setObjectName("commandTitle")
        title_font = title.font()
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)

        description = descriptor.get("description")
        if isinstance(description, str) and description:
            description_label = QLabel(description)
            description_label.setObjectName("commandDescription")
            description_label.setWordWrap(True)
            layout.addWidget(description_label)
        layout.addSpacing(COMMAND_SECTION_SPACING)

        content = QVBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(content)
        self.hosts[command].append(content)
        return host

    def build(self) -> None:
        for descriptor in self.get_descriptors:
            names = {
                str(field.get("name"))
                for field in descriptor.get("params", [])
            }
            if "pins" in names:
                self.window.send_request(
                    str(descriptor["cmd"]),
                    {"pins": []},
                    lambda message, item=descriptor: self._pins_received(
                        item, message
                    ),
                    timeout=DISCOVERY_TIMEOUT_S,
                )
            else:
                self.window.send_request(
                    "TARGET_LIST",
                    callback=lambda message, item=descriptor: (
                        self._targets_received(item, message)
                    ),
                    timeout=DISCOVERY_TIMEOUT_S,
                )

    def _make_panel(self, descriptor: dict[str, Any],
                    pins: list[dict[str, Any]],
                    target: Any | None = None,
                    target_label: str | None = None) -> IOPanel:
        normalized = self._decode_pin_enums(descriptor, pins)
        all_value = self._all_pin_value()
        return IOPanel(
            normalized,
            self.window.send_request,
            target,
            get_command=str(descriptor["cmd"]),
            set_command=self.set_command,
            all_value=all_value,
            update_observer=self._panel_updated,
            use_internal_scroll=False,
            target_label=target_label,
        )

    @staticmethod
    def _enum_titles(value: Any, field_name: str) -> dict[Any, str]:
        if isinstance(value, dict):
            if value.get("name") == field_name and value.get("type") == "enum":
                return {
                    item.get("value"): str(item.get("title", item.get("value", "")))
                    for item in value.get("constraints", {}).get("values", [])
                    if isinstance(item, dict) and "value" in item
                }
            for child in value.values():
                found = SpecialGpioCommandWidget._enum_titles(child, field_name)
                if found:
                    return found
        elif isinstance(value, list):
            for child in value:
                found = SpecialGpioCommandWidget._enum_titles(child, field_name)
                if found:
                    return found
        return {}

    def _decode_pin_enums(self, descriptor: dict[str, Any],
                          pins: list[dict[str, Any]]) -> list[dict[str, Any]]:
        names = self._enum_titles(descriptor.get("result", []), "name")
        types = self._enum_titles(descriptor.get("result", []), "type")
        directions = self._enum_titles(
            descriptor.get("result", []), "direction"
        )
        decoded: list[dict[str, Any]] = []
        for pin in pins:
            if not isinstance(pin, dict):
                continue
            item = dict(pin)
            raw_name = item.get("name")
            raw_type = item.get("direction", item.get("type"))
            pin_type = directions.get(raw_type, types.get(raw_type, raw_type))
            normalized_type = str(pin_type).casefold()
            if normalized_type in {"input", "in"}:
                pin_type = "IN"
            elif normalized_type in {"output", "out"}:
                pin_type = "OUT"
            item["_wire_name"] = raw_name
            item["name"] = names.get(raw_name, raw_name)
            item["direction"] = pin_type
            decoded.append(item)
        return decoded

    def _all_pin_value(self) -> Any:
        if self.set_descriptor is None:
            return None
        values = self._enum_titles(
            self.set_descriptor.get("params", []), "name")
        return next(iter(values), None)

    def _pins_received(self, descriptor: dict[str, Any],
                       message: dict[str, Any]) -> None:
        pins = self._result_pins(message)
        if pins is None:
            return
        self._add_panels(descriptor, pins)

    @staticmethod
    def _result_pins(
            message: dict[str, Any],
    ) -> list[dict[str, Any]] | None:
        if not message.get("success"):
            return None
        pins = message.get("result", {}).get("pins", [])
        return pins if isinstance(pins, list) else None

    def _add_panels(self, descriptor: dict[str, Any],
                    pins: list[dict[str, Any]],
                    target: Any | None = None,
                    target_label: str | None = None) -> None:
        command = str(descriptor["cmd"])
        for content in self.hosts.get(command, []):
            panel = self._make_panel(descriptor, pins, target, target_label)
            self.window._apply_button_cursors(panel)
            if target is None:
                content.addWidget(panel)
            else:
                target_box = QGroupBox(target_label or str(target))
                target_layout = QVBoxLayout(target_box)
                target_layout.addWidget(panel)
                content.addWidget(target_box)
            self.panels.append(panel)
            self.window.io_panels.append(panel)
            if self.window.io_panel is None:
                self.window.io_panel = panel
        self.window._sync_graph_gpio_inputs()

    def _panel_updated(self, source: IOPanel,
                       message: dict[str, Any]) -> None:
        for panel in self.panels:
            if (
                panel is not source
                and panel.target == source.target
                and panel.get_command == source.get_command
            ):
                panel.apply_update(message)
        self.window._gpio_panel_updated(source, message)

    def _targets_received(self, descriptor: dict[str, Any],
                          message: dict[str, Any]) -> None:
        if not message.get("success"):
            return
        targets = message.get("result", {}).get("targets", [])
        target_titles = self._enum_titles(
            descriptor.get("params", []), "target"
        )
        target_values = {
            title: value for value, title in target_titles.items()
        }
        pending_targets = [
            (
                target_values.get(str(target_info.get("name", "")),
                                  str(target_info.get("name", ""))),
                str(target_info.get("name", "")),
            )
            for target_info in (targets if isinstance(targets, list) else [])
            if isinstance(target_info, dict)
            if target_info.get("available", False)
            and str(target_info.get("name", ""))
        ]

        def request_next() -> None:
            if not pending_targets:
                return
            target, target_label = pending_targets.pop(0)

            def received(response: dict[str, Any]) -> None:
                self._target_pins_received(
                    descriptor, target, target_label, response
                )
                request_next()

            self.window.send_request(
                str(descriptor["cmd"]),
                {"target": target, "name": "ALL"},
                received,
            )

        request_next()

    def _target_pins_received(self, descriptor: dict[str, Any], target: Any,
                              target_label: str,
                              message: dict[str, Any]) -> None:
        pins = self._result_pins(message)
        if pins is None:
            return
        self._add_panels(descriptor, pins, target, target_label)

    def poll(self, period_ms: int) -> None:
        slot = int(time.monotonic() * 1000) // period_ms
        if self._poll_slot == slot:
            return
        self._poll_slot = slot
        polled_targets: set[tuple[str, Any | None]] = set()
        for panel in self.panels:
            key = (panel.get_command, panel.target)
            if key in polled_targets:
                continue
            polled_targets.add(key)
            panel.poll_inputs()


class SpecialPwmCommandWidget(CommandWidget):
    """Compact channel/duty/period control for the unified PWM command."""

    @classmethod
    def validate_descriptors(
            cls, descriptors: list[dict[str, Any]]) -> None:
        super().validate_descriptors(descriptors)
        if len(descriptors) != 1:
            raise WidgetCompatibilityError(
                "special_pwm ожидает ровно одну команду"
            )
        fields = {
            str(field.get("name")): field
            for field in descriptors[0].get("params", [])
        }
        if set(fields) != {"channel", "duty_cycle", "period_counter"}:
            raise WidgetCompatibilityError(
                "special_pwm требует параметры channel, duty_cycle и period_counter"
            )
        if fields["channel"].get("type") != "enum":
            raise WidgetCompatibilityError(
                "параметр channel special_pwm должен иметь тип enum"
            )
        for name in ("duty_cycle", "period_counter"):
            if fields[name].get("type") != "integer":
                raise WidgetCompatibilityError(
                    f"параметр {name} special_pwm должен иметь тип integer"
                )

    def hidden_command_names(self) -> set[str]:
        return set()

    @staticmethod
    def _integer_range(field: dict[str, Any]) -> tuple[int, int, int]:
        constraints = field.get("constraints", {})
        minimum = int(constraints.get("minimum", -2147483648))
        maximum = int(constraints.get("maximum", 2147483647))
        default = int(field.get("default", minimum))
        return minimum, maximum, default

    def create_widget(self, descriptor: dict[str, Any]) -> QWidget | None:
        fields = {
            str(field["name"]): field
            for field in descriptor.get("params", [])
        }
        host = QGroupBox()
        host.setObjectName("commandForm")
        host.setProperty("gridSpanMode", "full")
        layout = QVBoxLayout(host)
        layout.setSpacing(COMMAND_FORM_SPACING)

        title = QLabel(str(descriptor.get("title") or descriptor["cmd"]))
        title.setObjectName("commandTitle")
        font = title.font()
        font.setBold(True)
        title.setFont(font)
        layout.addWidget(title)

        channel_field = fields["channel"]
        duty_min, duty_max, duty_default = self._integer_range(
            fields["duty_cycle"]
        )
        period_min, period_max, period_default = self._integer_range(
            fields["period_counter"]
        )
        period_label = str(
            fields["period_counter"].get("label")
            or "Период счётчика (такты таймера)"
        )
        channels = channel_field.get("constraints", {}).get("values", [])
        for index, item in enumerate(channels):
            raw_channel = item.get("value")
            if not isinstance(raw_channel, str) or not raw_channel:
                continue
            row = QWidget()
            row.setObjectName("pwmChannelRow")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(8)

            channel_name = QLabel(str(item.get("title") or raw_channel))
            channel_name.setObjectName("pwmChannelName")
            channel_name.setMinimumWidth(120)
            row_layout.addWidget(channel_name)
            row_layout.addWidget(QLabel("Скважность, %:"))

            duty_slider = QSlider(Qt.Horizontal)
            duty_slider.setObjectName("pwmDutySlider")
            duty_slider.setRange(duty_min, duty_max)
            duty_slider.setValue(duty_default)
            duty_slider.setMinimumWidth(140)
            duty_spin = QSpinBox()
            duty_spin.setObjectName("pwmDutyValue")
            duty_spin.setRange(duty_min, duty_max)
            duty_spin.setValue(duty_default)
            duty_spin.setSuffix(" %")
            duty_slider.valueChanged.connect(duty_spin.setValue)
            duty_spin.valueChanged.connect(duty_slider.setValue)
            row_layout.addWidget(duty_slider, 1)
            row_layout.addWidget(duty_spin)

            row_layout.addWidget(QLabel(f"{period_label}:"))
            period = QSpinBox()
            period.setObjectName("pwmPeriodValue")
            period.setRange(period_min, period_max)
            period.setValue(period_default)
            period.setMinimumWidth(100)
            row_layout.addWidget(period)

            button = QPushButton("Применить")
            button.setObjectName("pwmApplyButton")

            def submit(
                    _checked: bool = False, *, channel: str = raw_channel,
                    duty_editor: QSpinBox = duty_spin,
                     period_editor: QSpinBox = period,
                     apply_button: QPushButton = button) -> None:
                set_button_pending(apply_button, True)
                apply_button.setEnabled(False)

                def finished(_message: dict[str, Any]) -> None:
                    set_button_pending(apply_button, False)
                    apply_button.setEnabled(True)

                transaction = self.window.send_request(
                    str(descriptor["cmd"]),
                    {
                        "channel": channel,
                        "duty_cycle": duty_editor.value(),
                        "period_counter": period_editor.value(),
                    },
                    finished,
                )
                if transaction is None:
                    set_button_pending(apply_button, False)
                    apply_button.setEnabled(True)

            button.clicked.connect(submit)
            row_layout.addWidget(button)
            layout.addWidget(row)
        return host

    def build(self) -> None:
        return


COMMAND_WIDGETS: dict[str, type[CommandWidget]] = {
    "special_gpio": SpecialGpioCommandWidget,
    "special_pwm": SpecialPwmCommandWidget,
}

# Результаты этих типов рисуются схемой, без widget_hint.
_STRUCTURED_RESULT_TYPES = ("object", "array", "union")


def _clear_result_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
            continue
        child = item.layout()
        if child is not None:
            _clear_result_layout(child)


def _result_text(value: Any) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, bool):
        return "да" if value else "нет"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _result_scalar_widget(field: dict[str, Any], value: Any) -> QWidget:
    kind = field.get("type") if isinstance(field, dict) else None
    if kind == "enum":
        widget = ResultEnumLabel(field)
        widget.setValue(value)
        return widget
    if kind == "boolean":
        widget = ResultBoolLabel()
        widget.setValue(value)
        return widget
    if kind in ("integer", "unsigned", "float"):
        widget = ResultIntLabel(compact=True)
        widget.setText(_result_text(value))
        return widget
    widget = QLabel(_result_text(value))
    widget.setTextInteractionFlags(Qt.TextSelectableByMouse)
    widget.setWordWrap(True)
    return widget


def _result_row_box() -> tuple[QWidget, QGridLayout]:
    box = QWidget()
    grid = QGridLayout(box)
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setHorizontalSpacing(8)
    grid.setVerticalSpacing(2)
    return box, grid


def _result_child_value(value: Any, child: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return value.get(str(child.get("name") or ""))
    return None


def _result_field_label(field: dict[str, Any]) -> str:
    return str(field.get("label") or field.get("name") or "")


def _build_result_value(field: dict[str, Any], value: Any,
                        depth: int = 0) -> QWidget:
    """Schema-driven widget for objects, arrays and tagged unions."""
    kind = field.get("type") if isinstance(field, dict) else None
    if depth > 8:
        return QLabel("…")
    if kind == "object":
        box, grid = _result_row_box()
        row = 0
        for child in field.get("fields", []) or []:
            if not isinstance(child, dict):
                continue
            grid.addWidget(QLabel(_result_field_label(child)), row, 0)
            grid.addWidget(
                _build_result_value(child, _result_child_value(value, child),
                                    depth + 1),
                row, 1,
            )
            row += 1
        return box
    if kind == "union":
        box, grid = _result_row_box()
        tag_field = field.get("tag") if isinstance(field.get("tag"), dict) else {}
        tag_name = str(tag_field.get("name") or "")
        tag_value = value.get(tag_name) if isinstance(value, dict) else None
        grid.addWidget(
            QLabel(_result_field_label(tag_field) or "Тип"), 0, 0
        )
        grid.addWidget(_result_scalar_widget(tag_field, tag_value), 0, 1)
        variant = None
        for item in field.get("variants", []) or []:
            if isinstance(item, dict) and item.get("value") == tag_value:
                variant = item
                break
        row = 1
        for child in (variant or {}).get("fields", []) or []:
            if not isinstance(child, dict):
                continue
            grid.addWidget(QLabel(_result_field_label(child)), row, 0)
            grid.addWidget(
                _build_result_value(child, _result_child_value(value, child),
                                    depth + 1),
                row, 1,
            )
            row += 1
        return box
    if kind == "array":
        box = QWidget()
        column = QVBoxLayout(box)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(4)
        element = field.get("items") if isinstance(field.get("items"), dict) else {}
        element_kind = element.get("type")
        for index, item in enumerate(value if isinstance(value, list) else []):
            entry = QWidget()
            entry_layout = QVBoxLayout(entry)
            entry_layout.setContentsMargins(0, 0, 0, 0)
            entry_layout.setSpacing(2)
            if element_kind in _STRUCTURED_RESULT_TYPES:
                caption = QLabel(f"#{index + 1}")
                font = caption.font()
                font.setBold(True)
                caption.setFont(font)
                entry_layout.addWidget(caption)
                entry_layout.addWidget(
                    _build_result_value(element, item, depth + 1)
                )
            else:
                row_widget = QWidget()
                row_layout = QHBoxLayout(row_widget)
                row_layout.setContentsMargins(0, 0, 0, 0)
                row_layout.addWidget(QLabel(f"#{index + 1}"))
                row_layout.addWidget(
                    _build_result_value(element, item, depth + 1), 1
                )
                entry_layout.addWidget(row_widget)
            column.addWidget(entry)
        return box
    return _result_scalar_widget(field if isinstance(field, dict) else {}, value)


class ResultStructuredWidget(ResultWidget):
    """Renders object/array/tagged-union results straight from the schema."""

    def __init__(self, field: dict[str, Any]) -> None:
        super().__init__(field)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(2)
        self._layout.addWidget(QLabel("—"))

    def setValue(self, value: Any) -> None:
        _clear_result_layout(self._layout)
        self._layout.addWidget(_build_result_value(self.field, value))


class CommandForm(QGroupBox):
    def __init__(self, descriptor: dict[str, Any], sender: Callable,
                 warning_sink: Callable[[str], None] | None = None):
        title = str(descriptor.get("title") or descriptor.get("cmd"))
        description = descriptor.get("description")
        super().__init__()
        self.setObjectName("commandForm")
        self.descriptor = descriptor
        self.command = str(descriptor["cmd"])
        self.sender = sender
        self.warning_sink = warning_sink
        self.param_widgets: dict[str, QWidget] = {}
        self.result_widgets: dict[str, QWidget] = {}
        self.in_flight = False
        self.last_poll = 0.0
        self.global_autopoll_enabled = False

        layout = QVBoxLayout(self)
        layout.setSpacing(COMMAND_FORM_SPACING)
        self.command_title_label = CollapseHeader(title, "commandTitle",
                                                  expanded=False)
        title_font = self.command_title_label.font()
        title_font.setBold(True)
        self.command_title_label.setFont(title_font)
        layout.addWidget(self.command_title_label)
        self.description_label: QLabel | None = None
        if isinstance(description, str) and description:
            self.description_label = QLabel(description)
            self.description_label.setObjectName("commandDescription")
            self.description_label.setWordWrap(True)
            layout.addWidget(self.description_label)

        # Hidden while the command is folded, so the card shows only its name.
        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(COMMAND_FORM_SPACING)
        layout.addWidget(self.body)
        self.body_layout.addSpacing(COMMAND_SECTION_SPACING)

        form = QFormLayout()
        for field in descriptor.get("params", []):
            widget = self._make_param_widget(field)
            self.param_widgets[str(field["name"])] = widget
            form.addRow(str(field.get("label") or field["name"]), widget)
        self.body_layout.addLayout(form)

        result_fields = descriptor.get("result", [])
        if result_fields:
            line = QFrame()
            line.setFrameShape(QFrame.HLine)
            self.body_layout.addWidget(line)
            results = QFormLayout()
            for field in result_fields:
                if field.get("type") == "console_string":
                    continue
                output = self._make_result_widget(field)
                self.result_widgets[str(field["name"])] = output
                result_label: str | QWidget = str(
                    field.get("label") or field["name"]
                )
                if isinstance(output, ResultWidget) and output.bold_label:
                    label_widget = QLabel(result_label)
                    label_font = label_widget.font()
                    label_font.setBold(True)
                    label_widget.setFont(label_font)
                    label_widget.setAlignment(Qt.AlignLeft | Qt.AlignTop)
                    result_label = label_widget
                results.addRow(result_label, output)
            self.body_layout.addLayout(results)

        self.body_layout.addSpacing(COMMAND_EXECUTE_TOP_SPACING)
        row = QHBoxLayout()
        self.execute_button = QPushButton("Выполнить")
        self.execute_button.clicked.connect(self.execute)
        row.addWidget(self.execute_button)
        if descriptor.get("autoupdate"):
            self.auto_enabled = QCheckBox("Авто")
            self.auto_enabled.setChecked(True)
            self.auto_enabled.toggled.connect(
                lambda _checked: self._update_execute_button()
            )
            row.addWidget(self.auto_enabled)
        else:
            self.auto_enabled = None
        row.addStretch()
        self.body_layout.addLayout(row)
        self.command_title_label.fold(self.body)

    def set_expanded(self, expanded: bool) -> None:
        self.command_title_label.set_expanded(expanded)

    def is_expanded(self) -> bool:
        return self.command_title_label.is_expanded()

    def set_global_autopoll(self, enabled: bool) -> None:
        self.global_autopoll_enabled = enabled
        self._update_execute_button()

    def _update_execute_button(self) -> None:
        auto_active = (
                self.global_autopoll_enabled
                and self.auto_enabled is not None
                and self.auto_enabled.isChecked()
        )
        set_button_pending(self.execute_button, self.in_flight)
        self.execute_button.setEnabled(not self.in_flight and not auto_active)

    def _warn_widget_fallback(self, registry: str, hint: str,
                              reason: str) -> None:
        if self.warning_sink is not None:
            self.warning_sink(
                f"Виджет {registry} '{hint}' для команды {self.command} "
                f"отклонён: {reason}. Используется стандартное отображение."
            )

    def _make_param_widget(self, field: dict[str, Any]) -> QWidget:
        hint = field.get("widget_hint")
        if isinstance(hint, str) and hint:
            widget_class = PARAM_WIDGETS.get(hint)
            if widget_class is None:
                self._warn_widget_fallback(
                    "PARAM_WIDGETS", hint, "класс не зарегистрирован"
                )
            elif (
                not isinstance(widget_class, type)
                or not issubclass(widget_class, ParameterWidget)
            ):
                self._warn_widget_fallback(
                    "PARAM_WIDGETS", hint, "класс не наследует ParameterWidget"
                )
            else:
                try:
                    widget_class.validate_descriptor(field)
                    return widget_class(field)
                except WidgetCompatibilityError as exc:
                    self._warn_widget_fallback("PARAM_WIDGETS", hint, str(exc))
        return self._make_default_param_widget(field)

    @staticmethod
    def _make_default_param_widget(field: dict[str, Any]) -> QWidget:
        field_type = field.get("type")
        default = field.get("default")
        constraints = field.get("constraints", {})
        if field_type in ("integer", "unsigned"):
            widget = QSpinBox()
            default_minimum = 0 if field_type == "unsigned" else -2147483648
            minimum = max(int(constraints.get("minimum", default_minimum)),
                          -2147483648)
            maximum = min(int(constraints.get("maximum", 2147483647)),
                          2147483647)
            widget.setRange(minimum, maximum)
            widget.setValue(int(default if default is not None
                                else widget.minimum()))
            return widget
        if field_type == "float":
            widget = QDoubleSpinBox()
            widget.setDecimals(6)
            widget.setRange(float(constraints.get("minimum", -1e9)),
                            float(constraints.get("maximum", 1e9)))
            widget.setSingleStep(float(constraints.get("step", 0.1)))
            widget.setValue(float(default if default is not None else widget.minimum()))
            return widget
        if field_type == "boolean":
            widget = QCheckBox()
            widget.setChecked(bool(default))
            return widget
        if field_type == "enum":
            widget = QComboBox()
            selected = 0
            for index, value in enumerate(constraints.get("values", [])):
                widget.addItem(str(value.get("title", value.get("value", ""))),
                               value.get("value"))
                if value.get("value") == default:
                    selected = index
            widget.setCurrentIndex(selected)
            return widget
        widget = QLineEdit(str(default or ""))
        widget.setMaxLength(int(constraints.get("maxLength", 32767)))
        return widget

    def _make_result_widget(self, field: dict[str, Any]) -> QWidget:
        hint = field.get("widget_hint")
        if isinstance(hint, str) and hint:
            widget_class = RESULT_WIDGETS.get(hint)
            if widget_class is None:
                self._warn_widget_fallback(
                    "RESULT_WIDGETS", hint, "класс не зарегистрирован"
                )
            elif (
                not isinstance(widget_class, type)
                or not issubclass(widget_class, ResultWidget)
            ):
                self._warn_widget_fallback(
                    "RESULT_WIDGETS", hint, "класс не наследует ResultWidget"
                )
            else:
                try:
                    widget_class.validate_descriptor(field)
                    return widget_class(field)
                except WidgetCompatibilityError as exc:
                    self._warn_widget_fallback("RESULT_WIDGETS", hint, str(exc))
        if field.get("type") in _STRUCTURED_RESULT_TYPES:
            return ResultStructuredWidget(field)
        return self._make_default_result_widget(field)

    @staticmethod
    def _make_default_result_widget(field: dict[str, Any]) -> QWidget:
        field_type = field.get("type")
        if field_type == "string":
            output = QLabel()
            output.setTextInteractionFlags(Qt.TextSelectableByMouse)
            output.setWordWrap(True)
            output.setAutoFillBackground(False)
            output.setStyleSheet(
                "QLabel { border: none; background: transparent; }"
            )
            return output
        if field_type in ("integer", "unsigned"):
            output = ResultIntLabel()
            output.setTextInteractionFlags(Qt.TextSelectableByMouse)
            output.setWordWrap(True)
            output.setAutoFillBackground(False)
            output.setStyleSheet(
                "QLabel { border: none; background: transparent; }"
            )
            return output
        if field_type == "boolean":
            return ResultBoolLabel()
        if field_type == "enum":
            return ResultEnumLabel(field)
        output = QLineEdit()
        output.setReadOnly(True)
        return output

    def parameters(self) -> dict[str, Any]:
        values: dict[str, Any] = {}
        fields = {str(field["name"]): field for field in self.descriptor.get("params", [])}
        for name, widget in self.param_widgets.items():
            field = fields[name]
            if isinstance(widget, ParameterWidget):
                values[name] = widget.value()
            elif isinstance(widget, QSpinBox):
                values[name] = widget.value()
            elif isinstance(widget, QDoubleSpinBox):
                values[name] = widget.value()
            elif isinstance(widget, QCheckBox):
                values[name] = widget.isChecked()
            elif isinstance(widget, QComboBox):
                values[name] = widget.currentData()
            elif isinstance(widget, QLineEdit):
                value = widget.text()
                constraints = field.get("constraints", {})
                minimum = int(constraints.get("minLength", 0))
                if len(value.encode("utf-8")) < minimum:
                    raise ValueError(f"Поле «{field.get('label', name)}» слишком короткое")
                values[name] = value
        return values

    def _submit(self, show_validation_error: bool) -> int | None:
        try:
            params = self.parameters()
        except ValueError as exc:
            if show_validation_error:
                QMessageBox.warning(self, "Параметры", str(exc))
            return None
        self._preserve_scroll_position()
        self.in_flight = True
        self._update_execute_button()
        transaction = self.sender(self.command, params, self.handle_response)
        if transaction is None:
            self.in_flight = False
            self._update_execute_button()
        return transaction

    def execute(self) -> None:
        self._submit(show_validation_error=True)

    def execute_for_autopoll(self) -> int | None:
        """Submit current form values without recurring validation dialogs."""

        return self._submit(show_validation_error=False)

    def handle_response(self, message: dict[str, Any]) -> None:
        self._preserve_scroll_position()
        self.in_flight = False
        self._update_execute_button()
        if not message.get("success"):
            return
        result = message.get("result", {})
        for name, widget in self.result_widgets.items():
            value = result.get(name, "")

            if isinstance(widget, ResultWidget):
                widget.setValue(value)
                continue

            if isinstance(widget, ResultBoolLabel):
                widget.setValue(value)
                continue

            if isinstance(widget, ResultEnumLabel):
                widget.setValue(value)
                continue

            widget.setText(
                json.dumps(value, ensure_ascii=False)
                if isinstance(value, (dict, list))
                else str(value)
            )

    def _preserve_scroll_position(self) -> None:
        parent = self.parentWidget()
        while parent is not None and not isinstance(parent, QScrollArea):
            parent = parent.parentWidget()
        if not isinstance(parent, QScrollArea):
            return
        scroll_bar = parent.verticalScrollBar()
        position = scroll_bar.value()
        QTimer.singleShot(
            0,
            lambda bar=scroll_bar, value=position: bar.setValue(
                min(value, bar.maximum())
            ),
        )

    def poll(self, requested_period_ms: int) -> None:
        if self.auto_enabled is None or not self.auto_enabled.isChecked() or self.in_flight:
            return
        auto = self.descriptor.get("autoupdate", {})
        period_ms = max(int(auto.get("min_period", requested_period_ms)), requested_period_ms)
        maximum = int(auto.get("max_period", period_ms))
        period_ms = min(period_ms, maximum)
        now = time.monotonic()
        if now - self.last_poll < period_ms / 1000.0:
            return
        self.last_poll = now
        self.execute()
