from __future__ import annotations

import pytest

import Starset as gui
from starset_config import DeviceProfileStore


IDENTITY = {
    "protocol_version": 3,
    "device_name": "KRUL",
    "device_id": "UID-001",
    "firmware": "1.0.0",
}


def adc_descriptor() -> dict:
    return {
        "cmd": "ADC_PROFILE_TEST",
        "tab": "Telemetry",
        "group": "ADC",
        "result": [{
            "name": "value",
            "type": "integer",
            "widget_hint": "special_adc",
        }],
    }


def graph_descriptor() -> dict:
    return {
        "cmd": "MONITOR_PROFILE_TEST",
        "tab": "Telemetry",
        "group": "Power",
        "autoupdate": {"min_period": 100, "max_period": 2000},
        "result": [{"name": "voltage", "type": "float"}],
    }


def test_profile_store_is_stable_across_firmware_updates(tmp_path) -> None:
    path = tmp_path / "profiles.json"
    store = DeviceProfileStore(path)
    first_key = store.select_device(IDENTITY)
    store.set_section("widgets", {"ADC:value": {"scale_factor": 2.5}})
    store.flush()

    reloaded = DeviceProfileStore(path)
    updated_identity = dict(IDENTITY, firmware="2.0.0")
    assert reloaded.select_device(updated_identity) == first_key
    assert reloaded.section("widgets")["ADC:value"]["scale_factor"] == 2.5

    other_identity = dict(updated_identity, device_id="UID-002")
    assert reloaded.select_device(other_identity) != first_key
    assert reloaded.section("widgets") == {}


def test_identity_without_device_id_uses_model_profile(tmp_path) -> None:
    store = DeviceProfileStore(tmp_path / "profiles.json")
    first = store.select_device({
        "protocol_version": 3, "device_name": "LEGACY", "firmware": "1.0",
    })
    second = store.select_device({
        "protocol_version": 3, "device_name": "LEGACY", "firmware": "2.0",
    })
    assert first == second == "model:LEGACY:protocol:3"


def test_adc_profile_restores_and_synchronizes_duplicate_forms(qtbot, tmp_path) -> None:
    store = DeviceProfileStore(tmp_path / "profiles.json")
    store.select_device(IDENTITY)
    store.set_section("widgets", {
        "ADC_PROFILE_TEST:value": {
            "correction_coefficient": 2.5,
        }
    })
    window = gui.MainWindow(profile_store=store)
    qtbot.addWidget(window)
    window.descriptors = {"ADC_PROFILE_TEST": adc_descriptor()}
    window._build_dynamic_tabs()

    widgets = [
        form.result_widgets["value"]
        for form in window.forms
        if form.command == "ADC_PROFILE_TEST"
    ]
    assert len(widgets) == 2
    assert all(widget.correction_coefficient.value() == pytest.approx(2.5)
               for widget in widgets)

    widgets[0].correction_coefficient.setValue(3.5)
    assert widgets[1].correction_coefficient.value() == pytest.approx(3.5)
    saved = store.section("widgets")["ADC_PROFILE_TEST:value"]
    assert saved["correction_coefficient"] == pytest.approx(3.5)


def test_graph_profile_restores_and_updates_store(qtbot, tmp_path) -> None:
    store = DeviceProfileStore(tmp_path / "profiles.json")
    store.select_device(IDENTITY)
    key = "MONITOR_PROFILE_TEST:voltage"
    store.set_section("graphs", {
        "mode": "time",
        "period_ms": 750,
        "history_s": 120,
        "auto_poll": False,
        "show_points": True,
        "line_style": "dash",
        "selected": [key],
        "transforms": {key: {"multiplier": 10.0, "base": 1.5}},
    })
    window = gui.MainWindow(profile_store=store)
    qtbot.addWidget(window)
    descriptor = graph_descriptor()
    window.descriptors = {descriptor["cmd"]: descriptor}
    window._show_graph_plot()
    graph = window.graph_window
    assert graph is not None

    assert graph.selected_keys == {key}
    assert graph.transforms[key] == pytest.approx((10.0, 1.5))
    assert graph.period_spin.value() == 750
    assert graph.history_spin.value() == 120
    assert graph.points_check.isChecked()
    assert graph.canvas.line_style == "dash"

    multiplier, _base = graph._transform_widgets[key]
    multiplier.setValue(4.0)
    assert store.section("graphs")["transforms"][key]["multiplier"] == 4.0


def test_adc_single_coefficient_setting_persists_and_applies(qtbot, tmp_path) -> None:
    path = tmp_path / "profiles.json"
    store = DeviceProfileStore(path)
    window = gui.MainWindow(profile_store=store)
    qtbot.addWidget(window)

    assert window.adc_single_coefficient_action.isChecked()
    assert "Использовать один коэффициент для АЦП" in (
        window.adc_single_coefficient_action.text()
    )
    assert not window.adc_mode_button.icon().isNull()

    window.descriptors = {"ADC_PROFILE_TEST": adc_descriptor()}
    window._build_dynamic_tabs()
    widgets = [
        form.result_widgets["value"]
        for form in window.forms
        if form.command == "ADC_PROFILE_TEST"
    ]
    assert widgets
    assert all(not widget.single_controls.isHidden() for widget in widgets)
    assert all(widget.legacy_controls.isHidden() for widget in widgets)

    window.adc_single_coefficient_action.setChecked(False)
    assert all(widget.single_controls.isHidden() for widget in widgets)
    assert all(not widget.legacy_controls.isHidden() for widget in widgets)

    store.flush()
    reloaded = DeviceProfileStore(path)
    assert reloaded.settings()["adc_single_coefficient"] is False

    restored = gui.MainWindow(profile_store=reloaded)
    qtbot.addWidget(restored)
    assert not restored.adc_single_coefficient_action.isChecked()
