#!/usr/bin/env python3
"""Universal Krul JSON/BSON/CBOR control panel for compatible controllers."""

from __future__ import annotations

import json
import math
import sys
import time
from collections import defaultdict
from typing import Any, Callable

import serial
import serial.tools.list_ports
from krul_wire import (
    FORMAT_BSON,
    FORMAT_CBOR,
    FORMAT_JSON,
)
from PySide6.QtCore import QEvent, QObject, QRectF, QSize, QThread, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPalette, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractSpinBox,
    QApplication,
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
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QSplitter,
    QStyle,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from GrathPlot import GrathPlotWindow, discover_measurements
from starset_config import DeviceProfileStore

# Public re-exports keep existing import-Starset integrations stable.
from starset_theme import *
from starset_transport import *
from starset_widgets import *


class MainWindow(QMainWindow):
    response_received = Signal(str, dict)

    def __init__(
        self,
        worker_factory: Callable[..., SerialWorker] = SerialWorker,
        profile_store: DeviceProfileStore | None = None,
    ):
        super().__init__()
        self.setWindowTitle("StarSet - Control Panel")
        self.resize(1450, 850)
        self.worker: SerialWorker | None = None
        self._worker_factory = worker_factory
        self.next_id = 1
        self.pending: dict[int, tuple[str, Callable | None, float]] = {}
        self._heartbeat_transaction: int | None = None
        self._heartbeat_misses = 0
        self.descriptors: dict[str, dict[str, Any]] = {}
        self.forms: list[CommandForm] = []
        self.command_widgets: list[CommandWidget] = []
        self.command_widget_by_command: dict[str, CommandWidget] = {}
        self.io_panel: IOPanel | None = None
        self.io_panels: list[IOPanel] = []
        self.graph_window: GrathPlotWindow | None = None
        self.profile_store = profile_store or DeviceProfileStore()
        self._adc_profile_widgets: dict[
            str, list[SpecialAdcResultWidget | SpecialAdcGroupResultWidget]
        ] = defaultdict(list)
        self._applying_profile = False
        self.raw_history: list[str] = []
        self.raw_history_index = 0
        self._session_tx_bytes = 0
        self._session_rx_bytes = 0
        self._connected_at: float | None = None
        self._disconnected_elapsed = 0.0
        self._build_ui()
        application = QApplication.instance()
        if application is not None:
            application.installEventFilter(self)
        self._refresh_ports()

        self.timeout_timer = QTimer(self)
        self.timeout_timer.timeout.connect(self._expire_requests)
        self.timeout_timer.start(250)
        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self._poll)
        self.poll_timer.start(50)
        self.heartbeat_timer = QTimer(self)
        self.heartbeat_timer.setInterval(HEARTBEAT_INTERVAL_MS)
        self.heartbeat_timer.timeout.connect(self._heartbeat_tick)
        self.connection_stats_timer = QTimer(self)
        self.connection_stats_timer.setInterval(1000)
        self.connection_stats_timer.timeout.connect(self._update_connection_stats)
        self.profile_save_timer = QTimer(self)
        self.profile_save_timer.setSingleShot(True)
        self.profile_save_timer.setInterval(350)
        self.profile_save_timer.timeout.connect(self.profile_store.flush)

    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(6, 6, 6, 6)

        connection = QHBoxLayout()
        connection.addWidget(QLabel("Порт:"))
        self.port_combo = QComboBox()
        self.port_combo.setEditable(True)
        self.port_combo.setMinimumWidth(260)
        self.port_combo.lineEdit().setPlaceholderText(
            "COM3 или socket://127.0.0.1:7000"
        )
        connection.addWidget(self.port_combo)
        self.refresh_ports_button = QToolButton()
        self.refresh_ports_button.setObjectName("utilityButton")
        self.refresh_ports_button.setIcon(
            self.style().standardIcon(QStyle.SP_BrowserReload)
        )
        self.refresh_ports_button.setToolTip("Обновить список портов")
        self.refresh_ports_button.clicked.connect(self._refresh_ports)
        connection.addWidget(self.refresh_ports_button)
        connection.addWidget(QLabel("Скорость:"))
        self.baud_combo = QComboBox()
        self.baud_combo.setEditable(True)
        for baud in (9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600):
            self.baud_combo.addItem(str(baud))
        self.baud_combo.setCurrentText(str(DEFAULT_BAUDRATE))
        connection.addWidget(self.baud_combo)
        connection.addWidget(QLabel("Codec:"))
        self.format_combo = QComboBox()
        self.format_combo.addItem("JSON", FORMAT_JSON)
        self.format_combo.addItem("BSON", FORMAT_BSON)
        self.format_combo.addItem("CBOR", FORMAT_CBOR)
        connection.addWidget(self.format_combo)
        self.connect_button = QPushButton("Подключить")
        self.connect_button.clicked.connect(self._toggle_connection)
        connection.addWidget(self.connect_button)
        self.rebuild_button = QPushButton("Переформировать GUI")
        self.rebuild_button.setEnabled(False)
        self.rebuild_button.clicked.connect(self._discover)
        connection.addWidget(self.rebuild_button)
        self.graph_button = QPushButton("Графики")
        self.graph_button.setEnabled(False)
        self.graph_button.clicked.connect(self._show_graph_plot)
        connection.addWidget(self.graph_button)
        self.theme_button = QPushButton()
        self.theme_button.clicked.connect(self._toggle_theme)
        connection.addWidget(self.theme_button)
        self._update_theme_button()
        self.device_label = QLabel("МК: -")
        connection.addWidget(self.device_label, 1)
        root.addLayout(connection)

        splitter = QSplitter(Qt.Horizontal)
        self.tabs = QTabWidget()
        self.tabs.tabBar().setMouseTracking(True)
        splitter.addWidget(self.tabs)
        splitter.addWidget(self._build_terminal())
        splitter.setSizes([900, 550])
        root.addWidget(splitter, 1)
        self.setCentralWidget(central)
        self._reset_tabs()
        self._apply_button_cursors(central)

    def _build_terminal(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        header = QHBoxLayout()
        title = QLabel("Терминал")
        title.setStyleSheet("font-weight:bold")
        header.addWidget(title)
        header.addStretch()
        self.clear_terminal_button = QToolButton()
        self.clear_terminal_button.setObjectName("utilityButton")
        self.clear_terminal_button.setIcon(
            self.style().standardIcon(QStyle.SP_DialogResetButton)
        )
        self.clear_terminal_button.setToolTip("Очистить терминал")
        self.clear_terminal_button.clicked.connect(lambda: self.terminal.clear())
        header.addWidget(self.clear_terminal_button)
        self.developer_check = QCheckBox("Режим разработчика")
        self.developer_check.toggled.connect(self._toggle_developer)
        header.addWidget(self.developer_check)
        layout.addLayout(header)

        self.developer_stats_label = QLabel()
        self.developer_stats_label.setObjectName("developerStats")
        self.developer_stats_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.developer_stats_label.hide()
        layout.addWidget(self.developer_stats_label)
        self._update_connection_stats()

        self.terminal = QPlainTextEdit()
        self.terminal.setObjectName("terminal")
        self.terminal.setReadOnly(True)
        self.terminal.setMaximumBlockCount(5000)
        layout.addWidget(self.terminal, 1)

        self.raw_row = QWidget()
        raw_layout = QHBoxLayout(self.raw_row)
        raw_layout.setContentsMargins(0, 0, 0, 0)
        self.raw_edit = QLineEdit()
        self.raw_edit.setPlaceholderText('{"cmd":"WHOAMI","id":1}')
        self.raw_edit.installEventFilter(self)
        self.raw_edit.returnPressed.connect(self._send_raw)
        raw_layout.addWidget(self.raw_edit, 1)
        send = QPushButton("Отправить")
        send.clicked.connect(self._send_raw)
        raw_layout.addWidget(send)
        self.raw_row.hide()
        layout.addWidget(self.raw_row)

        controls = QHBoxLayout()
        self.autoscroll_check = QCheckBox("Автопрокрутка")
        self.autoscroll_check.setChecked(True)
        controls.addWidget(self.autoscroll_check)
        self.autopoll_check = QCheckBox("Автоопрос")
        self.autopoll_check.toggled.connect(self._autopoll_toggled)
        controls.addWidget(self.autopoll_check)
        self.heartbeat_check = QCheckBox("PING")
        self.heartbeat_check.setToolTip(
            "Отправлять PING раз в секунду и контролировать ответы"
        )
        self.heartbeat_check.setChecked(True)
        self.heartbeat_check.toggled.connect(self._heartbeat_toggled)
        controls.addWidget(self.heartbeat_check)
        self.show_nogui_check = QCheckBox("Показывать NoGUI")
        self.show_nogui_check.setToolTip(
            "Принудительно отображать скрытые служебные команды"
        )
        self.show_nogui_check.toggled.connect(self._nogui_visibility_changed)
        self.show_nogui_check.hide()
        controls.addWidget(self.show_nogui_check)
        self.poll_period_combo = QComboBox()
        for period in (100, 250, 500, 1000, 2000, 5000):
            self.poll_period_combo.addItem(str(period), period)
        self.poll_period_combo.setCurrentText("500")
        controls.addWidget(self.poll_period_combo)
        controls.addWidget(QLabel("мс"))
        controls.addStretch()
        layout.addLayout(controls)
        return panel

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if isinstance(watched, QAbstractButton) and event.type() == QEvent.Polish:
            watched.setCursor(Qt.PointingHandCursor)
        if (
            isinstance(watched, (QAbstractSpinBox, QComboBox, QSlider))
            and event.type() == QEvent.Wheel
        ):
            parent = watched.parentWidget()
            while parent is not None and not isinstance(parent, QScrollArea):
                parent = parent.parentWidget()
            if isinstance(parent, QScrollArea):
                scroll_bar = parent.verticalScrollBar()
                pixel_delta = event.pixelDelta().y()
                if pixel_delta:
                    amount = pixel_delta
                else:
                    steps = event.angleDelta().y() / 120.0
                    amount = round(
                        steps
                        * QApplication.wheelScrollLines()
                        * scroll_bar.singleStep()
                    )
                if event.inverted():
                    amount = -amount
                scroll_bar.setValue(scroll_bar.value() - amount)
            # Колесо навигирует по странице и никогда не редактирует поле.
            return True
        if watched is self.tabs.tabBar():
            if event.type() == QEvent.MouseMove:
                tab_bar = self.tabs.tabBar()
                index = tab_bar.tabAt(event.position().toPoint())
                is_inactive = (
                    index >= 0
                    and index != tab_bar.currentIndex()
                    and tab_bar.isTabEnabled(index)
                )
                tab_bar.setCursor(
                    Qt.PointingHandCursor if is_inactive else Qt.ArrowCursor
                )
            elif event.type() == QEvent.Leave:
                self.tabs.tabBar().setCursor(Qt.ArrowCursor)
            return False
        if watched is self.raw_edit and event.type() == QEvent.KeyPress:
            if event.key() == Qt.Key_Up and self.raw_history:
                self.raw_history_index = max(0, self.raw_history_index - 1)
                self.raw_edit.setText(self.raw_history[self.raw_history_index])
                return True
            if event.key() == Qt.Key_Down and self.raw_history:
                self.raw_history_index = min(len(self.raw_history), self.raw_history_index + 1)
                self.raw_edit.setText(
                    "" if self.raw_history_index == len(self.raw_history)
                    else self.raw_history[self.raw_history_index]
                )
                return True
        return super().eventFilter(watched, event)

    @staticmethod
    def _apply_button_cursors(root: QWidget) -> None:
        for button in root.findChildren(QAbstractButton):
            button.setCursor(Qt.PointingHandCursor)

    def _update_theme_button(self) -> None:
        light_is_next = current_theme() == "dark"
        self.theme_button.setText(
            "☀ Светлая тема" if light_is_next else "☾ Тёмная тема"
        )
        self.theme_button.setToolTip(
            "Переключить на светлую тему"
            if light_is_next
            else "Переключить на тёмную тему"
        )

    def _toggle_theme(self) -> None:
        set_theme("light" if current_theme() == "dark" else "dark")
        application = QApplication.instance()
        if application is not None:
            application.setPalette(application_palette())
            application.setStyleSheet(application_stylesheet())
        self._update_theme_button()
        self._update_connect_button_style()
        for panel in self.io_panels:
            for card in panel.cards.values():
                card.set_state(card.state)
        if self.graph_window is not None:
            self.graph_window.update()
        self.update()

    def _refresh_ports(self) -> None:
        selected = self.port_combo.currentText()
        ports = sorted((item.device for item in serial.tools.list_ports.comports()),
                       key=str.casefold)
        self.port_combo.clear()
        self.port_combo.addItems(ports)
        if selected and selected not in ports:
            self.port_combo.addItem(selected)
        if selected:
            self.port_combo.setCurrentText(selected)

    def _toggle_connection(self) -> None:
        if self.worker is not None:
            self._disconnect()
            return
        port = self.port_combo.currentText().strip()
        try:
            baudrate = int(self.baud_combo.currentText())
            if not port or baudrate <= 0:
                raise ValueError
        except ValueError:
            QMessageBox.warning(self, "Подключение", "Выберите порт и корректную скорость.")
            return
        self.worker = self._worker_factory(port, baudrate, self)
        if hasattr(self.worker, "set_wire_format"):
            self.worker.set_wire_format(self.format_combo.currentData())
        self.worker.opened.connect(self._serial_opened)
        self.worker.open_failed.connect(self._open_failed)
        self.worker.transport_error.connect(self._transport_failed)
        self.worker.line_received.connect(self._receive_line)
        self.worker.line_sent.connect(self._sent_line)
        if hasattr(self.worker, "bytes_received"):
            self.worker.bytes_received.connect(self._record_received_bytes)
        if hasattr(self.worker, "bytes_sent"):
            self.worker.bytes_sent.connect(self._record_sent_bytes)
        if hasattr(self.worker, "frame_error"):
            self.worker.frame_error.connect(
                lambda error: self._append_terminal(
                    f"Krul transport frame: {error}", "warning"))
        self.connect_button.setText("Подключение…")
        self.connect_button.setEnabled(False)
        self.connect_button.setStyleSheet("")
        self.format_combo.setEnabled(False)
        self._session_tx_bytes = 0
        self._session_rx_bytes = 0
        self._connected_at = None
        self._disconnected_elapsed = 0.0
        self._update_connection_stats()
        self.worker.start()

    def _serial_opened(self) -> None:
        self._connected_at = time.monotonic()
        self._disconnected_elapsed = 0.0
        self.connection_stats_timer.start()
        self._update_connection_stats()
        self.connect_button.setText("Отключить")
        self.connect_button.setEnabled(True)
        self._update_connect_button_style()
        self.rebuild_button.setEnabled(True)
        self._append_terminal("Последовательный порт открыт", "info")
        QTimer.singleShot(CONNECT_SETTLE_MS, self._discover)

    def _update_connect_button_style(self) -> None:
        if self.connect_button.text() != "Отключить":
            self.connect_button.setStyleSheet("")
            return
        self.connect_button.setStyleSheet(f"""
            QPushButton {{
                background-color: {theme_color("danger")};
                color: {theme_color("accent_text")};
            }}
            QPushButton:hover {{
                background-color: {theme_color("danger_hover")};
            }}
            QPushButton:pressed {{
                background-color: {theme_color("danger_pressed")};
            }}
        """)

    def _open_failed(self, error: str) -> None:
        QMessageBox.critical(self, "Ошибка порта", error)
        self._disconnect(from_worker=True)

    def _transport_failed(self, error: str) -> None:
        QMessageBox.critical(self, "Ошибка связи", error)
        self._disconnect(from_worker=True)

    def _disconnect(self, from_worker: bool = False) -> None:
        if self._connected_at is not None:
            self._disconnected_elapsed = max(
                0.0, time.monotonic() - self._connected_at)
        self._connected_at = None
        self.connection_stats_timer.stop()
        self._update_connection_stats()
        self.heartbeat_timer.stop()
        self._heartbeat_transaction = None
        self._heartbeat_misses = 0
        worker = self.worker
        self.worker = None
        if worker is not None:
            worker.stop()
            if not from_worker:
                worker.wait(1000)
            worker.deleteLater()
        self.pending.clear()
        self.descriptors.clear()
        self.forms.clear()
        self.command_widgets.clear()
        self.command_widget_by_command.clear()
        self.io_panel = None
        self.io_panels.clear()
        self.device_label.setText("МК: -")
        self.connect_button.setText("Подключить")
        self.connect_button.setEnabled(True)
        self.connect_button.setStyleSheet("")
        self.rebuild_button.setEnabled(False)
        self.graph_button.setEnabled(False)
        self.format_combo.setEnabled(True)
        if self.graph_window is not None:
            self.graph_window.set_descriptors({})
        self._reset_tabs()

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.graph_window is not None:
            self.graph_window.close()
        self.profile_save_timer.stop()
        self.profile_store.flush()
        self._disconnect()
        event.accept()

    def _allocate_id(self) -> int:
        transaction = self.next_id
        self.next_id = 1 if transaction == 0xFFFFFFFF else transaction + 1
        return transaction

    def send_request(self, command: str, params: dict[str, Any] | None = None,
                     callback: Callable | None = None,
                     timeout: float | None = None) -> int | None:
        if self.worker is None:
            return None
        if timeout is None:
            declared_ms = int(self.descriptors.get(command, {}).get("timeout_ms", 0))
            timeout = max(RESPONSE_TIMEOUT_S, declared_ms / 1000.0 + 1.0)
        transaction = self._allocate_id()
        request: dict[str, Any] = {"cmd": command, "id": transaction}
        if params is not None:
            request["params"] = params
        line = json.dumps(request, ensure_ascii=False, separators=(",", ":"))
        self.pending[transaction] = (command, callback, time.monotonic() + timeout)
        self.worker.send_line(line)
        return transaction

    def _sent_line(self, line: str) -> None:
        if self.developer_check.isChecked():
            self._append_terminal(f"TX > {line}", "debug")

    def _receive_line(self, line: str) -> None:
        if self.developer_check.isChecked():
            self._append_terminal(f"RX < {line}", "debug")
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            # Malformed payloads are surfaced as warnings in every mode so
            # developer mode never hides a protocol formatting problem.
            self._append_terminal(f"Некорректный JSON от МК: {line}", "warning")
            return
        if not isinstance(message, dict):
            self._append_terminal("Корневое значение ответа не является object", "warning")
            return
        if "event" in message:
            if message.get("event") == "log":
                data = message.get("data", {})
                if isinstance(data, dict):
                    self._append_terminal(
                        str(data.get("message", "")),
                        str(data.get("severity", "info")),
                    )
                    return
            self._append_terminal(
                f"Событие {message.get('event')}: "
                f"{json.dumps(message.get('data', {}), ensure_ascii=False)}",
                "warning",
            )
            return
        transaction = message.get("id")
        if not isinstance(transaction, int):
            self._append_terminal("Ответ не содержит целочисленный id", "warning")
            return
        pending = self.pending.pop(transaction, None)
        if pending is None:
            self._append_terminal(
                f"Несопоставленный ответ id={transaction}", "warning"
            )
            return
        command = pending[0]
        if not message.get("success", False):
            error = message.get("error", {})
            self._append_terminal(
                f"Ошибка {error.get('code', '?')}: {error.get('message', 'Без описания')}",
                "error",
            )
        else:
            descriptor = self.descriptors.get(command or "", {})
            result = message.get("result", {})
            for field in descriptor.get("result", []):
                if field.get("type") == "console_string" and field.get("name") in result:
                    constraints = field.get("constraints", {})
                    self._append_terminal(str(result[field["name"]]),
                                          str(constraints.get("severity", "info")))
        if command is not None:
            self._mark_command_activity(command)
            self.response_received.emit(command, message)
            self._deliver_command_response(command, message, pending[1])

    def _deliver_command_response(
            self, command: str, message: dict[str, Any],
            callback: Callable | None) -> None:
        """Deliver one correlated response to every visible command copy."""

        callback_form: CommandForm | None = None
        if (
            callback is not None
            and isinstance(getattr(callback, "__self__", None), CommandForm)
            and getattr(callback, "__func__", None) is CommandForm.handle_response
        ):
            callback_form = callback.__self__

        if callback is not None:
            callback(message)
        for form in self.forms:
            if form.command == command and form is not callback_form:
                form.handle_response(message)

    def _expire_requests(self) -> None:
        now = time.monotonic()
        expired = [transaction for transaction, (_, _, deadline) in self.pending.items()
                   if deadline <= now]
        for transaction in expired:
            command, callback, _ = self.pending.pop(transaction)
            message = {
                "id": transaction,
                "success": False,
                "error": {
                    "code": CLIENT_ERROR_TIMEOUT,
                    "message": f"Тайм-аут команды {command}",
                    "source": "client",
                },
            }
            self._append_terminal(message["error"]["message"], "error")
            self._mark_command_activity(command)
            self.response_received.emit(command, message)
            self._deliver_command_response(command, message, callback)

    def _start_heartbeat(self) -> None:
        self._heartbeat_transaction = None
        self._heartbeat_misses = 0
        if self.heartbeat_check.isChecked():
            self.heartbeat_timer.start()
        else:
            self.heartbeat_timer.stop()

    def _heartbeat_toggled(self, enabled: bool) -> None:
        if not enabled:
            self.heartbeat_timer.stop()
            if self._heartbeat_transaction is not None:
                self.pending.pop(self._heartbeat_transaction, None)
            self._heartbeat_transaction = None
            self._heartbeat_misses = 0
            return
        if self.worker is not None and self._connected_at is not None:
            self._start_heartbeat()

    def _heartbeat_tick(self) -> None:
        if not self.heartbeat_check.isChecked():
            self._heartbeat_toggled(False)
            return
        if self.worker is None:
            self.heartbeat_timer.stop()
            return
        if self._heartbeat_transaction is not None:
            self.pending.pop(self._heartbeat_transaction, None)
            self._heartbeat_transaction = None
            self._heartbeat_misses += 1
            self._append_terminal(
                f"Связь потеряна, попытка переподключиться ({self._heartbeat_misses}/"
                f"{HEARTBEAT_MISS_LIMIT})",
                "warning",
            )
            if self._heartbeat_misses >= HEARTBEAT_MISS_LIMIT:
                self._heartbeat_connection_lost()
                return

        self._heartbeat_transaction = self.send_request(
            "PING",
            callback=self._heartbeat_received,
            timeout=2.0 * HEARTBEAT_INTERVAL_MS / 1000.0,
        )

    def _heartbeat_received(self, message: dict[str, Any]) -> None:
        if message.get("id") != self._heartbeat_transaction:
            return
        self._heartbeat_transaction = None
        if message.get("success"):
            self._heartbeat_misses = 0
            return

        self._heartbeat_misses += 1
        if self._heartbeat_misses >= HEARTBEAT_MISS_LIMIT:
            self._heartbeat_connection_lost()

    def _heartbeat_connection_lost(self) -> None:
        self._append_terminal(
            "Связь потеряна: три запроса подряд остались без ответа",
            "error",
        )
        self._disconnect()
        self.device_label.setText("МК: связь потеряна")

    def _discover(self) -> None:
        if self.worker is None:
            return
        self.heartbeat_timer.stop()
        self._heartbeat_transaction = None
        self._heartbeat_misses = 0
        self.pending.clear()
        self.descriptors.clear()
        self.forms.clear()
        self.command_widgets.clear()
        self.command_widget_by_command.clear()
        self.io_panel = None
        self.io_panels.clear()
        self.graph_button.setEnabled(False)
        if self.graph_window is not None:
            self.graph_window.set_descriptors({})
        self._reset_tabs()
        self.device_label.setText("МК: опрос…")
        self.send_request("WHOAMI", callback=self._whoami_received,
                          timeout=DISCOVERY_TIMEOUT_S)

    def _whoami_received(self, message: dict[str, Any]) -> None:
        if not message.get("success"):
            self.device_label.setText("МК: ошибка WHOAMI")
            return
        result = message.get("result", {})
        if not isinstance(result, dict):
            self.device_label.setText("МК: некорректный WHOAMI")
            return
        version = result.get("protocol_version")
        if version != PROTOCOL_VERSION:
            QMessageBox.critical(
                self, "Версия протокола",
                f"GUI поддерживает версию {PROTOCOL_VERSION}, МК сообщил {version}.",
            )
            return
        self.profile_store.select_device(result)
        self.device_label.setText(
            f"МК: {result.get('device_name', '?')} · FW {result.get('firmware', '?')} · JSON v{version}"
        )
        self._start_heartbeat()
        self.send_request("CMD_LIST", callback=self._command_list_received,
                          timeout=DISCOVERY_TIMEOUT_S)

    def _command_list_received(self, message: dict[str, Any]) -> None:
        if not message.get("success"):
            return
        names = [str(name) for name in message.get("result", {}).get("cmd_name", [])]
        if not names:
            self._append_terminal("CMD_LIST вернул пустой список", "error")
            return
        waiting = set(names)

        def described(response: dict[str, Any], expected: str) -> None:
            waiting.discard(expected)
            if response.get("success"):
                descriptor = response.get("result", {})
                if isinstance(descriptor, dict) and descriptor.get("cmd"):
                    self.descriptors[str(descriptor["cmd"])] = descriptor
            if not waiting:
                self._build_dynamic_tabs()

        for name in names:
            self.send_request(
                "DESCRIBE", {"name": name},
                lambda response, expected=name: described(response, expected),
                timeout=DISCOVERY_TIMEOUT_S,
            )

    def _reset_tabs(self) -> None:
        while self.tabs.count():
            widget = self.tabs.widget(0)
            self.tabs.removeTab(0)
            widget.deleteLater()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        welcome = QWidget()
        welcome.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        layout = QVBoxLayout(welcome)
        rabbit = QLabel("🌠🌌StarSet🌠")
        rabbit.setObjectName("welcomeTitle")
        rabbit.setAlignment(Qt.AlignCenter)
        rabbit.setStyleSheet(
            """font-size:28px; letter-spacing: 2px;
                margin-top: 10px; font-weight: bold;"""
        )
        layout.addWidget(rabbit)
        self.welcome_hint = QLabel(
            "Подключитесь к контроллеру - остальные вкладки будут построены "
            "по его DESCRIBE-метаданным."
        )
        layout.addWidget(self.welcome_hint, 0, Qt.AlignCenter)
        self.default_commands_layout = QVBoxLayout()
        layout.addLayout(self.default_commands_layout)
        layout.addStretch()
        scroll.setWidget(welcome)
        self.tabs.addTab(scroll, "Главная")

    def _populate_command_groups(
            self, body_layout: QVBoxLayout, descriptors: list[dict[str, Any]]
    ) -> None:
        page_grid = ResponsiveCardGrid(
            COMMAND_GRID_MIN_GROUP_WIDTH,
            COMMAND_GRID_MAX_COLUMNS,
        )
        page_grid.setObjectName("commandGroupsGrid")
        page_grid.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        body_layout.addWidget(page_grid)
        groups: dict[str, tuple[QGroupBox, ResponsiveCardGrid]] = {}

        def add_group_card(group_name: str, card: QWidget) -> None:
            box, command_grid = groups[group_name]
            command_grid.add_card(card)
            preferred_span = min(
                COMMAND_GRID_MAX_COLUMNS,
                max(1, math.ceil(math.sqrt(len(command_grid.cards)))),
            )
            box.setProperty("gridPreferredSpan", preferred_span)
            if card.property("gridSpanMode") == "full":
                box.setProperty("gridSpanMode", "full")
            page_grid.request_relayout()

        for descriptor in sorted(
                descriptors,
                key=lambda item: (int(item.get("order", 0)), str(item.get("cmd", ""))),
        ):
            group_name = str(descriptor.get("group") or "Команды")
            if group_name not in groups:
                box = QGroupBox()
                box.setObjectName("commandGroup")

                box_layout = QVBoxLayout(box)
                box_layout.setSpacing(COMMAND_GROUP_SPACING)
                group_header = CollapseHeader(
                    group_name, "commandGroupHeader", expanded=True,
                    size=COLLAPSE_HEADER_GROUP_SCALE,
                )
                box_layout.addWidget(group_header)
                command_grid = ResponsiveCardGrid(
                    COMMAND_GRID_MIN_CARD_WIDTH,
                    COMMAND_GRID_MAX_COLUMNS,
                )
                command_grid.setObjectName("commandCardsGrid")
                box_layout.addWidget(command_grid)
                group_header.fold(command_grid)
                groups[group_name] = (box, command_grid)
                page_grid.add_card(box)
            command = str(descriptor.get("cmd", ""))
            command_widget = self.command_widget_by_command.get(command)
            custom = (
                command_widget.create_widget(descriptor)
                if command_widget is not None
                else None
            )
            if custom is not None:
                self._apply_button_cursors(custom)
                add_group_card(group_name, custom)
                continue

            form = CommandForm(
                descriptor,
                self.send_request,
                lambda message: self._append_terminal(message, "warning"),
            )
            form.set_global_autopoll(self.autopoll_check.isChecked())
            self._apply_button_cursors(form)
            add_group_card(group_name, form)
            if form.auto_enabled is not None:
                peer = next(
                    (
                        existing for existing in self.forms
                        if existing.command == form.command
                        and existing.auto_enabled is not None
                    ),
                    None,
                )
                if peer is not None:
                    form.auto_enabled.setChecked(peer.auto_enabled.isChecked())
                form.auto_enabled.toggled.connect(
                    lambda enabled, source=form: self._sync_command_auto_checks(
                        source, enabled
                    )
                )
            self.forms.append(form)

    def _sync_command_auto_checks(
            self, source: CommandForm, enabled: bool) -> None:
        for form in self.forms:
            if (
                form is not source
                and form.command == source.command
                and form.auto_enabled is not None
            ):
                form.auto_enabled.setChecked(enabled)

    def _build_dynamic_tabs(self) -> None:
        self.welcome_hint.setText(
            "Все доступные команды и панели собраны ниже; тематические "
            "вкладки остаются доступны отдельно."
        )
        hidden_commands = self._prepare_command_widgets()
        normal = [
            descriptor
            for descriptor in self.descriptors.values()
            if not descriptor.get("builtin")
            and (not descriptor.get("nogui")
                 or self.show_nogui_check.isChecked())
            and str(descriptor.get("cmd")) not in hidden_commands
        ]

        overview: list[dict[str, Any]] = []
        for descriptor in normal:
            item = dict(descriptor)
            tab_name = str(item.get("tab") or "Кролик вампир")
            group_name = str(item.get("group") or "Команды")
            if tab_name != "Кролик вампир":
                item["group"] = f"{tab_name} / {group_name}"
            overview.append(item)
        self._populate_command_groups(self.default_commands_layout, overview)

        by_tab: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for descriptor in normal:
            by_tab[str(descriptor.get("tab") or "Кролик вампир")].append(descriptor)
        by_tab.pop("Кролик вампир", None)

        for tab_name, descriptors in by_tab.items():
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            body = QWidget()
            body.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            body_layout = QVBoxLayout(body)
            self._populate_command_groups(body_layout, descriptors)
            body_layout.addStretch()
            scroll.setWidget(body)
            self.tabs.addTab(scroll, tab_name)

        for widget in self.command_widgets:
            widget.build()
        self._bind_adc_profile_widgets()
        self._sync_graph_plot()

    def _bind_adc_profile_widgets(self) -> None:
        self._adc_profile_widgets.clear()
        stored = self.profile_store.section("widgets")
        for form in self.forms:
            for field_name, widget in form.result_widgets.items():
                if not isinstance(
                    widget,
                    (SpecialAdcResultWidget, SpecialAdcGroupResultWidget),
                ):
                    continue
                key = f"{form.command}:{field_name}"
                self._adc_profile_widgets[key].append(widget)

        self._applying_profile = True
        try:
            for key, widgets in self._adc_profile_widgets.items():
                config = stored.get(key)
                if isinstance(config, dict):
                    for widget in widgets:
                        widget.apply_profile_configuration(config)
        finally:
            self._applying_profile = False

        for key, widgets in self._adc_profile_widgets.items():
            for widget in widgets:
                for control in widget.profile_controls().values():
                    control.valueChanged.connect(
                        lambda _value, profile_key=key, source=widget:
                        self._adc_profile_changed(profile_key, source)
                    )

    def _adc_profile_changed(
        self,
        key: str,
        source: SpecialAdcResultWidget | SpecialAdcGroupResultWidget,
    ) -> None:
        if self._applying_profile:
            return
        config = source.profile_configuration()
        self._applying_profile = True
        try:
            for widget in self._adc_profile_widgets.get(key, []):
                if widget is not source:
                    widget.apply_profile_configuration(config)
        finally:
            self._applying_profile = False
        stored = self.profile_store.section("widgets")
        stored[key] = config
        self.profile_store.set_section("widgets", stored)
        self._schedule_profile_save()

    def _schedule_profile_save(self) -> None:
        if self.profile_store.active_key is not None:
            self.profile_save_timer.start()

    def _sync_graph_plot(self) -> None:
        if self.graph_window is not None:
            self.graph_window.set_descriptors(self.descriptors)
        self._sync_graph_gpio_inputs()
        if self.graph_window is not None:
            self.graph_window.apply_profile_configuration(
                self.profile_store.section("graphs")
            )

    @staticmethod
    def _gpio_source_id(panel: IOPanel) -> str:
        target = panel.target if panel.target is not None else "local"
        return f"{panel.get_command}:{target}"

    def _gpio_definitions(self) -> list[dict[str, Any]]:
        definitions: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for panel in self.io_panels:
            source_id = self._gpio_source_id(panel)
            descriptor = self.descriptors.get(panel.get_command, {})
            source_title = str(
                panel.target_label
                or descriptor.get("title")
                or panel.get_command
            )
            for card in panel.cards.values():
                identity = (source_id, card.name)
                if identity in seen:
                    continue
                seen.add(identity)
                definitions.append({
                    "key": f"GPIO:{source_id}:{card.name}",
                    "source_id": source_id,
                    "pin_name": card.name,
                    "tab": str(descriptor.get("tab") or "GPIO"),
                    "group": str(
                        descriptor.get("group")
                        or ("Входы GPIO" if card.pin_type == "IN" else "Выходы GPIO")
                    ),
                    "source_title": source_title,
                    "label": card.name,
                    "state": card.state,
                })
        return definitions

    def _sync_graph_gpio_inputs(self) -> None:
        definitions = self._gpio_definitions()
        available = bool(definitions or discover_measurements(self.descriptors))
        self.graph_button.setEnabled(available)
        if self.graph_window is not None:
            self.graph_window.register_gpio_inputs(definitions)

    def _request_graph_gpio(
            self, requests: dict[str, set[str]]) -> None:
        for panel in self.io_panels:
            source_id = self._gpio_source_id(panel)
            names = requests.get(source_id, set())
            if not names:
                continue
            if self.autopoll_check.isChecked():
                # Ordinary GPIO auto-poll already supplies every input. Only
                # selected outputs need an additional read in this mode.
                names = {
                    name for name in names
                    if name in panel.cards
                    and panel.cards[name].pin_type == "OUT"
                }
            panel.poll_pins(names)

    def _gpio_panel_updated(
            self, source: IOPanel, message: dict[str, Any]
    ) -> None:
        if self.graph_window is None or not message.get("success"):
            return
        result = message.get("result", {})
        if not isinstance(result, dict):
            return
        pins = result.get("pins", [])
        if not pins and result.get("name"):
            pins = [result]
        states: dict[str, int] = {}
        for pin in pins if isinstance(pins, list) else []:
            if not isinstance(pin, dict) or "state" not in pin:
                continue
            name = str(pin.get("name", ""))
            card = source.cards.get(name)
            if card is None:
                continue
            try:
                states[name] = int(pin["state"])
            except (TypeError, ValueError):
                continue
        if states:
            self.graph_window.ingest_gpio_values(
                self._gpio_source_id(source), states
            )

    def _request_graph_sample(
            self, command: str, params: dict[str, Any] | None
    ) -> int | None:
        if self.worker is None:
            return None
        if self._command_is_pending(command):
            return None
        if self.autopoll_check.isChecked():
            for form in self.forms:
                if form.command != command:
                    continue
                if form.auto_enabled is not None and form.auto_enabled.isChecked():
                    return form.execute_for_autopoll()
                break
        return self.send_request(command, params)

    def _command_is_pending(self, command: str) -> bool:
        return any(
            pending_command == command
            for pending_command, _, _ in self.pending.values()
        )

    def _mark_command_activity(self, command: str) -> None:
        now = time.monotonic()
        for form in self.forms:
            if form.command == command:
                form.last_poll = now

    def _show_graph_plot(self) -> None:
        if self.graph_window is None:
            self.graph_window = GrathPlotWindow(
                self.descriptors,
                requester=self._request_graph_sample,
                gpio_requester=self._request_graph_gpio,
                parent=self,
            )
            self.response_received.connect(self.graph_window.ingest_response)
            self.graph_window.configuration_changed.connect(
                self._graph_profile_changed
            )
        elif self.graph_window.descriptors != self.descriptors:
            self.graph_window.set_descriptors(self.descriptors)
        self._sync_graph_gpio_inputs()
        self.graph_window.apply_profile_configuration(
            self.profile_store.section("graphs")
        )
        self.graph_window.show()
        self.graph_window.raise_()
        self.graph_window.activateWindow()

    def _graph_profile_changed(self, config: dict[str, Any]) -> None:
        self.profile_store.set_section("graphs", config)
        self._schedule_profile_save()

    def _prepare_command_widgets(self) -> set[str]:
        self.command_widgets.clear()
        self.command_widget_by_command.clear()
        grouped: dict[tuple[str, str | None], list[dict[str, Any]]] = defaultdict(list)
        for descriptor in self.descriptors.values():
            hint = descriptor.get("widget_hint")
            if (
                not descriptor.get("builtin")
                and (not descriptor.get("nogui")
                     or self.show_nogui_check.isChecked())
                and isinstance(hint, str)
                and hint
            ):
                # A GPIO group is also its explicit read/write pairing key.
                # Keep descriptors without a group in one legacy bucket so
                # existing devices with a single PIN_GET/PIN_SET pair continue
                # to work unchanged.  Other command widgets retain their
                # historical grouping by widget hint alone.
                pair_group = (
                    str(descriptor.get("group") or "")
                    if hint == "special_gpio"
                    else None
                )
                grouped[(hint, pair_group)].append(descriptor)

        hidden: set[str] = set()
        for (hint, pair_group), descriptors in grouped.items():
            widget_class = COMMAND_WIDGETS.get(hint)
            group_context = (
                f" (group={pair_group!r})"
                if hint == "special_gpio" and pair_group
                else ""
            )
            if widget_class is None:
                self._append_terminal(
                    f"COMMAND_WIDGETS '{hint}'{group_context} не зарегистрирован; команды "
                    "будут показаны стандартными формами.",
                    "warning",
                )
                continue
            if (
                not isinstance(widget_class, type)
                or not issubclass(widget_class, CommandWidget)
            ):
                self._append_terminal(
                    f"COMMAND_WIDGETS '{hint}' содержит несовместимый класс; "
                    "команды будут показаны стандартными формами.",
                    "warning",
                )
                continue
            try:
                widget_class.validate_descriptors(descriptors)
                widget = widget_class(self, descriptors)
            except WidgetCompatibilityError as exc:
                self._append_terminal(
                    f"COMMAND_WIDGETS '{hint}'{group_context} отклонил "
                    f"дескрипторы: {exc}. "
                    "Команды будут показаны стандартными формами.",
                    "warning",
                )
                continue
            self.command_widgets.append(widget)
            for command in widget.handled_command_names():
                self.command_widget_by_command[command] = widget
            hidden.update(widget.hidden_command_names())
        return hidden

    def _toggle_developer(self, enabled: bool) -> None:
        self.raw_row.setVisible(enabled)
        self.developer_stats_label.setVisible(enabled)
        if not enabled and self.show_nogui_check.isChecked():
            self.show_nogui_check.setChecked(False)
        self.show_nogui_check.setVisible(enabled)
        if enabled:
            self._update_connection_stats()

    def _nogui_visibility_changed(self, _enabled: bool) -> None:
        if not self.descriptors:
            return
        self.forms.clear()
        self.command_widgets.clear()
        self.command_widget_by_command.clear()
        self.io_panel = None
        self.io_panels.clear()
        self._reset_tabs()
        self._build_dynamic_tabs()

    def _record_sent_bytes(self, count: int) -> None:
        if count > 0:
            self._session_tx_bytes += count
            self._update_connection_stats()

    def _record_received_bytes(self, count: int) -> None:
        if count > 0:
            self._session_rx_bytes += count
            self._update_connection_stats()

    @staticmethod
    def _format_connection_time(seconds: float) -> str:
        total = max(0, int(seconds))
        hours, remainder = divmod(total, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    def _update_connection_stats(self) -> None:
        elapsed = self._disconnected_elapsed
        connected = self._connected_at is not None
        if connected:
            elapsed = max(0.0, time.monotonic() - self._connected_at)
        status = "подключено" if connected else "отключено"
        tx = f"{self._session_tx_bytes:,}".replace(",", " ")
        rx = f"{self._session_rx_bytes:,}".replace(",", " ")
        self.developer_stats_label.setText(
            f"TX: {tx} B · RX: {rx} B · Время: "
            f"{self._format_connection_time(elapsed)} · {status}"
        )

    def _send_raw(self) -> None:
        if self.worker is None:
            return
        line = self.raw_edit.text().strip()
        if not line:
            return
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("Корень должен быть object")
            if "id" not in request:
                request["id"] = self._allocate_id()
            transaction = request["id"]
            if not isinstance(transaction, int) or not 1 <= transaction <= 0xFFFFFFFF:
                raise ValueError("id должен быть uint32 и не равен нулю")
            line = json.dumps(request, ensure_ascii=False, separators=(",", ":"))
        except (json.JSONDecodeError, ValueError) as exc:
            QMessageBox.warning(self, "JSON", str(exc))
            return
        self.raw_history.append(line)
        self.raw_history_index = len(self.raw_history)
        self.raw_edit.clear()
        self.worker.send_line(line)

    def _poll(self) -> None:
        if not self.autopoll_check.isChecked() or self.worker is None:
            return
        period = int(self.poll_period_combo.currentData())
        for form in self.forms:
            if self._command_is_pending(form.command):
                continue
            form.poll(period)
        for widget in self.command_widgets:
            widget.poll(period)

    def _autopoll_toggled(self, enabled: bool) -> None:
        for form in self.forms:
            form.set_global_autopoll(enabled)

    def _append_terminal(self, text: str, severity: str = "info") -> None:
        colors = {
            "debug": QColor(theme_color("terminal_debug")),
            "info": QColor(theme_color("terminal_info")),
            "warning": QColor(theme_color("terminal_warning")),
            "error": QColor(theme_color("terminal_error")),
        }
        cursor = self.terminal.textCursor()
        cursor.movePosition(QTextCursor.End)
        fmt = QTextCharFormat()
        fmt.setForeground(colors.get(severity, colors["info"]))
        timestamp = time.strftime("%H:%M:%S")
        cursor.insertText(f"[{timestamp}] {text}\n", fmt)
        if self.autoscroll_check.isChecked():
            self.terminal.setTextCursor(cursor)
            self.terminal.ensureCursorVisible()


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Starset")
    set_theme(DEFAULT_THEME)
    app.setPalette(application_palette())
    app.setStyleSheet(application_stylesheet())
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
