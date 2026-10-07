from __future__ import annotations

import pytest

from starset_widgets import IOPanel, SpecialGpioCommandWidget


@pytest.mark.parametrize("target", [None, 0, "KNUD"])
@pytest.mark.parametrize("category", [None, "", "  ", 12, {}])
def test_legacy_gpio_stays_flat_and_operational(qtbot, target, category):
    sent = []
    pins = [
        {"name": "BUTTON", "type": "IN", "state": 0},
        {"name": "LED", "type": "OUT", "state": 0},
    ]
    if category is not None:
        pins[1]["category"] = category
    panel = IOPanel(pins, lambda *args: sent.append(args) or 1, target=target)
    qtbot.addWidget(panel)
    assert not panel.output_categories
    assert panel.output_grid.cards == [panel.cards["LED"]]
    panel._set_one("LED", 1)
    assert sent[-1][1] == (
        {"pins": [{"name": "LED", "state": 1}]} if target is None else
        {"target": target, "name": "LED", "state": 1}
    )
    sent[-1][2]({"success": True, "result": {"name": "LED", "state": 1}})
    assert panel.cards["LED"].indicator.text.text() == "HIGH"
    panel.poll_outputs()
    assert sent[-1][1] == (
        {"pins": ["LED"]} if target is None else
        {"target": target, "name": "OUT"}
    )
    sent[-1][2]({"success": True, "result": {"pins": []}})
    panel._set_all(0)
    sent[-1][2]({"success": True, "result": {"name": "ALL", "state": 0}})
    assert panel.cards["LED"].indicator.text.text() == "LOW"


@pytest.mark.parametrize("target", [None, 0])
def test_categories_filter_and_actions_keep_all_outputs(qtbot, target):
    sent = []
    panel = IOPanel([
        {"name": "MSKA_SW_EN", "direction": "OUT", "state": 0, "category": "MSK"},
        {"name": "UART2_EN", "direction": "OUT", "state": 0, "category": " UART "},
        {"name": "UART3_EN", "direction": "OUT", "state": 1, "category": "UART"},
        {"name": "OTHER", "direction": "OUT", "state": 0},
        {"name": "BUTTON", "direction": "IN", "state": 0, "category": "Ignored"},
    ], lambda *args: sent.append(args) or 1, target=target, use_internal_scroll=False)
    qtbot.addWidget(panel)
    panel.resize(800, 600)
    panel.show()
    assert list(panel.output_categories) == ["MSK", "UART", "Прочие"]
    assert [c.name for c in panel.output_categories["UART"][1].cards] == [
        "UART2_EN", "UART3_EN",
    ]
    assert panel.input_grid.cards == [panel.cards["BUTTON"]]
    panel.filter_edit.setText("uart2")
    assert not panel.output_categories["UART"][0].isHidden()
    assert panel.output_categories["MSK"][0].isHidden()
    assert panel.output_categories["Прочие"][0].isHidden()
    assert panel.cards["UART3_EN"].isHidden()
    panel.poll_outputs()
    assert sent[-1][1] == (
        {"pins": ["MSKA_SW_EN", "OTHER", "UART2_EN", "UART3_EN"]}
        if target is None else {"target": target, "name": "OUT"}
    )
    # Poll replies from older firmware may omit categories.
    sent[-1][2]({"success": True, "result": {"pins": [
        {"name": "MSKA_SW_EN", "state": 1},
    ]}})
    assert panel.cards["MSKA_SW_EN"].indicator.text.text() == "HIGH"
    panel._set_one("UART2_EN", 1)
    sent[-1][2]({"success": True, "result": {"name": "UART2_EN", "state": 1}})
    assert panel.cards["UART2_EN"].indicator.text.text() == "HIGH"
    panel._set_all(0)
    assert sent[-1][1] == (
        {"pins": [{"name": "ALL", "state": 0}]} if target is None else
        {"target": target, "name": "ALL", "state": 0}
    )
    sent[-1][2]({"success": True, "result": {"name": "ALL", "state": 0}})
    assert all(c.indicator.text.text() == "LOW" for c in panel.cards.values())
    panel.filter_edit.clear()
    assert all(not box.isHidden() for box, _ in panel.output_categories.values())
    assert not panel.cards["UART3_EN"].isHidden()


def test_categories_preserve_numeric_wire_ids(qtbot):
    descriptor = {"result": [{"name": "pins", "type": "array", "items": {
        "type": "object", "fields": [
            {"name": "name", "type": "enum", "constraints": {"values": [
                {"value": 4, "title": "ENABLE"},
            ]}},
            {"name": "direction", "type": "enum", "constraints": {"values": [
                {"value": 1, "title": "Output"},
            ]}},
        ],
    }}]}
    widget = SpecialGpioCommandWidget.__new__(SpecialGpioCommandWidget)
    pins = widget._decode_pin_enums(descriptor, [
        {"name": 4, "direction": 1, "state": 0, "category": "Power"},
    ])
    sent = []
    panel = IOPanel(pins, lambda *args: sent.append(args) or 1, all_value=0)
    qtbot.addWidget(panel)
    assert panel.output_categories["Power"][1].cards == [panel.cards["ENABLE"]]
    panel._set_one(4, 1)
    assert sent[-1][1] == {"pins": [{"name": 4, "state": 1}]}
    sent[-1][2]({"success": True, "result": {"pins": [{"name": 4, "state": 1}]}})
    assert panel.cards["ENABLE"].indicator.text.text() == "HIGH"
    panel._set_all(0)
    assert sent[-1][1] == {"pins": [{"name": 0, "state": 0}]}
