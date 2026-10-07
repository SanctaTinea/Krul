#!/usr/bin/env python3
"""Stateful simulator of the СПУ (SPU) Krul v4 firmware for GUI development.

Mirrors the command table and result schemas declared in
``Apps/app_cm7/src/app_cmd.c``: the same command names, tabs, groups, orders
and — most importantly — the ``READ_TELEMETRY`` result, whose ``nodes`` array
is a tagged union of telemetry snapshots and events. The telemetry iterator
commands (``CREATE_TELEMETRY_ITERATOR``, ``TELEMETRY_ITERATOR_NEXT``,
``READ_TELEMETRY_PARTITION``, ``GET_TELEMETRY_ITERATOR``) reproduce the
firmware semantics of ``telemetry_iterator_next``: iteration is byte based, an
exhausted or invalidated iterator reports an execution error, and creating a
new iterator always restarts at the partition start.

Transport, framing, dispatch and the TCP server come from ``krul_simulator``;
this module only supplies the SPU descriptor set and command behaviour.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import deque
from typing import Any, Callable

from krul_simulator import (
    KrulSimulator,
    ProtocolFailure,
    SimulatorServer,
    _autoupdate,
    _enum_field,
    _enum_result,
    _publish_field_tags,
    serve_stream,
)

SPU_PROTOCOL_VERSION = 4
SPU_DEVICE_NAME = "СПУ"
SPU_DEVICE_ID = "SPU-MRAM-SIM"
SPU_FIRMWARE_VERSION = "3.0.0"

READ_TELEMETRY_MAX_COUNT = 16
UART_BUS_COUNT = 2
RS485_MAX_MESSAGE_SIZE = 256

# Относятся к телеграфу firmware: SETPIN_LIST / GETPIN_LIST.
SETPIN_NAMES = [
    "EN_3V3_PH_SW",
    "EN_FPF_3V3_PH",
    "EN_SW",
    "EN_V2_CH1",
    "EN_V3_CH1",
    "EN_ILIM2_CH1",
    "SW_CH1_EN_CHK_TM_CH1",
    "EN_CH1",
    "EN_CH2",
    "EN_CH3",
    "EN_CH4",
    "SW_4_CH4",
    "SW_3_CH4",
    "SW_2_CH4",
    "SW_1_CH4",
    "SW_CH4_U_SEL_CH4",
    "EN_CH5",
    "EN_VT1",
    "EN_VT2",
    "EN_VT3",
    "EN_VT4",
    "CAN_ONOFF",
    "TSens_ONOFF",
    "EN_FPF_485_1",
    "EN_FPF_485_2",
]
GETPIN_NAMES = [
    "SW_CH1_TM_LOUT_CH1",
    "TM_HOUT_CH1",
    "EN_SW_CHK",
    "TEMP_ALERT",
    "SW_CH4_HV_PRESENT",
    "SW_PW_Alert_meas_pre",
    "PW_CHECK_MCU",
    "FPF_FLAG_485_1",
    "FPF_FLAG_485_2",
    "TSENS_FLAG",
    "FPF_qFRAM_FLAG",
    "FPF_qMRAM_FLAG",
    "FPF_sFRAM2_FLAG",
    "CAN_FLAG",
    "FPF_SFRAM1_FLAG",
    "FPF_FLAG_3V3_PH",
]
PIN_NAMES = [*SETPIN_NAMES, *GETPIN_NAMES]
SPU_PIN_NAME_MAX = 40

ADC1_CHANNELS = [
    "ADC_CVOUT_CH1_HALL",
    "ADC_CVOUT1_CH1",
    "ADC_CVOUT2_CH1",
    "ADC_CVOUT1_CH2",
    "ADC_CVOUT2_CH2",
    "ADC_CVOUT_CH2",
    "ADC_CVOUT_CH2_HALL",
    "ADC_VOUT_CH3",
    "ADC_CVOUT2_CH5",
    "ADC_CVOUT1_CH5",
    "ADC_CVOUT_CH5",
    "ADC_CVOUT_CH5_HALL",
]
ADC3_CHANNELS = [
    "REF_OUT",
    "ADC_Vin_Power_POST",
    "ADC_Vin_Power_PRE",
    "VBAT",
    "VREF",
    "TEMP",
]

SPU_COMMAND_TITLES = ["no", "prepare", "test_enable", "enable", "restart"]
SPU_STATE_TITLES = ["Ожидание", "Работа", "Перезапуск"]
SPU_CFG_TITLES = [
    "preparebk_settings (7 шагов)",
    "enablebk_settings (8 шагов)",
    "restartbk_settings (11 шагов)",
]
MEM_TYPE_TITLES = ["MCU", "FRAM"]
DAC_CHANNEL_TITLES = ["Канал 1 (VRF_CH2)", "Канал 2 (REF_CH5)"]
CH5_STATE_TITLES = ["Выключен", "Норма", "Низкий", "Высокий"]
TELEM_DISCRIM_TITLES = ["Телеметрия", "Событие"]
TELEM_EVENT_TITLES = [
    "Начало циклограммы",
    "Конец циклограммы",
    "Включение питания",
]

SPUTELEMNODE_TELEMETRY = 0
SPUTELEMNODE_EVENT = 1
SPUEVENT_CYC_START = 0
SPUEVENT_CYC_END = 1
SPUEVENT_POWERUP = 2

# Record sizes in bytes, matching the packed structures of app_spu_telem.h:
# the discriminator is one byte, a telemetry snapshot is
# ``sizeof(spu_telemetry_t) == 44``, and events carry either a
# cycle index/time pair (8 bytes) or a power-up voltage/current pair
# (4 bytes). They drive the iterator's byte-based read_length/address.
TELEMETRY_RECORD_BYTES = 1 + 44
EVENT_CYCLE_RECORD_BYTES = 1 + 8
EVENT_POWERUP_RECORD_BYTES = 1 + 4
# Approximate MRAM address of the first telemetry slot
# (offsetof(spu_telemetry_memory_layout_t, telemetry_section.telemetry[0])).
TELEMETRY_PARTITION_START = 0x00001000


# --------------------------------------------------------------------------
# Конструкторы полей
# --------------------------------------------------------------------------


def _integer(name: str, label: str, minimum: int, maximum: int,
             default: int | None = None, widget: str | None = None
             ) -> dict[str, Any]:
    field: dict[str, Any] = {
        "name": name, "label": label, "type": "integer",
        "constraints": {"minimum": minimum, "maximum": maximum},
    }
    if default is not None:
        field["default"] = default
    if widget is not None:
        field["widget_hint"] = widget
    return field


def _unsigned(name: str, label: str | None = None) -> dict[str, Any]:
    field: dict[str, Any] = {"name": name, "type": "unsigned"}
    if label is not None:
        field["label"] = label
    return field


def _float(name: str, label: str, minimum: float, maximum: float, step: float,
           default: float | None = None) -> dict[str, Any]:
    field: dict[str, Any] = {
        "name": name, "label": label, "type": "float",
        "constraints": {"minimum": minimum, "maximum": maximum, "step": step},
    }
    if default is not None:
        field["default"] = default
    return field


def _boolean(name: str, label: str, default: bool | None = None
             ) -> dict[str, Any]:
    field: dict[str, Any] = {"name": name, "label": label, "type": "boolean"}
    if default is not None:
        field["default"] = default
    return field


def _string(name: str, label: str | None, min_length: int, max_length: int
            ) -> dict[str, Any]:
    field: dict[str, Any] = {
        "name": name, "type": "string",
        "constraints": {"minLength": min_length, "maxLength": max_length},
    }
    if label is not None:
        field["label"] = label
    return field


def _object(name: str, label: str, fields: list[dict[str, Any]],
            widget: str | None = None) -> dict[str, Any]:
    field: dict[str, Any] = {
        "name": name, "label": label, "type": "object", "fields": fields,
    }
    if widget is not None:
        field["widget_hint"] = widget
    return field


def _array(name: str, label: str, element: dict[str, Any], min_items: int,
           max_items: int, default: list[Any] | None = None
           ) -> dict[str, Any]:
    field: dict[str, Any] = {
        "name": name, "label": label, "type": "array",
        "constraints": {"minItems": min_items, "maxItems": max_items},
        "items": element,
    }
    if default is not None:
        field["default"] = default
    return field


def _telemetry_node_union() -> dict[str, Any]:
    """``READ_TELEMETRY`` node: tagged union of a snapshot or an event."""
    telemetry_fields = [
        _unsigned("time", "Время, мс"),
        _enum_result("spu_command", "Команда СПУ", SPU_COMMAND_TITLES),
        _enum_result("spu_state", "Состояние СПУ", SPU_STATE_TITLES),
        _enum_result("ch5_state", "Канал 5", CH5_STATE_TITLES),
        _unsigned("vin", "Vin"),
        _unsigned("in_spu", "IN_SPU"),
        _unsigned("iin_spu1", "IIn_SPU1"),
        _unsigned("iin_spu2", "IIn_SPU2"),
        _unsigned("tmp116_temp1", "TMP116 #1"),
        _unsigned("tmp116_temp2", "TMP116 #2"),
        _unsigned("tmp116_temp3", "TMP116 #3"),
        _unsigned("tmp116_temp4", "TMP116 #4"),
        _unsigned("tmp116_temp5", "TMP116 #5"),
        _unsigned("i_ch1_in", "Ток канала 1, вход"),
        _unsigned("i_ch1_out", "Ток канала 1, выход"),
        _unsigned("i_ch2_in", "Ток канала 2, вход"),
        _unsigned("i_ch2_out", "Ток канала 2, выход"),
        _unsigned("i_ch5_in", "Ток канала 5, вход"),
        _unsigned("i_ch5_out", "Ток канала 5, выход"),
        _unsigned("u_ch3", "Напряжение канала 3"),
        _unsigned("u_ch4", "Напряжение канала 4"),
        _unsigned("gpio_flags", "Флаги GPIO"),
        _unsigned("fpf_flags", "Флаги FPF"),
        _unsigned("alert_flags", "Флаги аварий"),
    ]
    event_fields = [
        _enum_result("event_kind", "Событие", TELEM_EVENT_TITLES),
        _unsigned("time", "Время, мс"),
        _unsigned("cyc_idx", "Номер циклограммы"),
        _unsigned("voltage", "Напряжение при включении"),
        _unsigned("current", "Ток при включении"),
    ]
    return {
        "type": "union",
        "tag": _enum_field("discrim", "Тип записи", TELEM_DISCRIM_TITLES),
        "variants": [
            {"value": SPUTELEMNODE_TELEMETRY, "fields": telemetry_fields},
            {"value": SPUTELEMNODE_EVENT, "fields": event_fields},
        ],
    }


# --------------------------------------------------------------------------
# Дескрипторы команд
# --------------------------------------------------------------------------


def _spu_descriptors() -> dict[str, dict[str, Any]]:
    builtins = {
        "PING": {"cmd": "PING", "builtin": True, "title": "PING"},
        "WHOAMI": {"cmd": "WHOAMI", "builtin": True, "title": "WHOAMI"},
        "CMD_LIST": {"cmd": "CMD_LIST", "builtin": True, "title": "CMD_LIST"},
        "DESCRIBE": {
            "cmd": "DESCRIBE",
            "builtin": True,
            "title": "DESCRIBE",
            "params": [_string("name", "Имя команды", 1, 47)],
        },
    }

    pin_read_item_fields = [
        _string("name", None, 1, SPU_PIN_NAME_MAX),
        _string("type", None, 1, 8),
        {"name": "state", "label": "Состояние", "type": "integer"},
    ]
    pin_result_item = {"type": "object", "fields": pin_read_item_fields}
    pin_update_fields = [
        _string("name", None, 1, SPU_PIN_NAME_MAX),
        _integer("state", "Состояние", 0, 1),
    ]
    pin_set_item = {"type": "object", "fields": pin_update_fields}
    pin_set_result_item = {"type": "object", "fields": pin_update_fields}

    adc1_fields = [_integer(name, name, 0, 65535) for name in ADC1_CHANNELS]
    adc3_fields = [_integer(name, name, 0, 65535) for name in ADC3_CHANNELS]
    temp_fields = [_integer(f"temp{index}", f"Датчик {index}", 0, 65535)
                   for index in range(1, 6)]
    ina_pair = [_integer("ina1", "Датчик 1", -32768, 32767),
                _integer("ina2", "Датчик 2", -32768, 32767)]
    dac_fields = [_integer("dac1", "Канал 1 (VRF_CH2)", 0, 4095),
                  _integer("dac2", "Канал 2 (REF_CH5)", 0, 4095)]

    anode_result = [
        {"name": "low_dac_level", "label": "Низкий уровень ЦАП",
         "type": "integer"},
        {"name": "normal_dac_level", "label": "Нормальный уровень ЦАП",
         "type": "integer"},
        {"name": "high_dac_level", "label": "Высокий уровень ЦАП",
         "type": "integer"},
        {"name": "critical_high_current_threshold", "label": "Крит. верхний ток, А",
         "type": "float"},
        {"name": "critical_low_current_threshold", "label": "Крит. нижний ток, А",
         "type": "float"},
        {"name": "high_current_threshold", "label": "Верхний ток, А",
         "type": "float"},
        {"name": "low_current_threshold", "label": "Нижний ток, А",
         "type": "float"},
        {"name": "channel_2_dac_value", "label": "ЦАП канала 2",
         "type": "integer"},
    ]
    anode_params = [
        _integer("low_dac_level", "Низкий уровень ЦАП", 0, 4095, 1950),
        _integer("normal_dac_level", "Нормальный уровень ЦАП", 0, 4095, 2350),
        _integer("high_dac_level", "Высокий уровень ЦАП", 0, 4095, 3540),
        _float("critical_high_current_threshold", "Крит. верхний ток, А",
               0.0, 10.0, 0.01, 0.85),
        _float("critical_low_current_threshold", "Крит. нижний ток, А",
               0.0, 10.0, 0.01, 0.37),
        _float("high_current_threshold", "Верхний ток, А", 0.0, 10.0, 0.01, 0.7679),
        _float("low_current_threshold", "Нижний ток, А", 0.0, 10.0, 0.01, 0.7279),
        _integer("channel_2_dac_value", "ЦАП канала 2", 0, 4095, 2180),
    ]

    commands: dict[str, dict[str, Any]] = {
        "PIN_READ": {
            "cmd": "PIN_READ", "tab": "GPIO", "title": "Прочитать выводы",
            "group": "Выводы", "order": 10, "widget_hint": "special_gpio",
            "params": [_array("pins", "Выводы",
                              _string(None, None, 1, SPU_PIN_NAME_MAX),
                              0, len(PIN_NAMES), default=[])],
            "result": [_array("pins", None, pin_result_item, 0, len(PIN_NAMES))],
            "autoupdate": _autoupdate(500),
        },
        "PIN_SET": {
            "cmd": "PIN_SET", "tab": "GPIO", "title": "Установить выводы",
            "group": "Выводы", "order": 20, "widget_hint": "special_gpio",
            "params": [_array("pins", None, pin_set_item, 1, len(SETPIN_NAMES))],
            "result": [_array("pins", None, pin_set_result_item, 1,
                              len(SETPIN_NAMES))],
        },
        "DAC_SET": {
            "cmd": "DAC_SET", "tab": "Аналоговые сигналы", "title": "Установить ЦАП",
            "group": "ЦАП", "order": 10,
            "params": [
                {**_enum_field("channel", "Канал", DAC_CHANNEL_TITLES),
                 "default": 0},
                _integer("value", "Код ЦАП", 0, 4095, 0, widget="special_dac"),
            ],
            "result": [{"name": "channel", "label": "Канал", "type": "integer"},
                       {"name": "value", "label": "Код ЦАП", "type": "integer"}],
        },
        "DAC_READ": {
            "cmd": "DAC_READ", "tab": "Аналоговые сигналы", "title": "Прочитать ЦАП",
            "group": "ЦАП", "order": 20,
            "result": [_object("dac", "Коды ЦАП", dac_fields,
                               widget="special_adc_group")],
            "autoupdate": _autoupdate(1000),
        },
        "ADC_READ": {
            "cmd": "ADC_READ", "tab": "Аналоговые сигналы", "title": "Мониторинг АЦП",
            "group": "АЦП", "order": 30,
            "result": [
                _object("adc1", "АЦП1 — токи и напряжения каналов",
                        adc1_fields, widget="special_adc_group"),
                _object("adc3", "АЦП3 — питание и служебные",
                        adc3_fields, widget="special_adc_group"),
            ],
            "autoupdate": _autoupdate(1000),
        },
        "TEMP_READ": {
            "cmd": "TEMP_READ", "tab": "Температура", "title": "Температура TMP116",
            "group": "Датчики", "order": 10,
            "result": [_object("temperature", "Температура (TMP116)",
                               temp_fields, widget="special_adc_group")],
            "autoupdate": _autoupdate(1000),
        },
        "INA_READ": {
            "cmd": "INA_READ", "tab": "Токи (INA230)", "title": "Мониторинг INA230",
            "group": "Датчики", "order": 10,
            "result": [
                _object("currents", "Токи", ina_pair,
                        widget="special_adc_group"),
                _object("powers", "Мощности", ina_pair,
                        widget="special_adc_group"),
                _object("shunt_voltages", "Напряжение на шунте", ina_pair,
                        widget="special_adc_group"),
                _object("bus_voltages", "Напряжение шины", ina_pair,
                        widget="special_adc_group"),
            ],
            "autoupdate": _autoupdate(1000),
        },
        "CAN_SEND": {
            "cmd": "CAN_SEND", "tab": "CAN", "title": "Отправить кадр FDCAN",
            "description": "Отправляет расширенный кадр FDCAN с данными",
            "group": "Отправка", "order": 10,
            "params": [_integer("id", "Идентификатор", 0, 0x1FFFFFFF),
                       _string("data", "Данные", 1, 64)],
            "result": [_unsigned("id", "Идентификатор"),
                       _unsigned("bytes", "Передано байт")],
        },
        "RUN_SPU_CMD": {
            "cmd": "RUN_SPU_CMD", "tab": "СПУ и телеметрия",
            "title": "Запустить программу СПУ",
            "description": "Запускает циклограммы запуска, подготовки",
            "group": "Управление", "order": 10,
            "params": [{**_enum_field("command", "Команда СПУ",
                                      SPU_COMMAND_TITLES), "default": 0}],
            "result": [_enum_result("command", "Команда", SPU_COMMAND_TITLES)],
        },
        "SET_ENGINE_STABILIZE_TIME": {
            "cmd": "SET_ENGINE_STABILIZE_TIME", "tab": "СПУ и телеметрия",
            "title": "Время стабилизации двигателя",
            "group": "Управление", "order": 20,
            "params": [_integer("time_ms", "Время стабилизации, мс", 0, 10000000)],
            "result": [_unsigned("time_ms", "Время, мс")],
        },
        "SPU_STATUS": {
            "cmd": "SPU_STATUS", "tab": "СПУ и телеметрия",
            "title": "Состояние СПУ",
            "description": "Состояние, тайминги и счётчики СПУ",
            "group": "Управление", "order": 30,
            "result": [
                _enum_result("state", "Состояние", SPU_STATE_TITLES),
                _enum_result("command", "Команда", SPU_COMMAND_TITLES),
                _unsigned("running_ms", "Наработано, мс"),
                _unsigned("time_ms", "Текущее время, мс"),
                _boolean("program_start_active", "Программа запущена"),
                _unsigned("program_start_tick", "Старт программы, тик"),
                _boolean("program_end_active", "Ожидание конца программы"),
                _unsigned("program_end_tick", "Конец программы, тик"),
                _unsigned("last_launch_start", "Старт последнего запуска, тик"),
                _unsigned("last_launch_end", "Конец последнего запуска, тик"),
                _unsigned("restart_count", "Число перезапусков"),
                _unsigned("restart_in_cmd_count", "Перезапуски в команде"),
            ],
            "autoupdate": _autoupdate(500),
        },
        "GET_SPU_CFG": {
            "cmd": "GET_SPU_CFG", "tab": "СПУ и телеметрия",
            "title": "Отобразить шаг циклограммы",
            "group": "Настройки", "order": 120,
            "params": [
                {**_enum_field("settings", "Блок циклограммы", SPU_CFG_TITLES),
                 "default": 0},
                _integer("step", "Шаг", 0, 10),
            ],
            "result": [{"name": "value", "label": "Задержка, мс",
                        "type": "integer"}],
        },
        "SET_SPU_CFG": {
            "cmd": "SET_SPU_CFG", "tab": "СПУ и телеметрия",
            "title": "Изменить шаг циклограммы",
            "group": "Настройки", "order": 130,
            "params": [
                {**_enum_field("settings", "Блок циклограммы", SPU_CFG_TITLES),
                 "default": 0},
                _integer("step", "Шаг", 0, 10),
                _integer("value", "Задержка, мс", 0, 10000000),
            ],
            "result": [{"name": "value", "label": "Задержка, мс",
                        "type": "integer"}],
        },
        "GET_ANODE_SETTINGS": {
            "cmd": "GET_ANODE_SETTINGS", "tab": "СПУ и телеметрия",
            "title": "Получить параметры анода",
            "group": "Настройки", "order": 140,
            "result": anode_result,
        },
        "SET_ANODE_SETTINGS": {
            "cmd": "SET_ANODE_SETTINGS", "tab": "СПУ и телеметрия",
            "title": "Изменить параметры анода",
            "description": "Уровни ЦАП и пороги токов канала 5",
            "group": "Настройки", "order": 150,
            "params": anode_params,
        },
        "SET_TELEMETRY_RECORDING_STATE": {
            "cmd": "SET_TELEMETRY_RECORDING_STATE", "tab": "СПУ и телеметрия",
            "title": "Включить запись телеметрии",
            "group": "Телеметрия", "order": 160,
            "params": [
                _boolean("enabled", "Запись телеметрии", default=False),
                _integer("period_ms", "Период записи, мс", 1, 3600000),
            ],
        },
        "GET_TELEMETRY_RECORDING_STATE": {
            "cmd": "GET_TELEMETRY_RECORDING_STATE", "tab": "СПУ и телеметрия",
            "title": "Получить состояние записи телеметрии",
            "group": "Телеметрия", "order": 170,
            "result": [_boolean("enabled", "Запись включена"),
                       _unsigned("period_ms", "Период, мс")],
            "autoupdate": _autoupdate(1000),
        },
        "READ_TELEMETRY": {
            "cmd": "READ_TELEMETRY", "tab": "СПУ и телеметрия",
            "title": "Чтение телеметрии",
            "description": "Читает записи телеметрии и событий из MRAM",
            "group": "Телеметрия", "order": 180,
            "params": [{
                "name": "count", "label": "Количество записей",
                "type": "unsigned", "default": 8,
                "constraints": {"minimum": 1,
                                "maximum": READ_TELEMETRY_MAX_COUNT},
            }],
            "result": [_array("nodes", "Записи", _telemetry_node_union(),
                              0, READ_TELEMETRY_MAX_COUNT)],
        },
        "CREATE_TELEMETRY_ITERATOR": {
            "cmd": "CREATE_TELEMETRY_ITERATOR", "nogui": True,
            "tab": "СПУ и телеметрия",
            "title": "Создание телеметрического итератора",
            "description": "Создаёт итератор внутри СПУ, который считывает "
                           "каждую запись телеметрии последовательно",
            "group": "Энергонезависимая память", "order": 220,
        },
        "TELEMETRY_ITERATOR_NEXT": {
            "cmd": "TELEMETRY_ITERATOR_NEXT", "nogui": True,
            "tab": "СПУ и телеметрия",
            "title": "Считать запись телеметрического итератора",
            "description": "Считывает и перемещает телеметрический итератор. "
                           "Если возникла ошибка, итератор нужно пересоздать",
            "group": "Энергонезависимая память", "order": 220,
            "result": [_array("nodes", "Записи", _telemetry_node_union(),
                              0, READ_TELEMETRY_MAX_COUNT)],
        },
        "READ_TELEMETRY_PARTITION": {
            "cmd": "READ_TELEMETRY_PARTITION", "nogui": True,
            "tab": "СПУ и телеметрия",
            "title": "Чтение раздела телеметрии",
            "description": "Читает раздел телеметрии",
            "group": "Телеметрия", "order": 180,
            "result": [_unsigned("start_address", "Стартовый адрес"),
                       _unsigned("length", "Длина")],
        },
        "GET_TELEMETRY_ITERATOR": {
            "cmd": "GET_TELEMETRY_ITERATOR", "nogui": True,
            "tab": "СПУ и телеметрия",
            "title": "Получить состояние телеметрического итератора",
            "description": "Отображает данные телеметрического итератора",
            "group": "Телеметрия", "order": 180,
            "result": [_unsigned("address", "Текущий адрес"),
                       _unsigned("read_length", "Пройденная длина")],
        },
        "WRITE_CONFIG_TO_MRAM": {
            "cmd": "WRITE_CONFIG_TO_MRAM", "tab": "СПУ и телеметрия",
            "title": "Сохранить конфигурацию в MRAM",
            "group": "Энергонезависимая память", "order": 190,
            "result": [_boolean("saved", "Сохранено")],
        },
        "SET_CONFIG_TO_DEFAULT": {
            "cmd": "SET_CONFIG_TO_DEFAULT", "tab": "СПУ и телеметрия",
            "title": "Сбросить конфигурацию",
            "description": "Восстанавливает заводские значения в RAM",
            "group": "Энергонезависимая память", "order": 200,
        },
        "MEM_READ": {
            "cmd": "MEM_READ", "nogui": True, "title": "Чтение памяти",
            "description": "Чтение памяти MCU/FRAM/MRAM, результат — hex-строка",
            "group": "Энергонезависимая память", "order": 210,
            "params": [
                {**_enum_field("mem", "Память", MEM_TYPE_TITLES), "default": 1},
                _integer("address", "Адрес", 0, 0x7FFFFFFF),
                _integer("size", "Размер, байт", 1, 512, 64),
            ],
            "result": [_unsigned("size", "Прочитано байт"),
                       _string("data", "Данные (hex)", 0, 1030)],
        },
        "MEM_WRITE": {
            "cmd": "MEM_WRITE", "nogui": True, "title": "Запись памяти",
            "description": "Запись hex-данных в память MCU/FRAM/MRAM",
            "group": "Энергонезависимая память", "order": 220,
            "params": [
                {**_enum_field("mem", "Память", MEM_TYPE_TITLES), "default": 1},
                _integer("address", "Адрес", 0, 0x7FFFFFFF),
                _string("data", "Данные (hex)", 1, 1024),
            ],
            "result": [_unsigned("size", "Записано байт")],
        },
        "SUPERMODE": {
            "cmd": "SUPERMODE", "nogui": True,
            "params": [_string("password", "Пароль", 1, 1000)],
            "result": [_boolean("result", None)],
        },
        "RS_WRITE": {
            "cmd": "RS_WRITE", "title": "Передать сообщение по RS485",
            "description": "Передаёт сообщение по выбранной шине",
            "timeout_ms": 20000,
            "params": [
                {"name": "rs_idx", "label": "Индекс шины", "type": "unsigned",
                 "constraints": {"minimum": 0, "maximum": UART_BUS_COUNT}},
                _string("data", "Данные", 1, RS485_MAX_MESSAGE_SIZE),
            ],
        },
    }
    descriptors = {**builtins, **commands}
    _publish_field_tags(descriptors)
    return descriptors


SPU_DESCRIPTORS = _spu_descriptors()


# --------------------------------------------------------------------------
# Модель
# --------------------------------------------------------------------------


class SpuSimulator(KrulSimulator):
    """Stateful simulation of the СПУ firmware command table."""

    protocol_version = SPU_PROTOCOL_VERSION
    device_name = SPU_DEVICE_NAME
    device_id = SPU_DEVICE_ID
    firmware = SPU_FIRMWARE_VERSION

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        super().__init__(clock=clock, descriptors=SPU_DESCRIPTORS)
        self._pins = {name: 0 for name in PIN_NAMES}
        self._dac = [0, 0]
        self._adc_tick = 0
        self._spu_state = 0
        self._spu_command = 0
        self._program_start_active = False
        self._program_start_tick = 0
        self._program_end_active = False
        self._program_end_tick = 0
        self._last_launch_start = 0
        self._last_launch_end = 0
        self._restart_count = 0
        self._restart_in_cmd_count = 0
        self._stabilize_time_ms = 0
        self._telemetry_enabled = False
        self._telemetry_period_ms = 1000
        self._cfg = {settings: [10 * (step + 1) for step in range(steps)]
                     for settings, steps in ((0, 7), (1, 8), (2, 11))}
        self._anode: dict[str, Any] = {
            "low_dac_level": 1950,
            "normal_dac_level": 2350,
            "high_dac_level": 3540,
            "critical_high_current_threshold": 0.85,
            "critical_low_current_threshold": 0.37,
            "high_current_threshold": 0.7679,
            "low_current_threshold": 0.7279,
            "channel_2_dac_value": 2180,
        }
        self._telemetry_log: deque[dict[str, Any]] = deque(maxlen=256)
        self._telemetry_tick = 0
        self._cycle_index = 0
        # Bytes ever written to the ring. The gap with the current log length
        # is how many bytes of older records the bounded deque dropped, which
        # shifts the partition start_address just like a wrapping ring.
        self._telemetry_written_bytes = 0
        self._telemetry_iterator: dict[str, Any] | None = None
        self._push_record(self._event_node(SPUEVENT_POWERUP, 0))

    # -- helpers ----------------------------------------------------------

    def _now_ms(self) -> int:
        return int((self._clock() - self._started_at) * 1000.0)

    def _require_int(self, value: Any, name: str, minimum: int, maximum: int
                     ) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ProtocolFailure(2, f"Field '{name}' must be an integer")
        if not minimum <= value <= maximum:
            raise ProtocolFailure(3, f"Field '{name}' is out of range")
        return value

    @staticmethod
    def _ch5_state() -> int:
        return 1

    def _telemetry_fields(self, tick: int) -> dict[str, Any]:
        return {
            "time": tick * 10,
            "spu_command": self._spu_command,
            "spu_state": self._spu_state,
            "ch5_state": self._ch5_state(),
            "vin": 27000 + tick,
            "in_spu": 1500 + tick % 100,
            "iin_spu1": 300 + tick % 40,
            "iin_spu2": 310 + tick % 40,
            "tmp116_temp1": 2500 + tick % 50,
            "tmp116_temp2": 2510 + tick % 50,
            "tmp116_temp3": 2520 + tick % 50,
            "tmp116_temp4": 2530 + tick % 50,
            "tmp116_temp5": 2540 + tick % 50,
            "i_ch1_in": 120 + tick % 20,
            "i_ch1_out": 118 + tick % 20,
            "i_ch2_in": 130 + tick % 20,
            "i_ch2_out": 128 + tick % 20,
            "i_ch5_in": 200 + tick % 30,
            "i_ch5_out": 198 + tick % 30,
            "u_ch3": 5000 + tick % 100,
            "u_ch4": 5100 + tick % 100,
            "gpio_flags": 0b101,
            "fpf_flags": 0b11,
            "alert_flags": 0b0,
        }

    def _telemetry_node(self, tick: int) -> dict[str, Any]:
        return {"discrim": SPUTELEMNODE_TELEMETRY,
                **self._telemetry_fields(tick)}

    def _event_node(self, kind: int, tick: int) -> dict[str, Any]:
        node = {
            "discrim": SPUTELEMNODE_EVENT,
            "event_kind": kind,
            "time": tick * 10,
            "cyc_idx": self._cycle_index,
            "voltage": 0,
            "current": 0,
        }
        if kind == SPUEVENT_POWERUP:
            node["voltage"] = 27000
            node["current"] = 120
        return node

    def _append_telemetry(self) -> None:
        self._telemetry_tick += 1
        tick = self._telemetry_tick
        if tick % 9 == 0:
            self._push_record(self._event_node(SPUEVENT_CYC_END, tick))
        elif tick % 5 == 0:
            self._cycle_index += 1
            self._push_record(self._event_node(SPUEVENT_CYC_START, tick))
        else:
            self._push_record(self._telemetry_node(tick))

    def _push_record(self, node: dict[str, Any]) -> None:
        self._telemetry_log.append(node)
        self._telemetry_written_bytes += self._node_size(node)

    @staticmethod
    def _node_size(node: dict[str, Any]) -> int:
        """Byte size of a record in the ring (mirrors get_telemetry_node_size)."""
        if node.get("discrim") == SPUTELEMNODE_TELEMETRY:
            return TELEMETRY_RECORD_BYTES
        if node.get("event_kind") == SPUEVENT_POWERUP:
            return EVENT_POWERUP_RECORD_BYTES
        return EVENT_CYCLE_RECORD_BYTES

    def _telemetry_partition(self) -> dict[str, int]:
        """Current telemetry partition as seen by the firmware iterator."""
        length = sum(self._node_size(node) for node in self._telemetry_log)
        dropped = self._telemetry_written_bytes - length
        return {"start_address": TELEMETRY_PARTITION_START + dropped,
                "length": length}

    def _record_at_offset(self, offset: int) -> tuple[dict[str, Any], int] | None:
        """Return the record starting exactly at *offset*, or None."""
        cursor = 0
        for node in self._telemetry_log:
            size = self._node_size(node)
            if cursor == offset:
                return node, size
            cursor += size
        return None

    # -- dispatch ---------------------------------------------------------

    def _execute(self, command: str,
                 params: dict[str, Any]) -> tuple[dict[str, Any],
                                                  list[dict[str, Any]]]:
        handler = getattr(self, f"_cmd_{command.lower()}", None)
        if handler is not None:
            return handler(params), []
        return super()._execute(command, params)

    # -- handlers ---------------------------------------------------------

    def _cmd_pin_read(self, params: dict[str, Any]) -> dict[str, Any]:
        names = params.get("pins", [])
        if not isinstance(names, list) or not all(
                isinstance(name, str) for name in names):
            raise ProtocolFailure(2, "Field 'pins' must be an array of strings")
        for name in names:
            if name not in self._pins:
                raise ProtocolFailure(3, f"Unknown pin '{name}'")
        selected = names or list(self._pins)
        with self._lock:
            return {"pins": [
                {"name": name,
                 "type": "OUT" if name in SETPIN_NAMES else "IN",
                 "state": self._pins[name]}
                for name in selected
            ]}

    def _cmd_pin_set(self, params: dict[str, Any]) -> dict[str, Any]:
        pins = params.get("pins")
        if not isinstance(pins, list) or not pins:
            raise ProtocolFailure(1, "Field 'pins' must be a non-empty array")
        updates: list[tuple[str, int]] = []
        for item in pins:
            if not isinstance(item, dict):
                raise ProtocolFailure(2, "Pin update must be an object")
            name, state = item.get("name"), item.get("state")
            if not isinstance(name, str) or name not in SETPIN_NAMES:
                raise ProtocolFailure(3, "Unknown output pin")
            if isinstance(state, bool) or state not in (0, 1):
                raise ProtocolFailure(3, "Invalid pin state")
            updates.append((name, state))
        with self._lock:
            for name, state in updates:
                self._pins[name] = state
        return {"pins": [{"name": name, "state": state}
                         for name, state in updates]}

    def _cmd_dac_set(self, params: dict[str, Any]) -> dict[str, Any]:
        channel = params.get("channel", 0)
        value = params.get("value", 0)
        if isinstance(channel, bool) or not isinstance(channel, int) or \
                not 0 <= channel < len(DAC_CHANNEL_TITLES):
            raise ProtocolFailure(3, "Unknown DAC channel")
        value = self._require_int(value, "value", 0, 4095)
        with self._lock:
            self._dac[channel] = value
        return {"channel": channel, "value": value}

    def _cmd_dac_read(self, params: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            return {"dac": {"dac1": self._dac[0], "dac2": self._dac[1]}}

    def _cmd_adc_read(self, params: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._adc_tick = (self._adc_tick + 1) % 4096
            tick = self._adc_tick
        return {
            "adc1": {name: 1000 + index * 200 + tick
                     for index, name in enumerate(ADC1_CHANNELS)},
            "adc3": {name: 2000 + index * 150 + tick
                     for index, name in enumerate(ADC3_CHANNELS)},
        }

    def _cmd_temp_read(self, params: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._adc_tick = (self._adc_tick + 1) % 4096
            tick = self._adc_tick
        return {"temperature": {f"temp{index}": 2500 + index * 10 + tick % 40
                                for index in range(1, 6)}}

    def _cmd_ina_read(self, params: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._adc_tick = (self._adc_tick + 1) % 4096
            tick = self._adc_tick
        pair = {"ina1": 120 + tick % 20, "ina2": 130 + tick % 20}
        return {
            "currents": dict(pair),
            "powers": {"ina1": pair["ina1"] * 27, "ina2": pair["ina2"] * 27},
            "shunt_voltages": {"ina1": 15 + tick % 5, "ina2": 16 + tick % 5},
            "bus_voltages": {"ina1": 27000 + tick, "ina2": 26900 + tick},
        }

    def _cmd_can_send(self, params: dict[str, Any]) -> dict[str, Any]:
        identifier = params.get("id")
        data = params.get("data")
        identifier = self._require_int(identifier, "id", 0, 0x1FFFFFFF)
        if not isinstance(data, str) or not 1 <= len(data) <= 64:
            raise ProtocolFailure(3, "Field 'data' is out of range")
        return {"id": identifier, "bytes": len(data)}

    def _cmd_run_spu_cmd(self, params: dict[str, Any]) -> dict[str, Any]:
        command = params.get("command", 0)
        if isinstance(command, bool) or not isinstance(command, int) or \
                not 0 <= command < len(SPU_COMMAND_TITLES):
            raise ProtocolFailure(3, "Unknown SPU command")
        with self._lock:
            self._spu_command = command
            if command == 3:  # enable
                self._spu_state = 1
                self._program_start_active = True
                self._program_start_tick = self._now_ms()
                self._last_launch_start = self._program_start_tick
            elif command == 4:  # restart
                self._spu_state = 2
                self._restart_count += 1
                self._restart_in_cmd_count += 1
        return {"command": command}

    def _cmd_set_engine_stabilize_time(
            self, params: dict[str, Any]) -> dict[str, Any]:
        time_ms = self._require_int(params.get("time_ms"), "time_ms",
                                    0, 10000000)
        with self._lock:
            self._stabilize_time_ms = time_ms
        return {"time_ms": time_ms}

    def _cmd_spu_status(self, params: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            return {
                "state": self._spu_state,
                "command": self._spu_command,
                "running_ms": self._now_ms(),
                "time_ms": self._now_ms(),
                "program_start_active": self._program_start_active,
                "program_start_tick": self._program_start_tick,
                "program_end_active": self._program_end_active,
                "program_end_tick": self._program_end_tick,
                "last_launch_start": self._last_launch_start,
                "last_launch_end": self._last_launch_end,
                "restart_count": self._restart_count,
                "restart_in_cmd_count": self._restart_in_cmd_count,
            }

    def _cmd_get_spu_cfg(self, params: dict[str, Any]) -> dict[str, Any]:
        settings = self._require_int(params.get("settings", 0), "settings",
                                     0, 2)
        step = self._require_int(params.get("step", 0), "step", 0, 10)
        with self._lock:
            block = self._cfg[settings]
            if step >= len(block):
                raise ProtocolFailure(3, "Step is out of range for the block")
            return {"value": block[step]}

    def _cmd_set_spu_cfg(self, params: dict[str, Any]) -> dict[str, Any]:
        settings = self._require_int(params.get("settings", 0), "settings",
                                     0, 2)
        step = self._require_int(params.get("step", 0), "step", 0, 10)
        value = self._require_int(params.get("value"), "value", 0, 10000000)
        with self._lock:
            block = self._cfg[settings]
            if step >= len(block):
                raise ProtocolFailure(3, "Step is out of range for the block")
            block[step] = value
        return {"value": value}

    def _cmd_get_anode_settings(self, params: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            return dict(self._anode)

    def _cmd_set_anode_settings(self, params: dict[str, Any]) -> dict[str, Any]:
        limits = {
            "low_dac_level": (0, 4095, True),
            "normal_dac_level": (0, 4095, True),
            "high_dac_level": (0, 4095, True),
            "channel_2_dac_value": (0, 4095, True),
            "critical_high_current_threshold": (0.0, 10.0, False),
            "critical_low_current_threshold": (0.0, 10.0, False),
            "high_current_threshold": (0.0, 10.0, False),
            "low_current_threshold": (0.0, 10.0, False),
        }
        updated: dict[str, Any] = {}
        for name, (minimum, maximum, integral) in limits.items():
            if name not in params:
                continue
            value = params[name]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ProtocolFailure(2, f"Field '{name}' must be a number")
            if not minimum <= value <= maximum:
                raise ProtocolFailure(3, f"Field '{name}' is out of range")
            updated[name] = int(value) if integral else float(value)
        with self._lock:
            self._anode.update(updated)
        return {}

    def _cmd_set_telemetry_recording_state(
            self, params: dict[str, Any]) -> dict[str, Any]:
        enabled = params.get("enabled", False)
        if not isinstance(enabled, bool):
            raise ProtocolFailure(2, "Field 'enabled' must be a boolean")
        period_ms = self._require_int(params.get("period_ms"), "period_ms",
                                      1, 3600000)
        with self._lock:
            self._telemetry_enabled = enabled
            self._telemetry_period_ms = period_ms
        return {}

    def _cmd_get_telemetry_recording_state(
            self, params: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            return {"enabled": self._telemetry_enabled,
                    "period_ms": self._telemetry_period_ms}

    def _cmd_read_telemetry(self, params: dict[str, Any]) -> dict[str, Any]:
        count = self._require_int(params.get("count", 8), "count", 1,
                                  READ_TELEMETRY_MAX_COUNT)
        with self._lock:
            self._append_telemetry()
            nodes = list(self._telemetry_log)[-count:]
        return {"nodes": nodes}

    def _cmd_create_telemetry_iterator(self, params: dict[str, Any]
                                       ) -> dict[str, Any]:
        with self._lock:
            partition = self._telemetry_partition()
            self._telemetry_iterator = {
                "address": partition["start_address"],
                "read_length": 0,
                # Snapshot: a partition change invalidates the iterator, as
                # memcmp(prev_partition, part) does in telemetry_iterator_next.
                "partition": partition,
            }
        return {}

    def _cmd_get_telemetry_iterator(self, params: dict[str, Any]
                                    ) -> dict[str, Any]:
        with self._lock:
            iterator = self._telemetry_iterator
            if iterator is None:
                return {"address": 0, "read_length": 0}
            return {"address": iterator["address"],
                    "read_length": iterator["read_length"]}

    def _cmd_read_telemetry_partition(self, params: dict[str, Any]
                                      ) -> dict[str, Any]:
        with self._lock:
            return self._telemetry_partition()

    def _cmd_telemetry_iterator_next(self, params: dict[str, Any]
                                     ) -> dict[str, Any]:
        with self._lock:
            iterator = self._telemetry_iterator
            if iterator is None:
                raise ProtocolFailure(7, "Telemetry iterator is not created")
            partition = self._telemetry_partition()
            if partition["length"] <= 0:
                raise ProtocolFailure(7, "Telemetry partition is empty")
            if partition != iterator["partition"]:
                raise ProtocolFailure(
                    7, "Telemetry partition changed; recreate the iterator")
            if iterator["read_length"] >= partition["length"]:
                raise ProtocolFailure(7, "Telemetry iterator reached the end")
            found = self._record_at_offset(iterator["read_length"])
            if found is None:
                raise ProtocolFailure(7, "Corrupt telemetry record")
            node, size = found
            iterator["read_length"] += size
            iterator["address"] = (partition["start_address"]
                                   + iterator["read_length"])
            return {"nodes": [dict(node)]}

    def _cmd_write_config_to_mram(self, params: dict[str, Any]
                                  ) -> dict[str, Any]:
        return {"saved": True}

    def _cmd_set_config_to_default(self, params: dict[str, Any]
                                   ) -> dict[str, Any]:
        with self._lock:
            self._anode.update({
                "low_dac_level": 1950,
                "normal_dac_level": 2350,
                "high_dac_level": 3540,
                "critical_high_current_threshold": 0.85,
                "critical_low_current_threshold": 0.37,
                "high_current_threshold": 0.7679,
                "low_current_threshold": 0.7279,
                "channel_2_dac_value": 2180,
            })
        return {}

    def _cmd_mem_read(self, params: dict[str, Any]) -> dict[str, Any]:
        self._require_int(params.get("mem", 1), "mem", 0, 1)
        address = self._require_int(params.get("address"), "address",
                                    0, 0x7FFFFFFF)
        size = self._require_int(params.get("size", 64), "size", 1, 512)
        data = bytes((address + index) & 0xFF for index in range(size))
        return {"size": size, "data": data.hex()}

    def _cmd_mem_write(self, params: dict[str, Any]) -> dict[str, Any]:
        self._require_int(params.get("mem", 1), "mem", 0, 1)
        self._require_int(params.get("address"), "address", 0, 0x7FFFFFFF)
        data = params.get("data")
        if not isinstance(data, str) or not 1 <= len(data) <= 1024:
            raise ProtocolFailure(3, "Field 'data' is out of range")
        try:
            payload = bytes.fromhex(data)
        except ValueError as exc:
            raise ProtocolFailure(3, f"Invalid hex data: {exc}") from exc
        return {"size": len(payload)}

    def _cmd_supermode(self, params: dict[str, Any]) -> dict[str, Any]:
        password = params.get("password")
        if not isinstance(password, str) or not 1 <= len(password) <= 1000:
            raise ProtocolFailure(3, "Field 'password' is out of range")
        return {"result": True}

    def _cmd_rs_write(self, params: dict[str, Any]) -> dict[str, Any]:
        self._require_int(params.get("rs_idx", 0), "rs_idx", 0, UART_BUS_COUNT)
        data = params.get("data")
        if not isinstance(data, str) or \
                not 1 <= len(data) <= RS485_MAX_MESSAGE_SIZE:
            raise ProtocolFailure(3, "Field 'data' is out of range")
        return {}


# --------------------------------------------------------------------------
# Точка входа
# --------------------------------------------------------------------------


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7001)
    parser.add_argument("--stdio", action="store_true",
                        help="Use stdin/stdout instead of a TCP socket")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    simulator = SpuSimulator()
    if args.stdio:
        serve_stream(sys.stdin.buffer, sys.stdout.buffer, simulator)
        return 0
    with SimulatorServer((args.host, args.port), simulator) as server:
        print(f"SPU simulator listening on {args.host}:{server.server_address[1]}",
              flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
