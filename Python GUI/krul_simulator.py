#!/usr/bin/env python3
"""Stateful Krul v4 simulator for GUI development without an STM32 board."""

from __future__ import annotations

import argparse
import json
import socketserver
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, BinaryIO, Callable

from krul_wire import FrameParser, cbor_key_tag, decode_payload, encode_frame


def _enum_field(name: str | None, label: str | None,
                values: list[str]) -> dict[str, Any]:
    field: dict[str, Any] = {
        "type": "enum",
        "constraints": {
            "values": [{"value": index, "title": value}
                       for index, value in enumerate(values)]
        },
    }
    if name is not None:
        field["name"] = name
    if label is not None:
        field["label"] = label
    return field


def _enum_result(name: str, label: str,
                 values: list[str]) -> dict[str, Any]:
    """Build an enum result field with the same schema as enum parameters."""

    return _enum_field(name, label, values)


def _autoupdate(default_period: int = 500) -> dict[str, int]:
    return {
        "min_period": 100,
        "max_period": 5000,
        "default_period": default_period,
    }


SQUARE_WAVE_PIN = "SQUARE_2S"
SQUARE_WAVE_PERIOD_S = 2.0
PIN_NAMES = [
    "BUTTON",
    "FAULT",
    SQUARE_WAVE_PIN,
    "LED_GREEN",
    "LED_YELLOW",
    "LED_RED",
    "RELAY",
]
OUTPUT_PINS = ["LED_GREEN", "RELAY", "LED_YELLOW", "LED_RED"]
PIN_DIRECTIONS = {
    "BUTTON": "IN",
    "FAULT": "IN",
    SQUARE_WAVE_PIN: "IN",
    "LED_GREEN": "OUT",
    "RELAY": "OUT",
    "LED_YELLOW": "OUT",
    "LED_RED": "OUT",
}

PIN_RESULT_ITEM = {
    "type": "object",
    "fields": [
        _enum_field("name", None, PIN_NAMES),
        _enum_field("type", None, ["IN", "OUT"]),
        {
            "name": "state",
            "label": "Состояние",
            "type": "integer",
            "constraints": {"minimum": 0, "maximum": 1},
        },
    ],
}

PIN_SET_ITEM = {
    "type": "object",
    "fields": [
        _enum_field("name", None, ["ALL", *OUTPUT_PINS]),
        {
            "name": "state",
            "label": "Состояние",
            "type": "integer",
            "constraints": {"minimum": 0, "maximum": 1},
        },
    ],
}


def _descriptors() -> dict[str, dict[str, Any]]:
    builtins = {
        "PING": {"cmd": "PING", "builtin": True, "title": "PING"},
        "WHOAMI": {"cmd": "WHOAMI", "builtin": True, "title": "WHOAMI"},
        "CMD_LIST": {"cmd": "CMD_LIST", "builtin": True, "title": "CMD_LIST"},
        "DESCRIBE": {
            "cmd": "DESCRIBE",
            "builtin": True,
            "title": "DESCRIBE",
            "params": [{
                "name": "name",
                "label": "Команда",
                "type": "string",
                "constraints": {"minLength": 1, "maxLength": 47},
            }],
        },
        "PIN_GET": {
            "cmd": "PIN_GET",
            "title": "PIN_GET",
            "widget_hint": "special_gpio",
            "params": [{
                "name": "pins",
                "label": "Выводы",
                "type": "array",
                "default": [],
                "constraints": {"minItems": 0, "maxItems": len(PIN_NAMES)},
                "items": _enum_field(None, None, PIN_NAMES),
            }],
            "result": [{
                "name": "pins",
                "type": "array",
                "constraints": {"minItems": 0, "maxItems": len(PIN_NAMES)},
                "items": PIN_RESULT_ITEM,
            }],
        },
        "PIN_SET": {
            "cmd": "PIN_SET",
            "title": "PIN_SET",
            "widget_hint": "special_gpio",
            "params": [{
                "name": "pins",
                "type": "array",
                "constraints": {"minItems": 1, "maxItems": len(OUTPUT_PINS)},
                "items": PIN_SET_ITEM,
            }],
            "result": [{
                "name": "pins",
                "type": "array",
                "constraints": {"minItems": 1, "maxItems": len(OUTPUT_PINS)},
                "items": PIN_SET_ITEM,
            }],
        },
    }
    normal: dict[str, dict[str, Any]] = {
        "ECHO": {
            "cmd": "ECHO",
            "tab": "PC simulator",
            "title": "Echo",
            "description": "Type something; the virtual rabbit will echo it.",
            "group": "Protocol",
            "order": 10,
            "params": [
                {
                    "name": "text",
                    "label": "Text",
                    "type": "string",
                    "constraints": {"minLength": 1, "maxLength": 128},
                },
                {
                    "name": "count",
                    "label": "Count",
                    "type": "integer",
                    "default": 1,
                    "constraints": {"minimum": 1, "maximum": 10},
                },
            ],
            "result": [{
                "name": "text",
                "label": "Result",
                "type": "string",
                "constraints": {"minLength": 0, "maxLength": 1280},
            }],
        },
        "ADC_READ": {
            "cmd": "ADC_READ",
            "tab": "Виджеты",
            "title": "АЦП по каналам",
            "description": "Отдельный special_adc для каждого канала.",
            "group": "Специальные виджеты",
            "order": 10,
            "result": [
                {
                    "name": f"value_AIN{index}",
                    "label": f"AIN{index}",
                    "type": "integer",
                    "widget_hint": "special_adc",
                }
                for index in range(4)
            ],
            "autoupdate": _autoupdate(),
        },
        "ADC_READ_BY_GROUP": {
            "cmd": "ADC_READ_BY_GROUP",
            "tab": "Виджеты",
            "title": "Сгруппированный АЦП",
            "group": "Специальные виджеты",
            "order": 21,
            "result": [
                {
                    "name": "Voltage",
                    "label": "Напряжение",
                    "type": "object",
                    "widget_hint": "special_adc_group",
                    "fields": [
                        {"name": "value_AIN0", "label": "value_AIN0", "type": "integer"},
                        {"name": "value_AIN1", "label": "value_AIN1", "type": "integer"},
                        {"name": "value_AIN2", "label": "value_AIN2", "type": "integer"},
                        {"name": "value_AIN3", "label": "value_AIN3", "type": "integer"},
                    ]
                },
                {
                    "name": "Current",
                    "label": "Токи",
                    "type": "object",
                    "widget_hint": "special_adc_group",
                    "fields": [
                        {"name": "value_AIN4", "label": "value_AIN4", "type": "integer"},
                        {"name": "value_AIN5", "label": "value_AIN5", "type": "integer"},
                        {"name": "value_AIN6", "label": "value_AIN6", "type": "integer"},
                        {"name": "value_AIN7", "label": "value_AIN7", "type": "integer"},
                    ]
                },
                {
                    "name": "Temp",
                    "label": "температура",
                    "type": "object",
                    "widget_hint": "special_adc_group",
                    "fields": [
                        {"name": "value_AIN8", "label": "value_AIN8", "type": "integer"},
                        {"name": "value_AIN9", "label": "value_AIN9", "type": "integer"},
                    ]
                },
                {"name": "value_AIN10", "label": "AIN10", "type": "integer", "widget_hint": "special_adc"},
                {"name": "value_AIN11", "label": "AIN10", "type": "integer", "widget_hint": "special_adc"},
                {"name": "value_AIN12", "label": "AIN12", "type": "integer", "widget_hint": "special_adc"},
            ],
            "autoupdate": _autoupdate(),
        },
        "DAC_SET": {
            "cmd": "DAC_SET",
            "tab": "Виджеты",
            "title": "Виртуальный ЦАП",
            "description": "Ползунок и числовое поле special_dac синхронизированы.",
            "group": "Специальные виджеты",
            "order": 30,
            "params": [{
                "name": "value",
                "label": "Код ЦАП",
                "type": "integer",
                "default": 2048,
                "constraints": {"minimum": 0, "maximum": 4095},
                "widget_hint": "special_dac",
            }],
            "result": [
                {"name": "value", "label": "Установлено", "type": "integer"},
                {"name": "voltage", "label": "Напряжение, В", "type": "float"},
            ],
        },
        "PWM_SET": {
            "cmd": "PWM_SET",
            "tab": "Виджеты",
            "title": "Управление ШИМ",
            "description": "Полноширинный special_pwm с несколькими каналами.",
            "group": "Специальные виджеты",
            "order": 40,
            "widget_hint": "special_pwm",
            "params": [
                {
                    **_enum_field(
                        "channel", "Канал",
                        ["PWM_CH1", "PWM_CH2", "PWM_CH3", "PWM_CH4"],
                    ),
                    "default": 0,
                },
                {
                    "name": "duty_cycle",
                    "label": "Скважность, %",
                    "type": "integer",
                    "default": 25,
                    "constraints": {"minimum": 0, "maximum": 100},
                },
                {
                    "name": "period_counter",
                    "label": "Период",
                    "type": "integer",
                    "default": 1000,
                    "constraints": {"minimum": 1, "maximum": 1000000},
                },
            ],
            "result": [
                {"name": "applied", "label": "Применено", "type": "boolean"}
            ],
        },
        "WIDGET_GALLERY": {
            "cmd": "WIDGET_GALLERY",
            "tab": "Виджеты",
            "title": "Стандартные поля",
            "description": "Все встроенные типы параметров и результатов в одной карточке.",
            "group": "Стандартные виджеты",
            "order": 10,
            "params": [
                {
                    "name": "text", "label": "Строка", "type": "string",
                    "default": "Krul simulator",
                    "constraints": {"minLength": 1, "maxLength": 80},
                },
                {
                    "name": "integer", "label": "Целое", "type": "integer",
                    "default": 42,
                    "constraints": {"minimum": -1000, "maximum": 1000},
                },
                {
                    "name": "floating", "label": "Дробное", "type": "float",
                    "default": 3.3,
                    "constraints": {"minimum": -100.0, "maximum": 100.0, "step": 0.05},
                },
                {"name": "enabled", "label": "Флаг", "type": "boolean", "default": True},
                {
                    **_enum_field("mode", "Список", ["standby", "manual", "auto"]),
                    "default": 2,
                },
            ],
            "result": [
                {"name": "text", "label": "Строка", "type": "string"},
                {"name": "integer", "label": "Целое", "type": "integer"},
                {"name": "floating", "label": "Дробное", "type": "float"},
                {"name": "enabled", "label": "Флаг", "type": "boolean"},
                _enum_result("mode", "Режим", ["standby", "manual", "auto"]),
                {
                    "name": "terminal", "label": "Терминал", "type": "console_string",
                    "constraints": {"severity": "info"},
                },
            ],
        },
        "PIN_GET_V2": {
            "cmd": "PIN_GET_V2",
            "tab": "GPIOA",
            "title": "Входы V2",
            "order": 100,
            "widget_hint": "special_gpio",
            "params": [{
                "name": "pins",
                "label": "Входы",
                "type": "array",
                "default": [],
                "constraints": {"minItems": 0, "maxItems": len(PIN_NAMES)},
                "items": _enum_field(None, None, PIN_NAMES),
            }],
            "result": [{
                "name": "pins",
                "type": "array",
                "constraints": {"minItems": 0, "maxItems": len(PIN_NAMES)},
                "items": PIN_RESULT_ITEM,
            }],
        },
        "LOG_EMIT": {
            "cmd": "LOG_EMIT",
            "tab": "PC simulator",
            "title": "Emit log event",
            "group": "Protocol",
            "order": 30,
            "params": [
                {
                    **_enum_field("severity", "Severity",
                                  ["debug", "info", "warning", "error"]),
                    "default": 1,
                },
                {
                    "name": "message",
                    "label": "Message",
                    "type": "string",
                    "default": "Hello from simulator",
                    "constraints": {"minLength": 0, "maxLength": 256},
                },
            ],
            "result": [{"name": "queued", "label": "Queued", "type": "boolean"}],
        },
        "DELAY": {
            "cmd": "DELAY",
            "tab": "PC simulator",
            "title": "Delayed response",
            "group": "Fault injection",
            "order": 40,
            "params": [{
                "name": "milliseconds",
                "label": "Delay, ms",
                "type": "integer",
                "default": 250,
                "constraints": {"minimum": 0, "maximum": 5000},
            }],
            "result": [{"name": "milliseconds", "type": "integer"}],
            "timeout_ms": 5000,
        },
        "DELAYED_ECHO": {
            "cmd": "DELAYED_ECHO",
            "tab": "Отложенные команды",
            "title": "Отложенное эхо",
            "description": "Ответ приходит после заданной паузы.",
            "group": "Ожидание ответа",
            "order": 10,
            "params": [
                {
                    "name": "text", "label": "Текст", "type": "string",
                    "default": "Ответ получен",
                    "constraints": {"minLength": 1, "maxLength": 128},
                },
                {
                    "name": "milliseconds", "label": "Задержка, мс", "type": "integer",
                    "default": 700,
                    "constraints": {"minimum": 0, "maximum": 5000},
                },
            ],
            "result": [
                {"name": "text", "label": "Ответ", "type": "string"},
                {"name": "milliseconds", "label": "Задержка, мс", "type": "integer"},
            ],
            "timeout_ms": 5000,
        },
        "SELF_TEST": {
            "cmd": "SELF_TEST",
            "tab": "Отложенные команды",
            "title": "Самодиагностика",
            "description": "Имитирует длительную аппаратную проверку.",
            "group": "Ожидание ответа",
            "order": 20,
            "params": [{
                **_enum_field("scope", "Область", ["quick", "memory", "full"]),
                "default": 0,
            }],
            "result": [
                {"name": "passed", "label": "Результат", "type": "boolean"},
                _enum_result("scope", "Проверено", ["quick", "memory", "full"]),
                {"name": "details", "label": "Подробности", "type": "string"},
            ],
            "timeout_ms": 3500,
        },
        "CALIBRATE": {
            "cmd": "CALIBRATE",
            "tab": "Отложенные команды",
            "title": "Калибровка",
            "description": "Сохраняет коэффициент после короткой имитации измерения.",
            "group": "Ожидание ответа",
            "order": 30,
            "params": [{
                "name": "reference", "label": "Опорное значение", "type": "float",
                "default": 2.5,
                "constraints": {"minimum": 0.1, "maximum": 10.0, "step": 0.1},
            }],
            "result": [
                {"name": "coefficient", "label": "Коэффициент", "type": "float"},
                {"name": "saved", "label": "Сохранено", "type": "boolean"},
            ],
            "timeout_ms": 3000,
        },
        "COUNTER_NEXT": {
            "cmd": "COUNTER_NEXT",
            "tab": "PC simulator",
            "title": "Счётчик запросов",
            "description": "Пример простой команды с состоянием.",
            "group": "Protocol",
            "order": 50,
            "result": [{"name": "value", "label": "Значение", "type": "integer"}],
        },
    }

    # These two deliberately large groups exercise the responsive grid with
    # many cards of different heights and widths.
    for index in range(1, 13):
        command = f"DEMO_SENSOR_{index:02d}"
        normal[command] = {
            "cmd": command,
            "tab": "Большие группы",
            "title": f"Датчик {index:02d}",
            "description": "Виртуальный канал телеметрии.",
            "group": "Телеметрия — 12 команд",
            "order": index,
            "result": [
                {"name": "value", "label": "Значение", "type": "float"},
                {"name": "healthy", "label": "Исправен", "type": "boolean"},
                _enum_result("state", "Состояние", ["normal", "warning", "alarm"]),
            ],
            "autoupdate": _autoupdate(250 + index * 50),
        }

    for index in range(1, 13):
        command = f"DEMO_ACTUATOR_{index:02d}"
        normal[command] = {
            "cmd": command,
            "tab": "Большие группы",
            "title": f"Исполнитель {index:02d}",
            "description": "Настройка виртуального исполнительного канала.",
            "group": "Управление - 12 команд",
            "order": 100 + index,
            "params": [
                {"name": "enabled", "label": "Включён", "type": "boolean", "default": False},
                {
                    "name": "setpoint", "label": "Уставка", "type": "float",
                    "default": float(index),
                    "constraints": {"minimum": 0.0, "maximum": 100.0, "step": 0.5},
                },
                {
                    **_enum_field("mode", "Режим", ["manual", "automatic", "service"]),
                    "default": 0,
                },
            ],
            "result": [
                {"name": "applied", "label": "Применено", "type": "boolean"},
                {"name": "actual", "label": "Фактически", "type": "float"},
                _enum_result("mode", "Режим", ["manual", "automatic", "service"]),
            ],
        }
    return {**builtins, **normal}


DESCRIPTORS = _descriptors()


def _publish_field_tags(value: Any) -> None:
    if isinstance(value, dict):
        if isinstance(value.get("name"), str) and isinstance(value.get("type"), str):
            value.setdefault("tag", cbor_key_tag(value["name"]))
        for item in value.values():
            _publish_field_tags(item)
    elif isinstance(value, list):
        for item in value:
            _publish_field_tags(item)


_publish_field_tags(DESCRIPTORS)


class ProtocolFailure(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class DispatchResult:
    response: dict[str, Any]
    events: list[dict[str, Any]]


class KrulSimulator:
    """Thread-safe, stateful implementation of a useful Krul v4 subset."""

    protocol_version = 4
    device_name = "KRUL-PC-SIM"
    device_id = "KRUL-PC-SIM-01"
    firmware = "sim-1.0.0"

    def __init__(self, clock: Callable[[], float] = time.monotonic,
                 descriptors: dict[str, dict[str, Any]] | None = None) -> None:
        self._lock = threading.Lock()
        self._clock = clock
        self._started_at = clock()
        self.descriptors = DESCRIPTORS if descriptors is None else descriptors
        self._pins = {name: 0 for name in PIN_NAMES}
        self._adc_tick = 0
        self._dac_value = 0
        self._pwm = {
            channel: {"duty_cycle": 0, "period_counter": 1000}
            for channel in ("PWM_CH1", "PWM_CH2", "PWM_CH3", "PWM_CH4")
        }
        self._counter = 0
        self._calibration = 1.0
        self._actuators: dict[int, dict[str, Any]] = {}

    def _pin_state(self, name: str, now: float) -> int:
        if name == SQUARE_WAVE_PIN:
            phase = (now - self._started_at) % SQUARE_WAVE_PERIOD_S
            return int(phase >= SQUARE_WAVE_PERIOD_S / 2.0)
        return self._pins[name]

    @staticmethod
    def _success(transaction: int, result: dict[str, Any]) -> dict[str, Any]:
        return {"id": transaction, "success": True, "result": result}

    @staticmethod
    def _error(transaction: int, code: int, message: str) -> dict[str, Any]:
        return {
            "id": transaction,
            "success": False,
            "error": {"code": code, "message": message},
        }

    def dispatch(self, request: Any) -> DispatchResult:
        transaction = 0
        try:
            if not isinstance(request, dict):
                raise ProtocolFailure(5, "Request root must be an object")
            transaction = request.get("id", 0)
            if isinstance(transaction, bool) or not isinstance(transaction, int) \
                    or not 1 <= transaction <= 0xFFFFFFFF:
                transaction = 0
                raise ProtocolFailure(5, "Field 'id' must be a nonzero uint32")
            command = request.get("cmd")
            if not isinstance(command, str):
                raise ProtocolFailure(2, "Field 'cmd' must be a string")
            if command not in self.descriptors:
                raise ProtocolFailure(6, f"Unknown command '{command}'")
            if command == "PING":
                return DispatchResult(
                    {"id": transaction, "success": True}, [])
            params = request.get("params", {})
            if not isinstance(params, dict):
                raise ProtocolFailure(2, "Field 'params' must be an object")
            result, events = self._execute(command, params)
            return DispatchResult(self._success(transaction, result), events)
        except ProtocolFailure as exc:
            return DispatchResult(self._error(transaction, exc.code, str(exc)), [])

    def _execute(self, command: str,
                 params: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        if command == "WHOAMI":
            return {
                "protocol_version": self.protocol_version,
                "device_name": self.device_name,
                "device_id": self.device_id,
                "firmware": self.firmware,
            }, []
        if command == "CMD_LIST":
            return {"cmd_name": list(self.descriptors)}, []
        if command == "DESCRIBE":
            name = params.get("name")
            if not isinstance(name, str):
                raise ProtocolFailure(1, "Missing field 'name'")
            descriptor = self.descriptors.get(name)
            if descriptor is None:
                raise ProtocolFailure(6, f"Unknown command '{name}'")
            return descriptor, []
        if command == "PIN_GET":
            codes = params.get("pins", [])
            if not isinstance(codes, list) or not all(
                    isinstance(code, int) and not isinstance(code, bool)
                    for code in codes):
                raise ProtocolFailure(2, "Field 'pins' must be an array of enum integers")
            if any(not 0 <= code < len(PIN_NAMES) for code in codes):
                raise ProtocolFailure(3, "Unknown pin")
            selected = codes or list(range(len(PIN_NAMES)))
            with self._lock:
                now = self._clock()
                pins = [
                    {
                        "name": code,
                        "type": 1 if PIN_DIRECTIONS[PIN_NAMES[code]] == "OUT" else 0,
                        "state": self._pin_state(PIN_NAMES[code], now),
                    }
                    for code in selected
                ]
            return {"pins": pins}, []
        if command == "PIN_GET_V2":
            codes = params.get("pins", [])
            if not isinstance(codes, list) or not all(
                    isinstance(code, int) and not isinstance(code, bool)
                    for code in codes):
                raise ProtocolFailure(2, "Field 'pins' must be an array of enum integers")
            if any(not 0 <= code < len(PIN_NAMES) for code in codes):
                raise ProtocolFailure(3, "Unknown pin")
            selected = codes or list(range(len(PIN_NAMES)))
            with self._lock:
                now = self._clock()
                pins = [
                    {
                        "name": code,
                        "type": 1 if PIN_DIRECTIONS[PIN_NAMES[code]] == "OUT" else 0,
                        "state": self._pin_state(PIN_NAMES[code], now),
                    }
                    for code in selected
                ]
            return {"pins": pins}, []
        if command == "PIN_SET":
            pins = params.get("pins")
            if not isinstance(pins, list) or not pins:
                raise ProtocolFailure(1, "Field 'pins' must be a non-empty array")
            updates: list[tuple[int, str, int]] = []
            for pin in pins:
                if not isinstance(pin, dict):
                    raise ProtocolFailure(2, "Pin update must be an object")
                code, state = pin.get("name"), pin.get("state")
                if code == 0 and len(pins) == 1 and not isinstance(state, bool) \
                        and state in (0, 1):
                    updates.extend((index + 1, output_name, state)
                                   for index, output_name in enumerate(OUTPUT_PINS))
                    continue
                if isinstance(code, bool) or not isinstance(code, int) or \
                        not 1 <= code <= len(OUTPUT_PINS) or \
                        isinstance(state, bool) or state not in (0, 1):
                    raise ProtocolFailure(3, "Invalid output pin or state")
                updates.append((code, OUTPUT_PINS[code - 1], state))
            with self._lock:
                for _code, name, state in updates:
                    self._pins[name] = state
            return {"pins": [{"name": code, "state": state}
                              for code, _name, state in updates]}, []
        if command == "ECHO":
            text = params.get("text")
            count = params.get("count", 1)
            if not isinstance(text, str):
                raise ProtocolFailure(1, "Missing field 'text'")
            if isinstance(count, bool) or not isinstance(count, int):
                raise ProtocolFailure(2, "Field 'count' must be an integer")
            if not 1 <= count <= 10 or not 1 <= len(text) <= 128:
                raise ProtocolFailure(3, "ECHO parameter is out of range")
            return {"text": text * count}, []
        if command == "WIDGET_GALLERY":
            text = params.get("text", "Krul simulator")
            integer = params.get("integer", 42)
            floating = params.get("floating", 3.3)
            enabled = params.get("enabled", True)
            mode = params.get("mode", 2)
            if not isinstance(text, str) or not 1 <= len(text) <= 80:
                raise ProtocolFailure(3, "Field 'text' is out of range")
            if isinstance(integer, bool) or not isinstance(integer, int) \
                    or not -1000 <= integer <= 1000:
                raise ProtocolFailure(3, "Field 'integer' is out of range")
            if isinstance(floating, bool) or not isinstance(floating, (int, float)) \
                    or not -100.0 <= floating <= 100.0:
                raise ProtocolFailure(3, "Field 'floating' is out of range")
            if not isinstance(enabled, bool):
                raise ProtocolFailure(2, "Field 'enabled' must be a boolean")
            if isinstance(mode, bool) or mode not in {0, 1, 2}:
                raise ProtocolFailure(3, "Unknown gallery mode")
            return {
                "text": text,
                "integer": integer,
                "floating": float(floating),
                "enabled": enabled,
                "mode": mode,
                "terminal": f"Widget gallery executed in mode {mode}",
            }, []
        if command == "DAC_SET":
            value = params.get("value", 2048)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ProtocolFailure(2, "Field 'value' must be an integer")
            if not 0 <= value <= 4095:
                raise ProtocolFailure(3, "DAC value is out of range")
            with self._lock:
                self._dac_value = value
            return {"value": value, "voltage": value * 3.3 / 4095.0}, []
        if command == "PWM_SET":
            channel = params.get("channel", 0)
            duty_cycle = params.get("duty_cycle")
            period_counter = params.get("period_counter")
            channels = tuple(self._pwm)
            if isinstance(channel, bool) or not isinstance(channel, int) or \
                    not 0 <= channel < len(channels):
                raise ProtocolFailure(3, "Unknown PWM channel")
            for name, value in (
                ("duty_cycle", duty_cycle),
                ("period_counter", period_counter),
            ):
                if isinstance(value, bool) or not isinstance(value, int):
                    raise ProtocolFailure(2, f"Field '{name}' must be an integer")
            if not 0 <= duty_cycle <= 100 or not 1 <= period_counter <= 1000000:
                raise ProtocolFailure(3, "PWM parameter is out of range")
            with self._lock:
                self._pwm[channels[channel]] = {
                    "duty_cycle": duty_cycle,
                    "period_counter": period_counter,
                }
            return {"applied": True}, []
        if command == "ADC_READ":
            with self._lock:
                self._adc_tick = (self._adc_tick + 1) % 4096
                tick = self._adc_tick
            return {
                "value_AIN0": 1000 + tick,
                "value_AIN1": 2000 - tick,
                "value_AIN2": 3000 + tick,
                "value_AIN3": 4000 - tick,
                "value_AIN4": 500 + tick,
                "value_AIN5": 1500 - tick,
                "value_AIN6": 2500 + tick,
                "value_AIN7": 3500 - tick,
                "value_AIN8": 750 + tick,
                "value_AIN9": 1750 - tick,
                "value_AIN10": 2750 + tick,
                "value_AIN11": 3750 - tick,
                "value_AIN12": 2350 - tick,
            }, []
        if command == "ADC_READ_BY_GROUP":
            with self._lock:
                self._adc_tick = (self._adc_tick + 1) % 4096
                tick = self._adc_tick
            return {
                "Voltage": {
                    "value_AIN0": 1000 + tick,
                    "value_AIN1": 2000 - tick,
                    "value_AIN2": 3000 + tick,
                    "value_AIN3": 4000 - tick,
                },
                "Current": {
                    "value_AIN4": 500 + tick,
                    "value_AIN5": 1500 - tick,
                    "value_AIN6": 2500 + tick,
                    "value_AIN7": 3500 - tick,
                },
                "Temp": {
                    "value_AIN8": 750 + tick,
                    "value_AIN9": 1750 - tick,
                },
                "value_AIN10": 2750 + tick,
                "value_AIN11": 3750 - tick,
                "value_AIN12": 2350 - tick,
            }, []
        if command == "LOG_EMIT":
            severity = params.get("severity", 1)
            message = params.get("message", "Hello from simulator")
            severities = ("debug", "info", "warning", "error")
            if isinstance(severity, bool) or not isinstance(severity, int) or \
                    not 0 <= severity < len(severities) or not isinstance(message, str):
                raise ProtocolFailure(3, "Invalid log severity or message")
            event = {"event": "log", "data": {
                "severity": severities[severity], "message": message}}
            return {"queued": True}, [event]
        if command == "DELAY":
            milliseconds = params.get("milliseconds", 250)
            if isinstance(milliseconds, bool) or not isinstance(milliseconds, int):
                raise ProtocolFailure(2, "Field 'milliseconds' must be an integer")
            if not 0 <= milliseconds <= 5000:
                raise ProtocolFailure(3, "Delay is out of range")
            time.sleep(milliseconds / 1000.0)
            return {"milliseconds": milliseconds}, []
        if command == "DELAYED_ECHO":
            text = params.get("text", "Ответ получен")
            milliseconds = params.get("milliseconds", 700)
            if not isinstance(text, str) or not 1 <= len(text) <= 128:
                raise ProtocolFailure(3, "Field 'text' is out of range")
            if isinstance(milliseconds, bool) or not isinstance(milliseconds, int):
                raise ProtocolFailure(2, "Field 'milliseconds' must be an integer")
            if not 0 <= milliseconds <= 5000:
                raise ProtocolFailure(3, "Delay is out of range")
            time.sleep(milliseconds / 1000.0)
            return {"text": text, "milliseconds": milliseconds}, []
        if command == "SELF_TEST":
            scope = params.get("scope", 0)
            delays = (0.35, 0.8, 1.5)
            if isinstance(scope, bool) or not isinstance(scope, int) or \
                    not 0 <= scope < len(delays):
                raise ProtocolFailure(3, "Unknown self-test scope")
            time.sleep(delays[scope])
            return {
                "passed": True,
                "scope": scope,
                "details": "All simulated checks passed",
            }, []
        if command == "CALIBRATE":
            reference = params.get("reference", 2.5)
            if isinstance(reference, bool) or not isinstance(reference, (int, float)):
                raise ProtocolFailure(2, "Field 'reference' must be a number")
            if not 0.1 <= reference <= 10.0:
                raise ProtocolFailure(3, "Calibration reference is out of range")
            time.sleep(0.75)
            coefficient = 2.5 / float(reference)
            with self._lock:
                self._calibration = coefficient
            return {"coefficient": coefficient, "saved": True}, []
        if command == "COUNTER_NEXT":
            with self._lock:
                self._counter += 1
                value = self._counter
            return {"value": value}, []
        if command.startswith("DEMO_SENSOR_"):
            index = int(command.rsplit("_", 1)[1])
            with self._lock:
                self._adc_tick = (self._adc_tick + 1) % 1000
                tick = self._adc_tick
            state = 2 if tick % 29 == 0 else (1 if tick % 11 == 0 else 0)
            return {
                "value": round(index * 10.0 + tick / 10.0, 3),
                "healthy": state != 2,
                "state": state,
            }, []
        if command.startswith("DEMO_ACTUATOR_"):
            index = int(command.rsplit("_", 1)[1])
            enabled = params.get("enabled", False)
            setpoint = params.get("setpoint", float(index))
            mode = params.get("mode", 0)
            if not isinstance(enabled, bool):
                raise ProtocolFailure(2, "Field 'enabled' must be a boolean")
            if isinstance(setpoint, bool) or not isinstance(setpoint, (int, float)):
                raise ProtocolFailure(2, "Field 'setpoint' must be a number")
            if not 0.0 <= setpoint <= 100.0:
                raise ProtocolFailure(3, "Actuator setpoint is out of range")
            if isinstance(mode, bool) or mode not in {0, 1, 2}:
                raise ProtocolFailure(3, "Unknown actuator mode")
            state = {
                "enabled": enabled,
                "setpoint": float(setpoint),
                "mode": mode,
            }
            with self._lock:
                self._actuators[index] = state
            return {
                "applied": True,
                "actual": float(setpoint) if enabled else 0.0,
                "mode": mode,
            }, []
        raise ProtocolFailure(6, f"Unknown command '{command}'")


def serve_stream(reader: BinaryIO, writer: BinaryIO,
                 simulator: KrulSimulator) -> None:
    parser = FrameParser()
    while True:
        read_chunk = getattr(reader, "read1", reader.read)
        raw = read_chunk(4096)
        if not raw:
            return
        frames, _errors = parser.feed(raw)
        for frame in frames:
            try:
                request = decode_payload(frame.payload, frame.wire_format)
                dispatched = simulator.dispatch(request)
            except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
                dispatched = DispatchResult(
                    KrulSimulator._error(0, 4, f"Malformed payload: {exc}"), []
                )
            writer.write(encode_frame(dispatched.response, frame.wire_format))
            for event in dispatched.events:
                writer.write(encode_frame(event, frame.wire_format))
        if frames:
            writer.flush()


class _RequestHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        serve_stream(self.rfile, self.wfile, self.server.simulator)  # type: ignore[attr-defined]


class SimulatorServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address: tuple[str, int], simulator: KrulSimulator):
        self.simulator = simulator
        super().__init__(address, _RequestHandler)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7000)
    parser.add_argument("--stdio", action="store_true",
                        help="Use stdin/stdout instead of a TCP socket")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    simulator = KrulSimulator()
    if args.stdio:
        serve_stream(sys.stdin.buffer, sys.stdout.buffer, simulator)
        return 0
    with SimulatorServer((args.host, args.port), simulator) as server:
        print(f"Krul simulator listening on {args.host}:{server.server_address[1]}",
              flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
