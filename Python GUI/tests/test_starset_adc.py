from __future__ import annotations

import pytest

import Starset as gui
from krul_simulator import KrulSimulator


def test_special_adc_result_scaling_and_editable_controls(qtbot) -> None:
    descriptor = {
        "cmd": "ADC_READ",
        "title": "ADC",
        "params": [],
        "result": [
            {
                "name": "adc",
                "label": "ADC",
                "type": "integer",
                "widget_hint": "special_adc",
            }
        ],
    }
    form = gui.CommandForm(descriptor, lambda *_args: None)
    qtbot.addWidget(form)

    output = form.result_widgets["adc"]
    assert isinstance(output, gui.SpecialAdcResultWidget)
    assert output.correction_coefficient.value() == pytest.approx(1.0)

    form.handle_response({"success": True, "result": {"adc": 2048}})
    assert output.raw_output.text() == "2048"
    assert float(output.scaled_output.text()) == pytest.approx(2048)
    assert "font-weight: 400" in output.raw_output.styleSheet()
    assert "font-weight: 600" in output.scaled_output.styleSheet()

    output.correction_coefficient.setValue(2.5)
    assert float(output.scaled_output.text()) == pytest.approx(2048 * 2.5)


def test_special_adc_legacy_mode_restores_four_controls(qtbot) -> None:
    descriptor = {
        "cmd": "ADC_READ",
        "title": "ADC",
        "params": [],
        "result": [
            {
                "name": "adc",
                "label": "ADC",
                "type": "integer",
                "widget_hint": "special_adc",
            }
        ],
    }
    form = gui.CommandForm(descriptor, lambda *_args: None)
    qtbot.addWidget(form)
    output = form.result_widgets["adc"]

    assert not output.single_controls.isHidden()
    assert output.legacy_controls.isHidden()

    form.handle_response({"success": True, "result": {"adc": 2048}})
    output.set_single_coefficient(False)
    assert output.single_controls.isHidden()
    assert not output.legacy_controls.isHidden()

    output.reference_voltage.setValue(3.3)
    output.scale_factor.setValue(1.0)
    output.base_voltage.setValue(0.5)
    output.resolution_bits.setValue(12)
    full_scale = (1 << 12) - 1
    assert float(output.scaled_output.text()) == pytest.approx(
        2048 * 3.3 / full_scale + 0.5
    )
    assert output.scaled_output.toolTip() != "RAW × коэффициент"

    output.set_single_coefficient(True)
    assert float(output.scaled_output.text()) == pytest.approx(2048)


def test_special_adc_group_scales_all_integer_fields_together(qtbot) -> None:
    descriptor = {
        "cmd": "ADC_READ_BY_GROUP",
        "title": "ADC group",
        "params": [],
        "result": [
            {
                "name": "voltage",
                "label": "Voltage",
                "type": "object",
                "widget_hint": "special_adc_group",
                "fields": [
                    {"name": "ain0", "label": "AIN0", "type": "integer"},
                    {"name": "ain1", "label": "AIN1", "type": "integer"},
                ],
            }
        ],
    }
    form = gui.CommandForm(descriptor, lambda *_args: None)
    qtbot.addWidget(form)

    output = form.result_widgets["voltage"]
    assert isinstance(output, gui.SpecialAdcGroupResultWidget)
    assert output.correction_coefficient.value() == pytest.approx(1.0)
    group_label = next(
        label for label in form.findChildren(gui.QLabel)
        if label.text() == "Voltage"
    )
    assert group_label.font().bold()
    assert output.layout().contentsMargins().left() == 12
    assert output.layout().contentsMargins().top() == 8
    assert output.layout().contentsMargins().bottom() == 8

    form.handle_response(
        {
            "success": True,
            "result": {"voltage": {"ain0": 1024, "ain1": 2048}},
        }
    )
    assert output.raw_outputs["ain0"].text() == "1024"
    assert output.raw_outputs["ain1"].text() == "2048"
    assert "font-weight: 400" in output.raw_outputs["ain0"].styleSheet()
    assert "font-weight: 600" in output.scaled_outputs["ain0"].styleSheet()
    assert float(output.scaled_outputs["ain0"].text()) == pytest.approx(1024)
    assert float(output.scaled_outputs["ain1"].text()) == pytest.approx(2048)

    output.correction_coefficient.setValue(2.5)
    assert float(output.scaled_outputs["ain0"].text()) == pytest.approx(
        1024 * 2.5
    )
    assert float(output.scaled_outputs["ain1"].text()) == pytest.approx(
        2048 * 2.5
    )

    simulated = KrulSimulator().dispatch(
        {"cmd": "ADC_READ_BY_GROUP", "id": 1}
    ).response
    assert simulated["success"] is True
    assert set(simulated["result"]["Voltage"]) == {
        "value_AIN0",
        "value_AIN1",
        "value_AIN2",
        "value_AIN3",
    }


def test_special_adc_group_legacy_mode_scales_with_reference(qtbot) -> None:
    descriptor = {
        "cmd": "ADC_READ_BY_GROUP",
        "title": "ADC group",
        "params": [],
        "result": [
            {
                "name": "voltage",
                "label": "Voltage",
                "type": "object",
                "widget_hint": "special_adc_group",
                "fields": [
                    {"name": "ain0", "label": "AIN0", "type": "integer"},
                ],
            }
        ],
    }
    form = gui.CommandForm(descriptor, lambda *_args: None)
    qtbot.addWidget(form)
    output = form.result_widgets["voltage"]

    assert not output.single_controls.isHidden()
    assert output.legacy_controls.isHidden()

    form.handle_response(
        {"success": True, "result": {"voltage": {"ain0": 4095}}}
    )
    output.set_single_coefficient(False)
    output.reference_voltage.setValue(3.3)
    output.scale_factor.setValue(2.0)
    output.base_voltage.setValue(-1.0)
    output.resolution_bits.setValue(12)
    full_scale = (1 << 12) - 1
    assert float(output.scaled_outputs["ain0"].text()) == pytest.approx(
        4095 * 3.3 * 2.0 / full_scale - 1.0
    )
