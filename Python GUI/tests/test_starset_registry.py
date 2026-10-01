from __future__ import annotations

from typing import Any

import Starset as gui


class DemoParameterWidget(gui.ParameterWidget):
    @classmethod
    def validate_descriptor(cls, field: dict[str, Any]) -> None:
        super().validate_descriptor(field)
        if field.get("type") != "string":
            raise gui.WidgetCompatibilityError("нужен string")

    def __init__(self, field: dict[str, Any]) -> None:
        super().__init__(field)
        self.editor = gui.QLineEdit(str(field.get("default") or ""), self)

    def value(self) -> Any:
        return self.editor.text()


class DemoResultWidget(gui.ResultWidget):
    @classmethod
    def validate_descriptor(cls, field: dict[str, Any]) -> None:
        super().validate_descriptor(field)
        if field.get("type") != "integer":
            raise gui.WidgetCompatibilityError("нужен integer")

    def __init__(self, field: dict[str, Any]) -> None:
        super().__init__(field)
        self.received: Any = None

    def setValue(self, value: Any) -> None:
        self.received = value


def test_parameter_and_result_classes_are_created_from_registries(
        qtbot, monkeypatch) -> None:
    monkeypatch.setitem(gui.PARAM_WIDGETS, "demo_param", DemoParameterWidget)
    monkeypatch.setitem(gui.RESULT_WIDGETS, "demo_result", DemoResultWidget)
    form = gui.CommandForm(
        {
            "cmd": "DEMO",
            "params": [{
                "name": "text",
                "type": "string",
                "default": "hello",
                "widget_hint": "demo_param",
            }],
            "result": [{
                "name": "number",
                "type": "integer",
                "widget_hint": "demo_result",
            }],
        },
        lambda *_args: None,
    )
    qtbot.addWidget(form)

    assert isinstance(form.param_widgets["text"], DemoParameterWidget)
    assert form.parameters() == {"text": "hello"}
    output = form.result_widgets["number"]
    assert isinstance(output, DemoResultWidget)
    form.handle_response({"success": True, "result": {"number": 42}})
    assert output.received == 42


def test_rejected_descriptor_uses_default_result_widget(qtbot) -> None:
    warnings: list[str] = []
    form = gui.CommandForm(
        {
            "cmd": "BAD_ADC",
            "result": [{
                "name": "value",
                "type": "string",
                "widget_hint": "special_adc",
            }],
        },
        lambda *_args: None,
        warnings.append,
    )
    qtbot.addWidget(form)

    assert isinstance(form.result_widgets["value"], gui.QLabel)
    assert not isinstance(form.result_widgets["value"], gui.ResultWidget)
    assert warnings and "special_adc поддерживает только" in warnings[0]


def _label_texts(widget: Any) -> list[str]:
    return [label.text() for label in widget.findChildren(gui.QLabel)]


def _telemetry_node_union() -> dict[str, Any]:
    return {
        "type": "union",
        "tag": {
            "name": "kind",
            "type": "enum",
            "constraints": {"values": [
                {"value": 0, "title": "Телеметрия"},
                {"value": 1, "title": "Событие"},
            ]},
        },
        "variants": [
            {"value": 0, "fields": [
                {"name": "time", "label": "Время", "type": "unsigned"},
            ]},
            {"value": 1, "fields": [
                {"name": "event", "label": "Тип события", "type": "enum",
                 "constraints": {"values": [
                     {"value": 0, "title": "Начало цикла"},
                     {"value": 1, "title": "Конец цикла"},
                 ]}},
                {"name": "cyc_idx", "label": "Номер цикла",
                 "type": "unsigned"},
            ]},
        ],
    }


def test_union_result_renders_tag_and_selected_variant(qtbot) -> None:
    form = gui.CommandForm(
        {
            "cmd": "READ_TELEMETRY",
            "result": [{
                "name": "node",
                "label": "Узел",
                "type": "union",
                "tag": {
                    "name": "kind",
                    "type": "enum",
                    "constraints": {"values": [
                        {"value": 0, "title": "Телеметрия"},
                        {"value": 1, "title": "Событие"},
                    ]},
                },
                "variants": [
                    {"value": 0, "fields": [
                        {"name": "time", "label": "Время",
                         "type": "unsigned"},
                    ]},
                    {"value": 1, "fields": [
                        {"name": "event", "label": "Тип события",
                         "type": "enum", "constraints": {"values": [
                             {"value": 0, "title": "Начало цикла"},
                             {"value": 1, "title": "Конец цикла"},
                         ]}},
                        {"name": "cyc_idx", "label": "Номер цикла",
                         "type": "unsigned"},
                    ]},
                ],
            }],
        },
        lambda *_args: None,
    )
    qtbot.addWidget(form)

    widget = form.result_widgets["node"]
    assert isinstance(widget, gui.ResultStructuredWidget)

    form.handle_response({
        "success": True,
        "result": {"node": {"kind": 1, "event": 1, "cyc_idx": 7}},
    })
    texts = _label_texts(widget)
    assert "Событие" in texts          # tag title for value 1
    assert "Тип события" in texts      # variant field label
    assert "Конец цикла" in texts      # nested enum title
    assert "Номер цикла" in texts
    assert "7" in texts
    assert "Время" not in texts        # variant 0 field must be hidden

    form.handle_response({
        "success": True,
        "result": {"node": {"kind": 0, "time": 12345}},
    })
    texts = _label_texts(widget)
    assert "Телеметрия" in texts
    assert "Время" in texts
    assert "12345" in texts
    assert "Тип события" not in texts  # variant 1 field must be hidden


def test_array_of_union_result_renders_each_node(qtbot) -> None:
    form = gui.CommandForm(
        {
            "cmd": "READ_TELEMETRY",
            "result": [{
                "name": "nodes",
                "label": "Узлы",
                "type": "array",
                "items": _telemetry_node_union(),
            }],
        },
        lambda *_args: None,
    )
    qtbot.addWidget(form)

    widget = form.result_widgets["nodes"]
    assert isinstance(widget, gui.ResultStructuredWidget)

    form.handle_response({
        "success": True,
        "result": {"nodes": [
            {"kind": 0, "time": 100},
            {"kind": 1, "event": 0, "cyc_idx": 42},
        ]},
    })
    texts = _label_texts(widget)
    assert "#1" in texts and "#2" in texts
    assert "Телеметрия" in texts and "Событие" in texts
    assert "Время" in texts
    assert "100" in texts
    assert "42" in texts
    assert "Начало цикла" in texts


def test_command_registry_receives_every_command_for_the_hint(
        qtbot, monkeypatch) -> None:
    class DemoCommandWidget(gui.CommandWidget):
        received_count = 0
        built = False

        @classmethod
        def validate_descriptors(
                cls, descriptors: list[dict[str, Any]]) -> None:
            super().validate_descriptors(descriptors)
            cls.received_count = len(descriptors)

        def build(self) -> None:
            type(self).built = True

    monkeypatch.setitem(gui.COMMAND_WIDGETS, "demo_commands", DemoCommandWidget)
    window = gui.MainWindow()
    qtbot.addWidget(window)
    window.descriptors = {
        f"DEMO_{index}": {
            "cmd": f"DEMO_{index}",
            "widget_hint": "demo_commands",
        }
        for index in range(5)
    }

    window._build_dynamic_tabs()
    assert DemoCommandWidget.received_count == 5
    assert DemoCommandWidget.built
    assert not window.forms


def test_special_dac_parameter_uses_synchronized_slider(qtbot) -> None:
    form = gui.CommandForm(
        {
            "cmd": "DAC_SET",
            "params": [{
                "name": "value",
                "type": "integer",
                "default": 100,
                "constraints": {"minimum": 0, "maximum": 4095},
                "widget_hint": "special_dac",
            }],
        },
        lambda *_args: None,
    )
    qtbot.addWidget(form)

    widget = form.param_widgets["value"]
    assert isinstance(widget, gui.SpecialDacParameterWidget)
    widget.slider.setValue(2048)
    assert widget.spinbox.value() == 2048
    assert form.parameters() == {"value": 2048}


def test_boolean_result_uses_checkbox(qtbot) -> None:
    label = gui.ResultBoolLabel()
    qtbot.addWidget(label)
    assert isinstance(label, gui.QCheckBox)
    label.setValue(True)
    assert label.checkState() == gui.Qt.Checked
    label.setValue(False)
    assert label.checkState() == gui.Qt.Unchecked
    label.setValue(None)
    assert label.checkState() == gui.Qt.PartiallyChecked


def test_special_pwm_widget_sends_unified_command(qtbot) -> None:
    requests: list[tuple[str, dict[str, Any]]] = []

    class FakeWindow:
        def send_request(self, command, params, callback):
            requests.append((command, params))
            callback({"success": True, "result": {}})
            return 1

    descriptor = {
        "cmd": "PWM_SET",
        "title": "Управление ШИМ",
        "params": [
            {
                "name": "channel", "type": "enum", "default": "PWM_A",
                "constraints": {"values": [
                    {"value": "PWM_A", "title": "PWM A"},
                    {"value": "PWM_B", "title": "PWM B"},
                ]},
            },
            {
                "name": "duty_cycle", "type": "integer", "default": 25,
                "constraints": {"minimum": 0, "maximum": 100},
            },
            {
                "name": "period_counter", "type": "integer", "default": 400,
                "constraints": {"minimum": 1, "maximum": 65535},
            },
        ],
    }
    gui.SpecialPwmCommandWidget.validate_descriptors([descriptor])
    command_widget = gui.SpecialPwmCommandWidget(FakeWindow(), [descriptor])
    panel = command_widget.create_widget(descriptor)
    assert panel is not None
    qtbot.addWidget(panel)
    rows = panel.findChildren(gui.QWidget, "pwmChannelRow")
    assert len(rows) == 2
    assert not panel.findChildren(gui.QComboBox)
    assert "Период счётчика (такты таймера)" in rows[0].findChildren(gui.QLabel)[2].text()
    rows[0].findChild(gui.QSpinBox, "pwmDutyValue").setValue(60)
    rows[0].findChild(gui.QSpinBox, "pwmPeriodValue").setValue(1000)
    qtbot.mouseClick(
        rows[0].findChild(gui.QPushButton, "pwmApplyButton"),
        gui.Qt.LeftButton,
    )

    assert requests == [("PWM_SET", {
        "channel": "PWM_A", "duty_cycle": 60, "period_counter": 1000,
    })]
