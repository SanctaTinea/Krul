#include "krul_internal.h"

/*
 * Сериализация discovery и встроенных команд.
 *
 * DESCRIBE преобразует те же дескрипторы, по которым прошивка проверяет
 * запросы/ответы, в метаданные GUI. Поэтому формы, диапазоны и таймауты не
 * дублируются в Python-клиенте.
 */

#include <string.h>

#define KRUL_MAX_COMMAND_NAME 48U

static const char* type_name(krul_type_t type) {
    switch (type) {
        case KRUL_TYPE_I32:
            return "integer";
        case KRUL_TYPE_U32:
            return "unsigned";
        case KRUL_TYPE_F32:
            return "float";
        case KRUL_TYPE_STRING:
            return "string";
        case KRUL_TYPE_BOOL:
            return "boolean";
        case KRUL_TYPE_ENUM:
            return "enum";
        case KRUL_TYPE_ARRAY:
            return "array";
        case KRUL_TYPE_OBJECT:
            return "object";
        case KRUL_TYPE_CONSOLE_STRING:
            return "console_string";
        default:
            return "invalid";
    }
}

static const char* widget_name(krul_widget_t widget) {
    switch (widget) {
        case KRUL_WIDGET_SLIDER:
            return "slider";
        case KRUL_WIDGET_SPINBOX:
            return "spinbox";
        case KRUL_WIDGET_SPECIAL_ADC:
            return "special_adc";
        case KRUL_WIDGET_SPECIAL_ADC_GROUP:
            return "special_adc_group";
        case KRUL_WIDGET_SPECIAL_GPIO:
            return "special_gpio";
        case KRUL_WIDGET_SPECIAL_DAC:
            return "special_dac";
        case KRUL_WIDGET_SPECIAL_PWM:
            return "special_pwm";
        case KRUL_WIDGET_SPECIAL_TEST:
            return "special_test";
        default:
            return NULL;
    }
}

static const char* console_name(krul_console_type_t severity) {
    switch (severity) {
        case KRUL_CONSOLE_DEBUG:
            return "debug";
        case KRUL_CONSOLE_WARNING:
            return "warning";
        case KRUL_CONSOLE_ERROR:
            return "error";
        default:
            return "info";
    }
}

static bool serialize_field_at(serde_writer_t writer,
                               const serde_key_t* container_key,
                               const krul_field_desc_t* field) {
    /* Рекурсивно сериализует одно поле, включая элементы/поля контейнеров. */
    serde_key_t name = krul_named_key("name");
    serde_key_t tag = krul_named_key("tag");
    serde_key_t label = krul_named_key("label");
    serde_key_t type = krul_named_key("type");
    serde_key_t widget = krul_named_key("widget_hint");
    serde_key_t constraints = krul_named_key("constraints");
    serde_key_t default_key = krul_named_key("default");
    if (!serde_begin_object(writer, container_key)) return false;
    if (field->name != NULL && !serde_put_string(writer, &name, field->name))
        return false;
    if (field->name != NULL &&
        !serde_put_u32(writer, &tag, krul_field_tag(field)))
        return false;
    if (field->label != NULL && !serde_put_string(writer, &label, field->label))
        return false;
    if (!serde_put_string(writer, &type, type_name(field->type))) return false;
    const char* widget_value = widget_name(field->widget);
    if (widget_value != NULL && !serde_put_string(writer, &widget, widget_value))
        return false;
    if (field->has_constraints) {
        if (!serde_begin_object(writer, &constraints)) return false;
        serde_key_t minimum = krul_named_key("minimum");
        serde_key_t maximum = krul_named_key("maximum");
        switch (field->type) {
            case KRUL_TYPE_I32:
                serde_put_i32(writer, &minimum, field->constraints.i32.min);
                serde_put_i32(writer, &maximum, field->constraints.i32.max);
                break;
            case KRUL_TYPE_U32:
                serde_put_u32(writer, &minimum, field->constraints.u32.min);
                serde_put_u32(writer, &maximum, field->constraints.u32.max);
                break;
            case KRUL_TYPE_F32: {
                serde_key_t step = krul_named_key("step");
                serde_put_f32(writer, &minimum, field->constraints.f32.min);
                serde_put_f32(writer, &maximum, field->constraints.f32.max);
                serde_put_f32(writer, &step, field->constraints.f32.step);
                break;
            }
            case KRUL_TYPE_STRING: {
                serde_key_t min_length = krul_named_key("minLength");
                serde_key_t max_length = krul_named_key("maxLength");
                serde_put_u32(writer, &min_length,
                              field->constraints.string.min_length);
                serde_put_u32(writer, &max_length,
                              field->constraints.string.max_length);
                break;
            }
            case KRUL_TYPE_ENUM: {
                serde_key_t values = krul_named_key("values");
                serde_key_t value = krul_named_key("value");
                serde_key_t title = krul_named_key("title");
                serde_begin_array(writer, &values);
                for (uint16_t index = 0U;
                     index < field->constraints.enumeration.count; ++index) {
                    serde_begin_object(writer, NULL);
                    serde_put_i32(
                        writer, &value,
                        field->constraints.enumeration.values[index].value);
                    serde_put_string(
                        writer, &title,
                        field->constraints.enumeration.values[index].title);
                    serde_end_object(writer);
                }
                serde_end_array(writer);
                break;
            }
            case KRUL_TYPE_ARRAY: {
                serde_key_t min_items = krul_named_key("minItems");
                serde_key_t max_items = krul_named_key("maxItems");
                serde_put_u32(writer, &min_items,
                              field->constraints.array.min_count);
                serde_put_u32(writer, &max_items,
                              field->constraints.array.max_count);
                break;
            }
            case KRUL_TYPE_CONSOLE_STRING: {
                serde_key_t severity = krul_named_key("severity");
                serde_key_t max_length = krul_named_key("maxLength");
                serde_put_string(writer, &severity,
                                 console_name(field->schema.console.severity));
                serde_put_u32(writer, &max_length,
                              field->constraints.console.max_length);
                break;
            }
            default:
                break;
        }
        if (!serde_end_object(writer)) return false;
    }
    if (field->has_default) {
        switch (field->type) {
            case KRUL_TYPE_I32:
                serde_put_i32(writer, &default_key, field->default_value.i32);
                break;
            case KRUL_TYPE_U32:
                serde_put_u32(writer, &default_key, field->default_value.u32);
                break;
            case KRUL_TYPE_F32:
                serde_put_f32(writer, &default_key, field->default_value.f32);
                break;
            case KRUL_TYPE_BOOL:
                serde_put_bool(writer, &default_key,
                               field->default_value.boolean);
                break;
            case KRUL_TYPE_STRING:
                serde_put_string(writer, &default_key,
                                 field->default_value.string);
                break;
            case KRUL_TYPE_ENUM:
                serde_put_i32(writer, &default_key,
                              field->default_value.i32);
                break;
            case KRUL_TYPE_ARRAY:
                serde_begin_array(writer, &default_key);
                serde_end_array(writer);
                break;
            default:
                break;
        }
    }
    if (field->type == KRUL_TYPE_ARRAY) {
        serde_key_t items = krul_named_key("items");
        if (!serialize_field_at(writer, &items, field->schema.array.element))
            return false;
    } else if (field->type == KRUL_TYPE_OBJECT) {
        serde_key_t fields = krul_named_key("fields");
        if (!serde_begin_array(writer, &fields)) return false;
        for (uint16_t index = 0U; index < field->schema.object.count; ++index) {
            if (!serialize_field_at(writer, NULL,
                                    &field->schema.object.fields[index]))
                return false;
        }
        if (!serde_end_array(writer)) return false;
    }
    return serde_end_object(writer) && serde_writer_ok(writer);
}

static bool serialize_command(serde_writer_t writer,
                              const krul_command_t* command) {
    /* Нулевые/NULL UI-метаданные опускаются, чтобы DESCRIBE оставался компактным. */
    serde_key_t cmd = krul_named_key("cmd");
    serde_key_t builtin = krul_named_key("builtin");
    serde_key_t nogui = krul_named_key("nogui");
    serde_key_t tab = krul_named_key("tab");
    serde_key_t title = krul_named_key("title");
    serde_key_t description = krul_named_key("description");
    serde_key_t group = krul_named_key("group");
    serde_key_t widget = krul_named_key("widget_hint");
    serde_key_t order = krul_named_key("order");
    serde_key_t params = krul_named_key("params");
    serde_key_t result = krul_named_key("result");
    serde_key_t autoupdate = krul_named_key("autoupdate");
    serde_key_t timeout = krul_named_key("timeout_ms");
    serde_put_string(writer, &cmd, command->name);
    if (command->type == KRUL_CMD_BUILTIN)
        serde_put_bool(writer, &builtin, true);
    if (command->type == KRUL_CMD_NOGUI)
        serde_put_bool(writer, &nogui, true);
    if (command->tab != NULL) serde_put_string(writer, &tab, command->tab);
    serde_put_string(writer, &title,
                     command->title != NULL ? command->title : command->name);
    if (command->description != NULL)
        serde_put_string(writer, &description, command->description);
    if (command->group != NULL) serde_put_string(writer, &group, command->group);
    const char* widget_value = widget_name(command->widget);
    if (widget_value != NULL)
        serde_put_string(writer, &widget, widget_value);
    if (command->order != 0U) serde_put_u32(writer, &order, command->order);
    if (command->timeout_ms != 0U)
        serde_put_u32(writer, &timeout, command->timeout_ms);
    if (command->params_count > 0U) {
        serde_begin_array(writer, &params);
        for (uint16_t index = 0U; index < command->params_count; ++index)
            if (!serialize_field_at(writer, NULL, &command->params[index]))
                return false;
        serde_end_array(writer);
    }
    if (command->result_count > 0U) {
        serde_begin_array(writer, &result);
        for (uint16_t index = 0U; index < command->result_count; ++index)
            if (!serialize_field_at(writer, NULL, &command->result[index]))
                return false;
        serde_end_array(writer);
    }
    if (command->autoupdate) {
        serde_key_t min_period = krul_named_key("min_period");
        serde_key_t max_period = krul_named_key("max_period");
        serde_key_t default_period = krul_named_key("default_period");
        serde_begin_object(writer, &autoupdate);
        serde_put_u32(writer, &min_period, command->min_period_ms);
        serde_put_u32(writer, &max_period, command->max_period_ms);
        serde_put_u32(writer, &default_period, command->default_period_ms);
        serde_end_object(writer);
    }
    return serde_writer_ok(writer);
}

bool krul_builtin_dispatch(const krul_server_t* server,
                           const krul_command_t* command,
                           const krul_args_t* args, serde_writer_t writer,
                           krul_error_t* error) {
    /* Встроенные команды без обработчика исполняются здесь, но используют тот же конверт,
     * запись и проверку входных params, что обычные команды. */
    if (strcmp(command->name, "PING") == 0) return true;
    if (strcmp(command->name, "WHOAMI") == 0) {
        serde_key_t protocol = krul_named_key("protocol_version");
        serde_key_t device = krul_named_key("device_name");
        serde_key_t device_id = krul_named_key("device_id");
        serde_key_t firmware = krul_named_key("firmware");
        serde_put_u32(writer, &protocol, server->config.protocol_version);
        serde_put_string(writer, &device, server->config.device_name);
        if (server->config.device_id != NULL && server->config.device_id[0] != '\0')
            serde_put_string(writer, &device_id, server->config.device_id);
        serde_put_string(writer, &firmware, server->config.firmware_version);
        return serde_writer_ok(writer);
    }
    if (strcmp(command->name, "CMD_LIST") == 0) {
        serde_key_t names = krul_named_key("cmd_name");
        serde_begin_array(writer, &names);
        for (size_t index = 0U; index < server->config.command_count; ++index)
            serde_put_string(writer, NULL,
                             server->config.commands[index]->name);
        serde_end_array(writer);
        return serde_writer_ok(writer);
    }
    if (strcmp(command->name, "DESCRIBE") == 0) {
        char name[KRUL_MAX_COMMAND_NAME];
        if (!krul_args_get_string(args, "name", name, sizeof(name))) {
            krul_error_set(error, KRUL_ERROR_INVALID_REQUEST,
                           "Invalid command name");
            return false;
        }
        const krul_command_t* described = krul_find_command(server, name);
        if (described == NULL) {
            krul_error_set(error, KRUL_ERROR_UNKNOWN_COMMAND,
                           "Unknown command '%s'", name);
            return false;
        }
        return serialize_command(writer, described);
    }
    krul_error_set(error, KRUL_ERROR_EXECUTION,
                   "Unknown built-in command implementation");
    return false;
}
