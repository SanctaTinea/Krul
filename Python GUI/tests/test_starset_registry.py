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


def test_boolean_result_uses_success_and_fail_labels(qtbot) -> None:
    label = gui.ResultBoolLabel()
    qtbot.addWidget(label)
    label.setValue(True)
    assert label.text() == "SUCCESS"
    assert "margin-right: 10px" in label.styleSheet()
    label.setValue(False)
    assert label.text() == "FAIL"
    assert "margin-right: 10px" in label.styleSheet()


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


def test_special_test_widget_shows_only_success_verdict(qtbot) -> None:
    requests: list[tuple[str, dict[str, Any]]] = []

    class FakeWindow:
        def send_request(self, command, params, callback):
            requests.append((command, params))
            callback({"success": True, "result": {
                "success": True, "summary": "ignored",
            }})
            return 1

    descriptor = {
        "cmd": "TEST_MEMORY", "title": "Memory", "description": "Connect J1.",
        "result": [
            {"name": "success", "type": "boolean"},
            {"name": "summary", "type": "string"},
        ],
    }
    gui.SpecialTestCommandWidget.validate_descriptors([descriptor])
    widget = gui.SpecialTestCommandWidget(FakeWindow(), [descriptor])
    panel = widget.create_widget(descriptor)
    assert panel is not None
    qtbot.addWidget(panel)

    description = panel.findChild(gui.QLabel, "testDescription")
    assert description is not None and description.text() == "Connect J1."
    assert not panel.findChildren(gui.QLineEdit)
    button = panel.findChild(gui.QPushButton, "testExecuteButton")
    result = panel.findChild(gui.ResultBoolLabel, "testResult")
    assert button.focusPolicy() == gui.Qt.NoFocus
    assert result.sizePolicy().horizontalPolicy() == gui.QSizePolicy.Expanding
    assert result.sizePolicy().verticalPolicy() == gui.QSizePolicy.Fixed
    qtbot.mouseClick(button, gui.Qt.LeftButton)

    assert requests == [("TEST_MEMORY", {})]
    assert result.text() == "SUCCESS"
