from __future__ import annotations

import json
import threading
import time

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent

import Starset as gui
from krul_simulator import DESCRIPTORS, KrulSimulator, SimulatorServer
from krul_wire import FORMAT_BSON, FORMAT_CBOR, FORMAT_JSON, encode_frame


@pytest.mark.parametrize("wire_format", [FORMAT_JSON, FORMAT_BSON, FORMAT_CBOR])
def test_starset_worker_supports_framed_simulator(qtbot,
                                                  wire_format: str) -> None:
    server = SimulatorServer(("127.0.0.1", 0), KrulSimulator())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    worker = gui.SerialWorker(
        f"socket://127.0.0.1:{server.server_address[1]}", 115200
    )
    worker.set_wire_format(wire_format)
    received_byte_counts: list[int] = []
    worker.bytes_received.connect(received_byte_counts.append)
    try:
        with qtbot.waitSignal(worker.opened, timeout=2000):
            worker.start()
        request = '{"cmd":"WHOAMI","id":1}'
        with qtbot.waitSignal(worker.bytes_sent, timeout=2000) as sent, \
                qtbot.waitSignal(worker.line_received, timeout=2000) as received:
            worker.send_line(request)
        response = json.loads(received.args[0])
        assert sent.args[0] == len(encode_frame(json.loads(request), wire_format))
        assert sum(received_byte_counts) > 0
        assert response["result"]["device_name"] == "KRUL-PC-SIM"
    finally:
        worker.stop()
        worker.wait(2000)
        server.shutdown()
        server.server_close()


def test_developer_mode_shows_session_traffic_and_connection_time(qtbot) -> None:
    window = gui.MainWindow()
    qtbot.addWidget(window)
    assert window.developer_stats_label.isHidden()

    window.developer_check.setChecked(True)
    window._connected_at = time.monotonic() - 65.0
    window._record_sent_bytes(1234)
    window._record_received_bytes(5678)
    window._update_connection_stats()

    text = window.developer_stats_label.text()
    assert not window.developer_stats_label.isHidden()
    assert "TX: 1 234 B" in text
    assert "RX: 5 678 B" in text
    assert "Время: 00:01:05" in text
    assert "подключено" in text

    window._disconnect()
    assert "отключено" in window.developer_stats_label.text()


def test_malformed_messages_are_warnings_in_developer_mode(qtbot) -> None:
    window = gui.MainWindow()
    qtbot.addWidget(window)

    window.developer_check.setChecked(True)
    window._receive_line("not valid json")
    text = window.terminal.toPlainText()
    assert "RX < not valid json" in text
    assert "Некорректный JSON от МК: not valid json" in text

    window._receive_line("[1, 2, 3]")
    assert "Корневое значение ответа не является object" in \
        window.terminal.toPlainText()


def test_malformed_messages_are_warnings_without_developer_mode(qtbot) -> None:
    window = gui.MainWindow()
    qtbot.addWidget(window)

    assert not window.developer_check.isChecked()
    window._receive_line("not valid json")
    text = window.terminal.toPlainText()
    assert "Некорректный JSON от МК: not valid json" in text
    assert "RX < not valid json" not in text


def test_developer_mode_can_force_nogui_commands_visible(qtbot) -> None:
    window = gui.MainWindow()
    qtbot.addWidget(window)
    window.descriptors = {
        "VISIBLE": {
            "cmd": "VISIBLE", "title": "Visible", "tab": "Test",
            "params": [], "result": [],
        },
        "HIDDEN": {
            "cmd": "HIDDEN", "title": "Hidden", "tab": "Test",
            "params": [], "result": [], "nogui": True,
        },
    }
    window._reset_tabs()
    window._build_dynamic_tabs()
    assert "HIDDEN" not in {form.command for form in window.forms}

    window.developer_check.setChecked(True)
    assert not window.show_nogui_check.isHidden()
    window.show_nogui_check.setChecked(True)
    assert "HIDDEN" in {form.command for form in window.forms}

    window.developer_check.setChecked(False)
    assert window.show_nogui_check.isHidden()
    assert not window.show_nogui_check.isChecked()
    assert "HIDDEN" not in {form.command for form in window.forms}


def test_command_heading_description_and_group_spacing(qtbot) -> None:
    window = gui.MainWindow()
    qtbot.addWidget(window)
    body = gui.QWidget()
    body_layout = gui.QVBoxLayout(body)
    descriptors = [
        {
            "cmd": "FIRST",
            "title": "First command",
            "description": "First description",
            "group": "Test",
            "params": [],
            "result": [],
        },
        {
            "cmd": "SECOND",
            "title": "Second command",
            "description": "Second description",
            "group": "Test",
            "params": [],
            "result": [],
        },
    ]

    window._populate_command_groups(body_layout, descriptors)
    group = body.findChild(gui.QGroupBox, "commandGroup")
    assert group is not None
    page_grid = body.findChild(gui.ResponsiveCardGrid, "commandGroupsGrid")
    command_grid = group.findChild(gui.ResponsiveCardGrid, "commandCardsGrid")
    assert page_grid is not None and page_grid.cards == [group]
    assert command_grid is not None and len(command_grid.cards) == 2
    assert group.layout().spacing() == gui.COMMAND_GROUP_SPACING

    first = next(form for form in window.forms if form.command == "FIRST")
    assert first.command_title_label.text() == "First command"
    assert first.command_title_label.font().bold()
    assert first.description_label is not None
    assert first.description_label.text() == "First description"
    assert first.layout().indexOf(first.description_label) == (
        first.layout().indexOf(first.command_title_label) + 1
    )
    action_spacer = first.body_layout.itemAt(
        first.body_layout.count() - 2
    ).spacerItem()
    assert action_spacer is not None
    assert action_spacer.sizeHint().height() == gui.COMMAND_EXECUTE_TOP_SPACING

    group_header = group.findChild(gui.CollapseHeader, "commandGroupHeader")
    assert group_header is not None and group_header.is_expanded()
    assert not first.is_expanded()
    assert first.body.isHidden()


def test_command_and_group_headers_fold_their_content(qtbot) -> None:
    window = gui.MainWindow()
    qtbot.addWidget(window)
    body = gui.QWidget()
    body_layout = gui.QVBoxLayout(body)
    descriptors = [{
        "cmd": "FIRST", "title": "First", "group": "Test",
        "params": [], "result": [],
    }]
    window._populate_command_groups(body_layout, descriptors)

    group = body.findChild(gui.QGroupBox, "commandGroup")
    group_header = group.findChild(gui.CollapseHeader, "commandGroupHeader")
    command_grid = group.findChild(gui.ResponsiveCardGrid, "commandCardsGrid")
    assert group_header is not None and command_grid is not None
    assert group_header.is_expanded()
    assert not command_grid.isHidden()

    group_header.set_expanded(False)
    assert command_grid.isHidden()
    group_header.set_expanded(True)
    assert not command_grid.isHidden()

    form = next(form for form in window.forms if form.command == "FIRST")
    assert form.command_title_label.text() == "First"
    assert not form.is_expanded()
    assert form.body.isHidden()

    form.command_title_label.setChecked(True)
    assert form.is_expanded()
    assert not form.body.isHidden()

    form.command_title_label.setChecked(False)
    assert form.body.isHidden()


def test_collapse_header_size_scales_only_the_arrow(qtbot) -> None:
    command = gui.CollapseHeader("Command", "commandTitle")
    group = gui.CollapseHeader(
        "Group", "commandGroupHeader",
        size=gui.COLLAPSE_HEADER_GROUP_SCALE,
    )
    qtbot.addWidget(command)
    qtbot.addWidget(group)

    # Only the arrow shrinks; the label stays at the normal font size.
    assert group.iconSize().height() < command.iconSize().height()
    assert group.font().pointSizeF() == command.font().pointSizeF()


def test_responsive_card_grid_uses_width_hints_and_reflows(qtbot) -> None:
    class SizedCard(gui.QWidget):
        def __init__(self, width: int, height: int) -> None:
            super().__init__()
            self.hint = gui.QSize(width, height)

        def sizeHint(self):  # noqa: N802 - Qt API
            return self.hint

        def minimumSizeHint(self):  # noqa: N802 - Qt API
            return gui.QSize(120, self.hint.height())

    grid = gui.ResponsiveCardGrid(min_column_width=220, max_columns=3)
    cards = [
        SizedCard(180, 90),
        SizedCard(180, 50),
        SizedCard(520, 120),
        SizedCard(180, 70),
    ]
    for card in cards:
        grid.add_card(card)
    qtbot.addWidget(grid)
    grid.resize(720, 1)
    grid.show()
    qtbot.wait(20)

    assert grid._columns == 3
    assert grid._card_spans[cards[2]] == 2
    assert len({card.geometry().x() for card in cards}) > 1
    assert all(card.geometry().right() < grid.width() for card in cards)

    grid.resize(260, grid.height())
    qtbot.wait(20)

    assert grid._columns == 1
    assert all(card.geometry().x() == 0 for card in cards)
    assert all(card.geometry().width() == 260 for card in cards)
    assert [card.geometry().y() for card in cards] == sorted(
        card.geometry().y() for card in cards
    )


def test_grid_stacks_later_cards_below_earlier_full_width_card(qtbot) -> None:
    class SizedCard(gui.QWidget):
        def __init__(self, height: int) -> None:
            super().__init__()
            self.hint_height = height

        def sizeHint(self):  # noqa: N802 - Qt API
            return gui.QSize(180, self.hint_height)

        def minimumSizeHint(self):  # noqa: N802 - Qt API
            return gui.QSize(120, self.hint_height)

    grid = gui.ResponsiveCardGrid(min_column_width=220, max_columns=3)
    tall = SizedCard(200)
    full = SizedCard(120)
    full.setProperty("gridSpanMode", "full")
    trailing = SizedCard(60)
    for card in (tall, full, trailing):
        grid.add_card(card)
    qtbot.addWidget(grid)
    grid.resize(720, 1)
    grid.show()
    qtbot.wait(20)

    assert grid._card_spans[full] == 3
    assert tall.geometry().y() == 0
    assert full.geometry().y() >= tall.geometry().bottom()
    # Order preserving: a card placed after the full-width card stays below it
    # instead of backfilling a hole above it, so growing a card above can
    # never force it to move.
    assert trailing.geometry().y() >= full.geometry().bottom()
    assert trailing.geometry().x() == 0


def test_growing_a_card_never_reorders_later_cards(qtbot) -> None:
    class SizedCard(gui.QWidget):
        def __init__(self, height: int) -> None:
            super().__init__()
            self.hint_height = height

        def sizeHint(self):  # noqa: N802 - Qt API
            return gui.QSize(180, self.hint_height)

        def minimumSizeHint(self):  # noqa: N802 - Qt API
            return gui.QSize(120, self.hint_height)

    grid = gui.ResponsiveCardGrid(min_column_width=220, max_columns=3)
    first = SizedCard(100)
    second = SizedCard(120)
    full = SizedCard(100)
    full.setProperty("gridSpanMode", "full")
    below_full = SizedCard(90)
    last = SizedCard(90)
    cards = [first, second, full, below_full, last]
    for card in cards:
        grid.add_card(card)
    qtbot.addWidget(grid)
    grid.resize(720, 1)
    grid.show()
    qtbot.wait(20)

    before = {card: (card.geometry().x(), card.geometry().y())
              for card in cards}

    # Unfolding a command grows this card a lot.
    second.hint_height = 520
    grid.request_relayout()
    qtbot.wait(20)
    after = {card: (card.geometry().x(), card.geometry().y())
             for card in cards}

    # The grown card keeps its column and its row.
    assert after[second][0] == before[second][0]
    assert after[second][1] == before[second][1]
    # Cards after it only move down, never above their previous position.
    for card in (full, below_full, last):
        assert after[card][0] == before[card][0]
        assert after[card][1] >= before[card][1]


def test_grid_keeps_card_columns_when_height_changes(qtbot) -> None:
    class SizedCard(gui.QWidget):
        def __init__(self, height: int) -> None:
            super().__init__()
            self.hint_height = height

        def sizeHint(self):  # noqa: N802 - Qt API
            return gui.QSize(180, self.hint_height)

        def minimumSizeHint(self):  # noqa: N802 - Qt API
            return gui.QSize(120, self.hint_height)

    grid = gui.ResponsiveCardGrid(min_column_width=220, max_columns=3)
    cards = [SizedCard(height) for height in (100, 120, 90, 110, 130, 80)]
    for card in cards:
        grid.add_card(card)
    qtbot.addWidget(grid)
    grid.resize(720, 1)
    grid.show()
    qtbot.wait(20)
    assert grid._columns == 3
    assert len({card.geometry().x() for card in cards}) > 1

    before = {card: (card.geometry().x(), card.geometry().y())
              for card in cards}

    # Unfolding one card makes it much taller; the others must not jump
    # to another column, only slide down.
    cards[0].hint_height = 400
    grid.request_relayout()
    qtbot.wait(20)
    grown = {card: (card.geometry().x(), card.geometry().y())
             for card in cards}
    assert all(before[card][0] == grown[card][0] for card in cards)

    # Folding it back restores every card to its original position.
    cards[0].hint_height = 100
    grid.request_relayout()
    qtbot.wait(20)
    restored = {card: (card.geometry().x(), card.geometry().y())
                for card in cards}
    assert restored == before


def test_io_panel_has_no_horizontal_layout_margins(qtbot) -> None:
    panel = gui.IOPanel(
        [
            {"name": "BUTTON", "type": "IN", "state": 0},
            {"name": "LED", "type": "OUT", "state": 0},
        ],
        lambda *_args: None,
    )
    qtbot.addWidget(panel)

    outer = panel.layout()
    assert (outer.contentsMargins().left(), outer.contentsMargins().right()) == (0, 0)
    filter_row = outer.itemAt(0).layout()
    assert filter_row is not None
    assert filter_row.spacing() == gui.IO_FILTER_FIELD_SPACING
    assert (
        filter_row.contentsMargins().left(),
        filter_row.contentsMargins().right(),
    ) == (0, 0)

    scroll = outer.itemAt(1).widget()
    body_layout = scroll.widget().layout()
    assert (
        body_layout.contentsMargins().left(),
        body_layout.contentsMargins().right(),
    ) == (0, 0)

    sections = scroll.widget().findChildren(gui.QGroupBox, "gpioSection")
    assert len(sections) == 2
    for section in sections:
        margins = section.layout().contentsMargins()
        assert (margins.left(), margins.right()) == (0, 0)
    output_spacing = sections[1].layout().itemAt(1).spacerItem()
    assert output_spacing is not None
    assert output_spacing.sizeHint().height() == gui.IO_OUTPUT_ACTIONS_TOP_SPACING

    for grid in (panel.input_grid.grid, panel.output_grid.grid):
        margins = grid.contentsMargins()
        assert (margins.left(), margins.right()) == (0, 0)
        assert grid.horizontalSpacing() == gui.PIN_GRID_HORIZONTAL_SPACING

    card_margins = panel.cards["LED"].layout().contentsMargins()
    assert (card_margins.left(), card_margins.right()) == (0, 0)


def test_embedded_io_panel_expands_without_nested_scroll(qtbot) -> None:
    panel = gui.IOPanel(
        [
            {"name": f"OUT_{index}", "type": "OUT", "state": 0}
            for index in range(60)
        ],
        lambda *_args: None,
        use_internal_scroll=False,
    )
    qtbot.addWidget(panel)
    panel.resize(800, 100)
    panel.show()
    qtbot.wait(20)

    assert panel.findChild(gui.QScrollArea) is None
    assert panel.sizeHint().height() > 300
    assert panel.sizePolicy().verticalPolicy() == gui.QSizePolicy.Minimum


def test_button_and_inactive_tab_cursors(qtbot) -> None:
    window = gui.MainWindow()
    qtbot.addWidget(window)
    window.tabs.addTab(gui.QWidget(), "Second")
    window.show()

    assert window.connect_button.cursor().shape() == Qt.PointingHandCursor
    assert window.rebuild_button.cursor().shape() == Qt.PointingHandCursor

    tab_bar = window.tabs.tabBar()
    qtbot.mouseMove(tab_bar, tab_bar.tabRect(1).center())
    assert tab_bar.cursor().shape() == Qt.PointingHandCursor
    qtbot.mouseMove(tab_bar, tab_bar.tabRect(0).center())
    assert tab_bar.cursor().shape() == Qt.ArrowCursor


def test_theme_button_switches_complete_application_theme(qtbot) -> None:
    application = gui.QApplication.instance()
    gui.set_theme("dark")
    application.setPalette(gui.application_palette())
    application.setStyleSheet(gui.application_stylesheet())
    try:
        window = gui.MainWindow()
        qtbot.addWidget(window)
        assert set(gui.THEMES["light"]) == set(gui.THEMES["dark"])
        assert window.theme_button.text() == "☀ Светлая тема"

        qtbot.mouseClick(window.theme_button, Qt.LeftButton)

        assert gui.theme_color("background") == "#FFFFFF"
        assert window.theme_button.text() == "☾ Тёмная тема"
        assert application.palette().color(gui.QPalette.Window).name() == "#ffffff"
        assert gui.theme_color("terminal_background") in application.styleSheet()
    finally:
        gui.set_theme("dark")
        application.setPalette(gui.application_palette())
        application.setStyleSheet(gui.application_stylesheet())


def test_refresh_and_terminal_clear_buttons_use_utility_style(qtbot) -> None:
    window = gui.MainWindow()
    qtbot.addWidget(window)

    assert window.refresh_ports_button.objectName() == "utilityButton"
    assert window.clear_terminal_button.objectName() == "utilityButton"
    stylesheet = gui.application_stylesheet()
    assert "QToolButton#utilityButton" in stylesheet
    assert gui.theme_color("utility_button") in stylesheet

    window.terminal.setPlainText("message")
    qtbot.mouseClick(window.clear_terminal_button, Qt.LeftButton)
    assert window.terminal.toPlainText() == ""


def test_disconnect_button_is_red_only_while_connected(qtbot) -> None:
    window = gui.MainWindow()
    qtbot.addWidget(window)
    assert window.connect_button.styleSheet() == ""

    window._serial_opened()
    assert window.connect_button.text() == "Отключить"
    assert gui.theme_color("danger") in window.connect_button.styleSheet()

    window._disconnect()
    assert window.connect_button.text() == "Подключить"
    assert window.connect_button.styleSheet() == ""


def test_heartbeat_disconnects_after_three_consecutive_misses(qtbot) -> None:
    class HeartbeatWorker:
        def __init__(self) -> None:
            self.requests = []
            self.stopped = False

        def send_line(self, line: str) -> None:
            self.requests.append(json.loads(line))

        def stop(self) -> None:
            self.stopped = True

        def wait(self, _milliseconds: int) -> bool:
            return True

        def deleteLater(self) -> None:  # noqa: N802 - Qt-compatible fake
            return

    window = gui.MainWindow()
    qtbot.addWidget(window)
    worker = HeartbeatWorker()
    window.worker = worker
    window._start_heartbeat()
    window.heartbeat_timer.stop()

    window._heartbeat_tick()
    first = worker.requests[-1]
    assert first["cmd"] == "PING"
    window._receive_line(json.dumps({
        "id": first["id"],
        "success": True,
    }))
    assert window._heartbeat_misses == 0

    window._heartbeat_tick()
    window._heartbeat_tick()
    assert window._heartbeat_misses == 1
    window._heartbeat_tick()
    assert window._heartbeat_misses == 2
    window._heartbeat_tick()

    assert window.worker is None
    assert worker.stopped
    assert window.device_label.text() == "МК: связь потеряна"


def test_disabling_ping_stops_send_and_resets_state(qtbot) -> None:
    class HeartbeatWorker:
        def __init__(self) -> None:
            self.requests = []

        def send_line(self, line: str) -> None:
            self.requests.append(json.loads(line))

    window = gui.MainWindow()
    qtbot.addWidget(window)
    worker = HeartbeatWorker()
    window.worker = worker

    window._heartbeat_tick()
    assert worker.requests[-1]["cmd"] == "PING"
    assert window._heartbeat_transaction is not None

    window._heartbeat_misses = 2
    window.heartbeat_check.setChecked(False)
    assert not window.heartbeat_timer.isActive()
    assert window._heartbeat_transaction is None
    assert window._heartbeat_misses == 0
    assert not window.pending

    window._heartbeat_tick()
    assert len(worker.requests) == 1
    window.worker = None


def test_spinbox_has_no_arrows_and_combobox_has_visible_arrow() -> None:
    stylesheet = gui.application_stylesheet()
    assert "QSpinBox::up-button" in stylesheet
    assert "QDoubleSpinBox::down-button" in stylesheet
    assert "QSpinBox::up-arrow" in stylesheet
    assert "image: none" in stylesheet
    assert "QComboBox::drop-down" in stylesheet
    assert "QComboBox::down-arrow" in stylesheet
    assert "combo-arrow-dark.svg" in stylesheet
    assert "border: none" in stylesheet


def test_enum_parameter_and_result_use_descriptor_values(qtbot) -> None:
    values = [
        {"value": 0, "title": "Ожидание"},
        {"value": 1, "title": "Работа"},
    ]
    form = gui.CommandForm(
        {
            "cmd": "MODE",
            "params": [{
                "name": "requested",
                "type": "enum",
                "default": 0,
                "constraints": {"values": values},
            }],
            "result": [{
                "name": "active",
                "type": "enum",
                "constraints": {"values": values},
            }],
        },
        lambda *_args: None,
    )
    qtbot.addWidget(form)

    selector = form.param_widgets["requested"]
    assert isinstance(selector, gui.QComboBox)
    assert selector.currentText() == "Ожидание"
    selector.setCurrentIndex(selector.findData(1))
    assert form.parameters() == {"requested": 1}

    output = form.result_widgets["active"]
    assert isinstance(output, gui.ResultEnumLabel)
    form.handle_response({"success": True, "result": {"active": 1}})
    assert output.text() == "Работа"
    assert output.toolTip() == "Значение протокола: 1"

    form.handle_response({"success": True, "result": {"active": 99}})
    assert output.text() == "99"


def test_message_boxes_follow_the_dark_theme() -> None:
    stylesheet = gui.application_stylesheet()
    assert "QMessageBox" in stylesheet
    assert f"background-color: {gui.theme_color('surface')}" in stylesheet
    assert f"color: {gui.theme_color('text')}" in stylesheet


def test_wheel_over_input_scrolls_page_without_editing_value(qtbot) -> None:
    window = gui.MainWindow()
    qtbot.addWidget(window)
    scroll = gui.QScrollArea()
    body = gui.QWidget()
    layout = gui.QVBoxLayout(body)
    spin = gui.QSpinBox()
    spin.setRange(0, 100)
    spin.setValue(50)
    layout.addWidget(spin)
    slider = gui.QSlider(Qt.Horizontal)
    slider.setRange(0, 100)
    slider.setValue(50)
    layout.addWidget(slider)
    for index in range(40):
        layout.addWidget(gui.QLabel(f"Строка {index}"))
    scroll.setWidget(body)
    scroll.setWidgetResizable(True)
    scroll.resize(240, 150)
    qtbot.addWidget(scroll)
    scroll.show()
    qtbot.wait(20)
    scroll.verticalScrollBar().setValue(20)
    old_scroll = scroll.verticalScrollBar().value()

    local = QPointF(spin.rect().center())
    global_position = QPointF(spin.mapToGlobal(QPoint(5, 5)))
    event = QWheelEvent(
        local,
        global_position,
        QPoint(0, 0),
        QPoint(0, -120),
        Qt.NoButton,
        Qt.NoModifier,
        Qt.ScrollUpdate,
        False,
    )
    gui.QApplication.sendEvent(spin, event)

    assert spin.value() == 50
    assert scroll.verticalScrollBar().value() > old_scroll

    old_scroll = scroll.verticalScrollBar().value()
    slider_event = QWheelEvent(
        QPointF(slider.rect().center()),
        QPointF(slider.mapToGlobal(QPoint(5, 5))),
        QPoint(0, 0),
        QPoint(0, -120),
        Qt.NoButton,
        Qt.NoModifier,
        Qt.ScrollUpdate,
        False,
    )
    gui.QApplication.sendEvent(slider, slider_event)

    assert slider.value() == 50
    assert scroll.verticalScrollBar().value() > old_scroll


def test_checkbox_style_uses_surface_accent_and_white_checkmark() -> None:
    stylesheet = gui.application_stylesheet()
    assert "QCheckBox::indicator" in stylesheet
    assert f"background-color: {gui.theme_color('surface')}" in stylesheet
    assert f"background-color: {gui.theme_color('accent')}" in stylesheet
    assert f"border: 1px solid {gui.theme_color('border')}" in stylesheet
    assert "border-radius: 2px" in stylesheet
    checkmark = gui.Path(gui.__file__).resolve().parent / "assets" / "check.svg"
    assert checkmark.exists()
    assert 'stroke="#fff"' in checkmark.read_text(encoding="utf-8")


def test_gpio_widget_uses_normal_tabs_and_supplies_io_commands(qtbot) -> None:
    window = gui.MainWindow()
    qtbot.addWidget(window)
    window.descriptors = dict(DESCRIPTORS)
    window._build_dynamic_tabs()

    gpio = next(
        widget for widget in window.command_widgets
        if isinstance(widget, gui.SpecialGpioCommandWidget)
    )
    assert {item["cmd"] for item in gpio.get_descriptors} == {
        "PIN_GET",
        "PIN_GET_V2",
    }
    assert gpio.set_descriptor["cmd"] == "PIN_SET"
    gpio_hosts = [
        layout.parentWidget()
        for layouts in gpio.hosts.values()
        for layout in layouts
    ]
    assert gpio_hosts
    assert all(host.property("gridSpanMode") == "full" for host in gpio_hosts)
    assert all(
        host.parentWidget().parentWidget().property("gridSpanMode") == "full"
        for host in gpio_hosts
    )
    assert all(
        form.command not in {"PIN_GET", "PIN_GET_V2", "PIN_SET"}
        for form in window.forms
    )

    sent: list[str] = []
    panel = gui.IOPanel(
        [{"name": "BUTTON", "type": "IN", "state": 0}],
        lambda command, *_args: sent.append(command),
        get_command="GPIO_READ",
        set_command="GPIO_WRITE",
    )
    qtbot.addWidget(panel)
    panel.poll_inputs()
    assert sent == ["GPIO_READ"]

    gpio._pins_received(
        gpio.get_descriptors[0],
        {
            "success": True,
            "result": {
                "pins": [{"name": "BUTTON", "type": "IN", "state": 0}]
            },
        },
    )
    rabbit = window.tabs.widget(0).widget()
    assert len(rabbit.findChildren(gui.IOPanel)) == 1
    assert len(window.io_panels) == 1
    assert window.io_panels[0].findChild(gui.QScrollArea) is None
    window.io_panels[0]._updated(
        {
            "success": True,
            "result": {
                "pins": [{"name": "BUTTON", "type": "IN", "state": 1}]
            },
        }
    )
    assert window.io_panels[0].cards["BUTTON"].indicator.text.text() == "HIGH"

    gpio._pins_received(
        next(item for item in gpio.get_descriptors if item["cmd"] == "PIN_GET_V2"),
        {
            "success": True,
            "result": {
                "pins": [{"name": "BUTTON", "type": "IN", "state": 0}]
            },
        },
    )
    tab_names = [window.tabs.tabText(index) for index in range(window.tabs.count())]
    assert "GPIOA" in tab_names
    assert all(not name.startswith("IO ") for name in tab_names)
    gpioa = window.tabs.widget(tab_names.index("GPIOA")).widget()
    assert len(gpioa.findChildren(gui.IOPanel)) == 1


def test_gpio_widget_uses_group_as_pair_identifier(qtbot) -> None:
    window = gui.MainWindow()
    qtbot.addWidget(window)

    def get_descriptor(command: str, group: str) -> dict:
        return {
            "cmd": command,
            "tab": "GPIO",
            "group": group,
            "widget_hint": "special_gpio",
            "params": [{"name": "pins", "type": "array"}],
            "result": [],
        }

    def set_descriptor(command: str, group: str) -> dict:
        return {
            "cmd": command,
            "tab": "GPIO",
            "group": group,
            "widget_hint": "special_gpio",
            "params": [{
                "name": "pins",
                "type": "array",
                "items": {
                    "type": "object",
                    "fields": [
                        {"name": "name", "type": "enum"},
                        {"name": "state", "type": "integer"},
                    ],
                },
            }],
            "result": [],
        }

    descriptors = [
        get_descriptor("LOCAL_GET", "Локальные GPIO"),
        set_descriptor("LOCAL_SET", "Локальные GPIO"),
        get_descriptor("REMOTE_GET", "Удалённые GPIO"),
        set_descriptor("REMOTE_SET", "Удалённые GPIO"),
    ]
    window.descriptors = {
        str(descriptor["cmd"]): descriptor for descriptor in descriptors
    }

    hidden = window._prepare_command_widgets()

    gpio_widgets = [
        widget for widget in window.command_widgets
        if isinstance(widget, gui.SpecialGpioCommandWidget)
    ]
    assert len(gpio_widgets) == 2
    assert hidden == {"LOCAL_SET", "REMOTE_SET"}
    assert {
        widget.get_descriptors[0]["cmd"]: widget.set_command
        for widget in gpio_widgets
    } == {
        "LOCAL_GET": "LOCAL_SET",
        "REMOTE_GET": "REMOTE_SET",
    }
    assert window.command_widget_by_command["LOCAL_GET"].set_command == "LOCAL_SET"
    assert window.command_widget_by_command["REMOTE_GET"].set_command == "REMOTE_SET"


def test_target_gpio_uses_numeric_enums_and_direction_field(qtbot) -> None:
    requests = []

    class FakeWindow:
        def __init__(self) -> None:
            self.io_panels = []
            self.io_panel = None

        def send_request(self, command, params=None, callback=None, timeout=None):
            requests.append((command, params, callback, timeout))
            return len(requests)

        def _apply_button_cursors(self, _widget) -> None:
            return

        def _sync_graph_gpio_inputs(self) -> None:
            return

        def _gpio_panel_updated(self, _source, _message) -> None:
            return

    get_descriptor = {
        "cmd": "PIN_GET",
        "tab": "GPIO",
        "title": "Read pins",
        "widget_hint": "special_gpio",
        "params": [
            {
                "name": "target",
                "type": "enum",
                "constraints": {"values": [
                    {"value": 0, "title": "CVM"},
                    {"value": 1, "title": "KNUD"},
                    {"value": 2, "title": "KTPP"},
                ]},
            },
            {"name": "name", "type": "string"},
        ],
        "result": [{
            "name": "pins",
            "type": "array",
            "items": {
                "type": "object",
                "fields": [
                    {"name": "name", "type": "string"},
                    {
                        "name": "direction",
                        "type": "enum",
                        "constraints": {"values": [
                            {"value": 0, "title": "Input"},
                            {"value": 1, "title": "Output"},
                        ]},
                    },
                    {"name": "state", "type": "integer"},
                ],
            },
        }],
    }
    set_descriptor = {
        "cmd": "PIN_SET",
        "tab": "GPIO",
        "title": "Set output",
        "widget_hint": "special_gpio",
        "params": [
            get_descriptor["params"][0],
            {"name": "name", "type": "string"},
            {
                "name": "state",
                "type": "enum",
                "constraints": {"values": [
                    {"value": 0, "title": "Off"},
                    {"value": 1, "title": "On"},
                    {"value": 2, "title": "Toggle"},
                ]},
            },
        ],
        "result": [],
    }
    fake_window = FakeWindow()
    widget = gui.SpecialGpioCommandWidget(
        fake_window, [get_descriptor, set_descriptor]
    )
    host = widget.create_widget(get_descriptor)
    assert host is not None
    qtbot.addWidget(host)

    widget._targets_received(get_descriptor, {
        "success": True,
        "result": {"targets": [{"name": "CVM", "available": True}]},
    })
    assert requests[-1][0] == "PIN_GET"
    assert requests[-1][1] == {"target": 0, "name": "ALL"}

    requests[-1][2]({
        "success": True,
        "result": {"pins": [
            {"name": "UART2_EN", "direction": 1, "state": 0},
        ]},
    })
    panel = fake_window.io_panels[0]
    assert panel.target == 0
    assert panel.target_label == "CVM"
    assert panel.cards["UART2_EN"].pin_type == "OUT"

    panel._set_one("UART2_EN", 1)
    assert requests[-1][0] == "PIN_SET"
    assert requests[-1][1] == {
        "target": 0, "name": "UART2_EN", "state": 1,
    }


def test_invalid_gpio_group_does_not_disable_valid_pair(qtbot) -> None:
    window = gui.MainWindow()
    qtbot.addWidget(window)
    warnings: list[tuple[str, str]] = []
    window._append_terminal = lambda message, severity="info": warnings.append(
        (message, severity)
    )
    window.descriptors = {
        "GOOD_GET": {
            "cmd": "GOOD_GET",
            "group": "Исправная пара",
            "widget_hint": "special_gpio",
            "params": [{"name": "pins", "type": "array"}],
        },
        "GOOD_SET": {
            "cmd": "GOOD_SET",
            "group": "Исправная пара",
            "widget_hint": "special_gpio",
            "params": [{"name": "state", "type": "integer"}],
        },
        "BROKEN_GET": {
            "cmd": "BROKEN_GET",
            "group": "Неполная пара",
            "widget_hint": "special_gpio",
            "params": [{"name": "pins", "type": "array"}],
        },
    }

    hidden = window._prepare_command_widgets()

    assert hidden == {"GOOD_SET"}
    assert "GOOD_GET" in window.command_widget_by_command
    assert "BROKEN_GET" not in window.command_widget_by_command
    assert any(
        "group='Неполная пара'" in message and severity == "warning"
        for message, severity in warnings
    )


def test_pwm_widget_and_group_are_always_full_width(qtbot) -> None:
    descriptor = {
        "cmd": "PWM_SET",
        "title": "Управление ШИМ",
        "tab": "PWM",
        "group": "PWM",
        "widget_hint": "special_pwm",
        "params": [
            {
                "name": "channel",
                "type": "enum",
                "constraints": {
                    "values": [
                        {"value": f"PWM_{index}", "title": f"PWM {index}"}
                        for index in range(1, 5)
                    ]
                },
            },
            {
                "name": "duty_cycle",
                "type": "integer",
                "default": 0,
                "constraints": {"minimum": 0, "maximum": 100},
            },
            {
                "name": "period_counter",
                "type": "integer",
                "default": 100,
                "constraints": {"minimum": 1, "maximum": 1000000},
            },
        ],
        "result": [],
    }
    window = gui.MainWindow()
    qtbot.addWidget(window)
    window.descriptors = {"PWM_SET": descriptor}
    window._build_dynamic_tabs()

    pwm_hosts = [
        host for host in window.findChildren(gui.QGroupBox, "commandForm")
        if host.property("gridSpanMode") == "full"
    ]
    assert len(pwm_hosts) == 2  # Overview and the dedicated PWM tab.
    assert all(
        host.parentWidget().parentWidget().property("gridSpanMode") == "full"
        for host in pwm_hosts
    )


def test_output_indicator_changes_only_from_response_state(qtbot) -> None:
    callbacks = []
    panel = gui.IOPanel(
        [{"name": "LED", "type": "OUT", "state": 0}],
        lambda _command, _params, callback: callbacks.append(callback),
    )
    qtbot.addWidget(panel)

    panel._set_one("LED", 1)
    indicator = panel.cards["LED"].indicator.text
    assert indicator.text() == "LOW"

    callbacks.pop()({
        "success": True,
        "result": {"pins": [{"name": "LED", "state": 1}]},
    })
    assert indicator.text() == "HIGH"

    panel._set_one("LED", 0)
    callbacks.pop()({
        "success": True,
        "result": {"pins": [{"name": "LED"}]},
    })
    assert indicator.text() == "HIGH"


def test_output_slide_switch_uses_confirmed_state_and_shared_colors(qtbot) -> None:
    sent = []

    def sender(command, params, callback):
        sent.append((command, params, callback))
        return 77

    panel = gui.IOPanel(
        [{"name": "LED", "type": "OUT", "state": 0}],
        sender,
    )
    qtbot.addWidget(panel)
    pin_switch = panel.cards["LED"].switch
    assert pin_switch is not None
    panel.show()

    assert pin_switch.state() == 0
    assert pin_switch.size().toTuple() == (40, 20)
    assert gui.theme_color("inactive") == "#5F5AE2"
    assert "#5F5AE2" in panel.cards["LED"].indicator.text.styleSheet()
    assert pin_switch.grab().toImage().pixelColor(32, 10).name() == "#5f5ae2"

    qtbot.mouseClick(pin_switch, Qt.LeftButton)
    command, params, callback = sent.pop()
    assert command == "PIN_SET"
    assert params == {"pins": [{"name": "LED", "state": 1}]}
    assert pin_switch.state() == 0
    assert pin_switch.is_pending()
    assert pin_switch.grab().toImage().pixelColor(32, 10).name() == "#c4c92e"

    callback({
        "success": True,
        "result": {"pins": [{"name": "LED", "state": 1}]},
    })
    assert not pin_switch.is_pending()
    assert pin_switch.state() == 1
    assert pin_switch.grab().toImage().pixelColor(8, 10).name() == "#35c96f"


def test_gpio_action_buttons_show_pending_color(qtbot) -> None:
    sent = []

    def sender(command, params, callback):
        sent.append((command, params, callback))
        return len(sent)

    panel = gui.IOPanel(
        [{"name": "LED", "type": "OUT", "state": 0}],
        sender,
    )
    qtbot.addWidget(panel)

    assert panel.read_inputs_button.focusPolicy() == Qt.NoFocus
    assert panel.read_outputs_button.focusPolicy() == Qt.NoFocus
    assert panel.activate_all_button.focusPolicy() == Qt.NoFocus
    assert panel.deactivate_all_button.focusPolicy() == Qt.NoFocus

    qtbot.mouseClick(panel.read_outputs_button, Qt.LeftButton)
    assert panel.read_outputs_button.property("commandPending") is True
    assert not panel.read_outputs_button.isEnabled()
    _, _, callback = sent.pop()
    callback({"success": True, "result": {"pins": []}})
    assert panel.read_outputs_button.property("commandPending") is False
    assert panel.read_outputs_button.isEnabled()

    qtbot.mouseClick(panel.activate_all_button, Qt.LeftButton)
    assert panel.activate_all_button.property("commandPending") is True
    assert panel.cards["LED"].switch.is_pending()
    _, _, callback = sent.pop()
    callback({"success": True, "result": {"name": "ALL", "state": 1}})
    assert panel.activate_all_button.property("commandPending") is False
    assert not panel.cards["LED"].switch.is_pending()


def test_read_inputs_does_not_move_parent_scroll_area(qtbot) -> None:
    scroll = gui.QScrollArea()
    scroll.setWidgetResizable(True)
    body = gui.QWidget()
    layout = gui.QVBoxLayout(body)
    top = gui.QWidget()
    top.setFixedHeight(700)
    layout.addWidget(top)
    panel = gui.IOPanel(
        [{"name": "BUTTON", "type": "IN", "state": 0}],
        lambda *_args: 1,
        use_internal_scroll=False,
    )
    layout.addWidget(panel)
    bottom = gui.QWidget()
    bottom.setFixedHeight(700)
    layout.addWidget(bottom)
    scroll.setWidget(body)
    scroll.resize(500, 320)
    qtbot.addWidget(scroll)
    scroll.show()
    scroll.ensureWidgetVisible(panel.read_inputs_button)
    qtbot.wait(20)
    position = scroll.verticalScrollBar().value()

    qtbot.mouseClick(panel.read_inputs_button, Qt.LeftButton)
    qtbot.wait(20)

    assert scroll.verticalScrollBar().value() == position


def test_set_all_uses_one_compact_request_and_updates_outputs(qtbot) -> None:
    sent = []
    panel = gui.IOPanel(
        [
            {"name": "BUTTON", "type": "IN", "state": 0},
            {"name": "LED_A", "type": "OUT", "state": 0},
            {"name": "LED_B", "type": "OUT", "state": 0},
        ],
        lambda command, params, callback: sent.append((command, params, callback)),
    )
    qtbot.addWidget(panel)

    panel._set_all(1)

    assert len(sent) == 1
    command, params, callback = sent.pop()
    assert command == "PIN_SET"
    assert params == {"pins": [{"name": "ALL", "state": 1}]}

    callback({"success": True, "result": {"name": "ALL", "state": 1}})
    assert panel.cards["BUTTON"].indicator.text.text() == "LOW"
    assert panel.cards["LED_A"].indicator.text.text() == "HIGH"
    assert panel.cards["LED_B"].indicator.text.text() == "HIGH"


def test_target_set_all_uses_one_gateway_request(qtbot) -> None:
    sent = []
    panel = gui.IOPanel(
        [{"name": "ENABLE", "direction": "OUT", "state": 1}],
        lambda command, params, callback: sent.append((command, params, callback)),
        target="KNUD",
    )
    qtbot.addWidget(panel)

    panel._set_all(0)

    assert len(sent) == 1
    assert sent[0][0] == "PIN_SET"
    assert sent[0][1] == {"target": "KNUD", "name": "ALL", "state": 0}


def test_poll_outputs_reads_only_outputs_in_one_request(qtbot) -> None:
    sent = []
    panel = gui.IOPanel(
        [
            {"name": "BUTTON", "type": "IN", "state": 0},
            {"name": "LED_A", "type": "OUT", "state": 0},
            {"name": "LED_B", "type": "OUT", "state": 1},
        ],
        lambda command, params, callback: sent.append((command, params, callback)),
    )
    qtbot.addWidget(panel)

    panel.poll_outputs()

    assert len(sent) == 1
    assert sent[0][0] == "PIN_GET"
    assert sent[0][1] == {"pins": ["LED_A", "LED_B"]}


def test_target_poll_outputs_uses_out_filter(qtbot) -> None:
    sent = []
    panel = gui.IOPanel(
        [{"name": "ENABLE", "direction": "OUT", "state": 0}],
        lambda command, params, callback: sent.append((command, params, callback)),
        target="KTPP",
    )
    qtbot.addWidget(panel)

    panel.poll_outputs()

    assert len(sent) == 1
    assert sent[0][0] == "PIN_GET"
    assert sent[0][1] == {"target": "KTPP", "name": "OUT"}


def test_starset_heading_style_has_valid_margin_and_weight(qtbot) -> None:
    window = gui.MainWindow()
    qtbot.addWidget(window)
    heading = window.tabs.widget(0).widget().findChild(gui.QLabel)
    assert heading is not None
    assert "margin-top: 10px" in heading.styleSheet()
    assert "font-weight: bold" in heading.styleSheet()


def test_vampire_rabbit_contains_commands_from_every_tab(qtbot) -> None:
    window = gui.MainWindow()
    qtbot.addWidget(window)
    window.descriptors = dict(DESCRIPTORS)
    window._build_dynamic_tabs()

    rabbit = window.tabs.widget(0).widget()
    overview_commands = {
        form.command for form in rabbit.findChildren(gui.CommandForm)
    }
    expected = {
        name for name, descriptor in DESCRIPTORS.items()
        if not descriptor.get("builtin")
        and descriptor.get("widget_hint") not in gui.COMMAND_WIDGETS
    }
    assert overview_commands == expected
    assert sum(form.command == "ECHO" for form in window.forms) == 2


def test_autopoll_response_updates_command_in_every_tab(qtbot) -> None:
    class CaptureWorker:
        def __init__(self) -> None:
            self.requests: list[dict] = []

        def send_line(self, line: str) -> None:
            self.requests.append(json.loads(line))

    descriptor = {
        "cmd": "MONITOR",
        "title": "Monitor",
        "tab": "Telemetry",
        "group": "Power",
        "params": [],
        "result": [{"name": "value", "type": "integer"}],
        "autoupdate": {"min_period": 100, "max_period": 1000},
    }
    window = gui.MainWindow()
    qtbot.addWidget(window)
    window.poll_timer.stop()
    worker = CaptureWorker()
    window.worker = worker
    window.descriptors = {"MONITOR": descriptor}
    window._build_dynamic_tabs()
    window.autopoll_check.setChecked(True)

    forms = [form for form in window.forms if form.command == "MONITOR"]
    assert len(forms) == 2
    window._poll()
    assert len(worker.requests) == 1

    window._receive_line(json.dumps({
        "id": worker.requests[0]["id"],
        "success": True,
        "result": {"value": 7},
    }))
    assert [form.result_widgets["value"].text() for form in forms] == ["7", "7"]

    # Локальные Auto у копий одной команды всегда переключаются вместе.
    forms[0].auto_enabled.setChecked(False)
    assert [form.auto_enabled.isChecked() for form in forms] == [False, False]
    for form in forms:
        form.last_poll = 0.0
    window._poll()
    assert len(worker.requests) == 1

    forms[1].auto_enabled.setChecked(True)
    assert [form.auto_enabled.isChecked() for form in forms] == [True, True]
    for form in forms:
        form.last_poll = 0.0
    window._poll()
    assert len(worker.requests) == 2
    window._receive_line(json.dumps({
        "id": worker.requests[1]["id"],
        "success": True,
        "result": {"value": 8},
    }))
    assert [form.result_widgets["value"].text() for form in forms] == ["8", "8"]

    window.worker = None


def test_nogui_command_is_not_rendered(qtbot) -> None:
    window = gui.MainWindow()
    qtbot.addWidget(window)
    window.descriptors = {
        "VISIBLE": {"cmd": "VISIBLE", "title": "Visible"},
        "HIDDEN": {"cmd": "HIDDEN", "title": "Hidden", "nogui": True},
    }

    window._build_dynamic_tabs()

    assert {form.command for form in window.forms} == {"VISIBLE"}


def test_execute_keeps_scroll_position(qtbot) -> None:
    scroll = gui.QScrollArea()
    scroll.setWidgetResizable(True)
    body = gui.QWidget()
    layout = gui.QVBoxLayout(body)
    sender_calls: list[str] = []

    first = gui.CommandForm(
        {"cmd": "FIRST", "title": "First", "params": [], "result": []},
        lambda command, *_args: sender_calls.append(command),
    )
    first.set_expanded(True)
    layout.addWidget(first)
    for index in range(30):
        layout.addWidget(gui.QLabel(f"Spacer {index}"))
    scroll.setWidget(body)
    scroll.resize(300, 180)
    qtbot.addWidget(scroll)
    scroll.show()
    scroll.verticalScrollBar().setValue(40)
    position = scroll.verticalScrollBar().value()

    qtbot.mouseClick(first.execute_button, Qt.LeftButton)
    qtbot.wait(20)
    assert sender_calls == ["FIRST"]
    assert scroll.verticalScrollBar().value() == position


def test_execute_button_shows_pending_color_until_response(qtbot) -> None:
    callbacks = []

    def sender(_command, _params, callback):
        callbacks.append(callback)
        return 91

    form = gui.CommandForm(
        {"cmd": "WAIT", "title": "Wait", "params": [], "result": []},
        sender,
    )
    form.set_expanded(True)
    qtbot.addWidget(form)

    qtbot.mouseClick(form.execute_button, Qt.LeftButton)
    assert form.execute_button.property("commandPending") is True
    assert not form.execute_button.isEnabled()
    assert gui.theme_color("pending") in gui.application_stylesheet()

    callbacks.pop()({"success": True, "result": {}})
    assert form.execute_button.property("commandPending") is False
    assert form.execute_button.isEnabled()
