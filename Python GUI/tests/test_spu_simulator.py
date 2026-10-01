from __future__ import annotations

import krul_wire
import Starset as gui
from krul_wire import (
    FORMAT_CBOR,
    FORMAT_JSON,
    FrameParser,
    decode_payload,
    encode_frame,
    encode_payload,
)
from spu_simulator import SPU_DESCRIPTORS, SpuSimulator


def request(simulator: SpuSimulator, command: str, transaction: int,
            params: dict | None = None) -> dict:
    payload = {"cmd": command, "id": transaction}
    if params is not None:
        payload["params"] = params
    return simulator.dispatch(payload).response


def test_identity_and_command_list() -> None:
    simulator = SpuSimulator()

    identity = request(simulator, "WHOAMI", 1)["result"]
    assert identity["protocol_version"] == 4
    assert identity["device_name"] == "СПУ"
    assert identity["firmware"] == "3.0.0"

    names = request(simulator, "CMD_LIST", 2)["result"]["cmd_name"]
    assert {"READ_TELEMETRY", "SPU_STATUS", "PIN_READ", "RS_WRITE"} <= set(names)

    assert request(simulator, "PING", 9) == {"id": 9, "success": True}


def test_describe_exposes_union_schema_without_widget_hint() -> None:
    simulator = SpuSimulator()
    descriptor = request(simulator, "DESCRIBE", 1,
                         {"name": "READ_TELEMETRY"})["result"]

    assert descriptor["tab"] == "СПУ и телеметрия"
    assert "widget_hint" not in descriptor

    nodes = descriptor["result"][0]
    assert nodes["name"] == "nodes"
    assert nodes["type"] == "array"

    union = nodes["items"]
    assert union["type"] == "union"
    assert union["tag"]["name"] == "discrim"
    assert [variant["value"] for variant in union["variants"]] == [0, 1]
    titles = [item["title"] for item in union["tag"]["constraints"]["values"]]
    assert titles == ["Телеметрия", "Событие"]

    telemetry_fields = {field["name"]
                        for field in union["variants"][0]["fields"]}
    event_fields = {field["name"] for field in union["variants"][1]["fields"]}
    assert {"time", "vin", "tmp116_temp5", "gpio_flags"} <= telemetry_fields
    assert {"event_kind", "cyc_idx", "voltage", "current"} <= event_fields


def test_read_telemetry_returns_tagged_union_nodes() -> None:
    simulator = SpuSimulator()
    for index in range(7):
        request(simulator, "READ_TELEMETRY", index + 1, {"count": 1})

    response = request(simulator, "READ_TELEMETRY", 100, {"count": 6})
    assert response["success"]
    nodes = response["result"]["nodes"]
    assert len(nodes) == 6
    assert {node["discrim"] for node in nodes} <= {0, 1}
    assert any(node["discrim"] == 0 for node in nodes)
    assert any(node["discrim"] == 1 for node in nodes)

    telemetry = next(node for node in nodes if node["discrim"] == 0)
    assert {"time", "vin", "tmp116_temp5", "gpio_flags"} <= set(telemetry)
    event = next(node for node in nodes if node["discrim"] == 1)
    assert {"event_kind", "cyc_idx", "voltage", "current"} <= set(event)


def test_stateful_spu_status_and_pin_round_trip() -> None:
    simulator = SpuSimulator()

    before = request(simulator, "SPU_STATUS", 1)["result"]
    assert before["state"] == 0 and before["program_start_active"] is False

    request(simulator, "RUN_SPU_CMD", 2, {"command": 3})
    running = request(simulator, "SPU_STATUS", 3)["result"]
    assert running["state"] == 1 and running["program_start_active"] is True

    request(simulator, "RUN_SPU_CMD", 4, {"command": 4})
    restarted = request(simulator, "SPU_STATUS", 5)["result"]
    assert restarted["state"] == 2 and restarted["restart_count"] == 1

    pins = request(simulator, "PIN_READ", 6,
                   {"pins": ["EN_CH1"]})["result"]["pins"]
    assert pins == [{"name": "EN_CH1", "type": "OUT", "state": 0}]
    request(simulator, "PIN_SET", 7,
            {"pins": [{"name": "EN_CH1", "state": 1}]})
    pins = request(simulator, "PIN_READ", 8,
                   {"pins": ["EN_CH1"]})["result"]["pins"]
    assert pins[0]["state"] == 1


def test_monitoring_commands_return_expected_shapes() -> None:
    simulator = SpuSimulator()

    adc = request(simulator, "ADC_READ", 1)["result"]
    assert set(adc) == {"adc1", "adc3"}
    assert "ADC_CVOUT_CH1_HALL" in adc["adc1"]
    assert "REF_OUT" in adc["adc3"]

    temperature = request(simulator, "TEMP_READ", 2)["result"]["temperature"]
    assert set(temperature) == {"temp1", "temp2", "temp3", "temp4", "temp5"}

    ina = request(simulator, "INA_READ", 3)["result"]
    assert set(ina) == {"currents", "powers", "shunt_voltages", "bus_voltages"}

    anode = request(simulator, "GET_ANODE_SETTINGS", 4)["result"]
    assert anode["normal_dac_level"] == 2350

    recording = request(simulator, "GET_TELEMETRY_RECORDING_STATE", 5)["result"]
    assert recording == {"enabled": False, "period_ms": 1000}


def test_validation_reports_protocol_errors() -> None:
    simulator = SpuSimulator()

    out_of_range = request(simulator, "READ_TELEMETRY", 1, {"count": 99})
    assert out_of_range["success"] is False
    assert out_of_range["error"]["code"] == 3

    unknown_pin = request(simulator, "PIN_READ", 2, {"pins": ["NOPE"]})
    assert unknown_pin["error"]["code"] == 3

    missing_field = request(simulator, "PIN_SET", 3, {"pins": []})
    assert missing_field["error"]["code"] == 1

    unknown_command = request(simulator, "NO_SUCH_COMMAND", 4)
    assert unknown_command["error"]["code"] == 6


def test_cbor_round_trip_decodes_union_field_names() -> None:
    simulator = SpuSimulator()
    client = FrameParser()

    def exchange(payload: dict) -> dict:
        request_frames, errors = FrameParser().feed(
            encode_frame(payload, FORMAT_CBOR))
        assert not errors and len(request_frames) == 1
        decoded_request = decode_payload(request_frames[0].payload, FORMAT_CBOR)
        response = simulator.dispatch(decoded_request).response
        frames, errors = client.feed(encode_frame(response, FORMAT_CBOR))
        assert not errors and len(frames) == 1
        return decode_payload(frames[0].payload, FORMAT_CBOR)

    exchange({"id": 1, "cmd": "DESCRIBE",
              "params": {"name": "READ_TELEMETRY"}})
    for index in range(6):
        simulator.dispatch({"id": 10 + index, "cmd": "READ_TELEMETRY",
                            "params": {"count": 1}})

    decoded = exchange({"id": 2, "cmd": "READ_TELEMETRY",
                        "params": {"count": 4}})
    assert decoded["success"]
    for node in decoded["result"]["nodes"]:
        assert node["discrim"] in (0, 1)
        assert "time" in node
        if node["discrim"] == 0:
            assert "vin" in node
        else:
            assert "event_kind" in node


def _fresh_protocol_tables() -> tuple[dict[int, str], dict[str, int]]:
    tags = {krul_wire.cbor_key_tag(name): name
            for name in krul_wire._CBOR_PROTOCOL_KEYS}
    names = {name: krul_wire.cbor_key_tag(name)
             for name in krul_wire._CBOR_PROTOCOL_KEYS}
    return tags, names


def _wire_describe(simulator: SpuSimulator, command: str) -> bytes:
    response = simulator.dispatch({"id": 1, "cmd": "DESCRIBE",
                                   "params": {"name": command}}).response
    return encode_frame(response, FORMAT_CBOR)


def test_describe_over_wire_keeps_union_variants(monkeypatch) -> None:
    simulator = SpuSimulator()
    frame = _wire_describe(simulator, "READ_TELEMETRY")

    # Emulate a fresh GUI process: only static protocol names are known.
    tags, names = _fresh_protocol_tables()
    monkeypatch.setattr(krul_wire, "_CBOR_TAG_NAMES", tags)
    monkeypatch.setattr(krul_wire, "_CBOR_NAME_TAGS", names)

    frames, errors = FrameParser().feed(frame)
    assert not errors and len(frames) == 1
    descriptor = decode_payload(frames[0].payload, FORMAT_CBOR)["result"]

    union = descriptor["result"][0]["items"]
    assert union["type"] == "union"
    assert "variants" in union
    assert [variant["value"] for variant in union["variants"]] == [0, 1]
    assert any(field["name"] == "vin"
               for field in union["variants"][0]["fields"])


def test_wire_descriptor_renders_union_variant_fields(qtbot,
                                                      monkeypatch) -> None:
    simulator = SpuSimulator()
    frame = _wire_describe(simulator, "READ_TELEMETRY")

    tags, names = _fresh_protocol_tables()
    monkeypatch.setattr(krul_wire, "_CBOR_TAG_NAMES", tags)
    monkeypatch.setattr(krul_wire, "_CBOR_NAME_TAGS", names)

    frames, errors = FrameParser().feed(frame)
    assert not errors and len(frames) == 1
    descriptor = decode_payload(frames[0].payload, FORMAT_CBOR)["result"]

    form = gui.CommandForm(descriptor, lambda *_args: None)
    qtbot.addWidget(form)
    widget = form.result_widgets["nodes"]
    assert isinstance(widget, gui.ResultStructuredWidget)

    form.handle_response({"success": True, "result": {"nodes": [
        {"discrim": 1, "event_kind": 0, "time": 200, "cyc_idx": 3,
         "voltage": 0, "current": 0},
    ]}})
    texts = [label.text() for label in widget.findChildren(gui.QLabel)]
    assert "Событие" in texts            # tag title is rendered
    assert "Номер циклограммы" in texts  # ...and the variant's own fields
    assert "Начало циклограммы" in texts


def test_unsigned_param_uses_spinbox_and_sends_integer(qtbot) -> None:
    form = gui.CommandForm(SPU_DESCRIPTORS["READ_TELEMETRY"],
                           lambda *_args: None)
    qtbot.addWidget(form)

    widget = form.param_widgets["count"]
    assert isinstance(widget, gui.QSpinBox)
    widget.setValue(5)

    params = form.parameters()
    assert params == {"count": 5}
    assert isinstance(params["count"], int)


def test_form_parameters_round_trip_through_json(qtbot) -> None:
    form = gui.CommandForm(SPU_DESCRIPTORS["READ_TELEMETRY"],
                           lambda *_args: None)
    qtbot.addWidget(form)

    request = {"id": 1, "cmd": "READ_TELEMETRY", "params": form.parameters()}
    decoded = decode_payload(encode_payload(request, FORMAT_JSON), FORMAT_JSON)
    response = SpuSimulator().dispatch(decoded).response

    assert response["success"], response
    assert "nodes" in response["result"]


def test_read_telemetry_renders_in_command_form(qtbot) -> None:
    form = gui.CommandForm(SPU_DESCRIPTORS["READ_TELEMETRY"],
                           lambda *_args: None)
    qtbot.addWidget(form)

    widget = form.result_widgets["nodes"]
    assert isinstance(widget, gui.ResultStructuredWidget)

    form.handle_response({
        "success": True,
        "result": {"nodes": [
            {"discrim": 0, "time": 100, "spu_command": 0, "spu_state": 1,
             "ch5_state": 1, "vin": 27000, "in_spu": 1, "iin_spu1": 2,
             "iin_spu2": 3, "tmp116_temp1": 4, "tmp116_temp2": 5,
             "tmp116_temp3": 6, "tmp116_temp4": 7, "tmp116_temp5": 8,
             "i_ch1_in": 9, "i_ch1_out": 10, "i_ch2_in": 11, "i_ch2_out": 12,
             "i_ch5_in": 13, "i_ch5_out": 14, "u_ch3": 15, "u_ch4": 16,
             "gpio_flags": 17, "fpf_flags": 18, "alert_flags": 19},
            {"discrim": 1, "event_kind": 0, "time": 200, "cyc_idx": 3,
             "voltage": 0, "current": 0},
        ]},
    })

    texts = [label.text() for label in widget.findChildren(gui.QLabel)]
    assert "#1" in texts and "#2" in texts
    assert "Телеметрия" in texts and "Событие" in texts
    assert "Vin" in texts
    assert "Номер циклограммы" in texts
