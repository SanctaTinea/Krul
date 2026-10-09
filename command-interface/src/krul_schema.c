#include "krul_internal.h"

/*
 * Статическая проверка таблиц дескрипторов при krul_server_init().
 *
 * Здесь проверяется не запрос, а корректность самой прошивки: уникальность
 * команд/полей, согласованность объединения с type, диапазоны, значения по умолчанию и рекурсивные
 * схемы container. Если таблица противоречива, сервер вообще не запускается.
 */

#include <math.h>
#include <string.h>

static bool descriptor_valid(const krul_field_desc_t* field, bool output,
                             uint8_t depth);

/* Поля варианта объединения: непустые, с заданными уникальными именами. */
static bool variant_fields_valid(const krul_field_desc_t* fields,
                                 uint16_t count, bool output, uint8_t depth) {
    if (count == 0U || fields == NULL || count > KRUL_MAX_FIELDS_PER_OBJECT)
        return false;
    for (uint16_t outer = 0U; outer < count; ++outer) {
        if (fields[outer].name == NULL ||
            !descriptor_valid(&fields[outer], output, depth))
            return false;
        for (uint16_t inner = (uint16_t)(outer + 1U); inner < count; ++inner) {
            if (strcmp(fields[outer].name, fields[inner].name) == 0 ||
                krul_field_tag(&fields[outer]) ==
                    krul_field_tag(&fields[inner]))
                return false;
        }
    }
    return true;
}

static bool descriptor_valid(const krul_field_desc_t* field, bool output,
                             uint8_t depth) {
    /* Результат не может иметь default: обработчик обязан явно вернуть каждое поле. */
    if (field == NULL || depth >= KRUL_MAX_RESULT_DEPTH ||
        (output && field->has_default))
        return false;
    if (field->has_constraints) {
        switch (field->type) {
            case KRUL_TYPE_I32:
                if (field->constraints.i32.min > field->constraints.i32.max)
                    return false;
                break;
            case KRUL_TYPE_U32:
                if (field->constraints.u32.min > field->constraints.u32.max)
                    return false;
                break;
            case KRUL_TYPE_F32:
                if (!isfinite(field->constraints.f32.min) ||
                    !isfinite(field->constraints.f32.max) ||
                    !isfinite(field->constraints.f32.step) ||
                    field->constraints.f32.min > field->constraints.f32.max ||
                    field->constraints.f32.step < 0.0F)
                    return false;
                break;
            case KRUL_TYPE_STRING:
                if (field->constraints.string.min_length >
                    field->constraints.string.max_length)
                    return false;
                break;
            case KRUL_TYPE_ARRAY:
                if (field->constraints.array.min_count >
                    field->constraints.array.max_count)
                    return false;
                break;
            default: break;
        }
    }
    /* Контейнеры проверяются рекурсивно. Элемент массива безымянный, поля объекта
     * именованные — это соответствует правилам потоковой записи. */
    if (field->type == KRUL_TYPE_ENUM) {
        if (!field->has_constraints ||
            field->constraints.enumeration.values == NULL ||
            field->constraints.enumeration.count == 0U)
            return false;
        for (uint16_t index = 0U; index < field->constraints.enumeration.count;
             ++index) {
            const char* title =
                field->constraints.enumeration.values[index].title;
            if (title == NULL) return false;
            for (uint16_t inner = (uint16_t)(index + 1U);
                 inner < field->constraints.enumeration.count; ++inner)
                if (field->constraints.enumeration.values[index].value ==
                    field->constraints.enumeration.values[inner].value)
                    return false;
        }
    } else if (field->type == KRUL_TYPE_ARRAY) {
        if (field->schema.array.element == NULL ||
            field->schema.array.element->name != NULL ||
            !descriptor_valid(field->schema.array.element, output,
                              (uint8_t)(depth + 1U)))
            return false;
    } else if (field->type == KRUL_TYPE_OBJECT) {
        if (field->schema.object.count > KRUL_MAX_FIELDS_PER_OBJECT ||
            (field->schema.object.count > 0U &&
             field->schema.object.fields == NULL))
            return false;
        for (uint16_t index = 0U; index < field->schema.object.count; ++index) {
            if (field->schema.object.fields[index].name == NULL ||
                !descriptor_valid(&field->schema.object.fields[index], output,
                                  (uint8_t)(depth + 1U)))
                return false;
            for (uint16_t inner = (uint16_t)(index + 1U);
                 inner < field->schema.object.count; ++inner) {
                const krul_field_desc_t* left =
                    &field->schema.object.fields[index];
                const krul_field_desc_t* right =
                    &field->schema.object.fields[inner];
                if (strcmp(left->name, right->name) == 0 ||
                    (left->tag != 0U && left->tag == right->tag))
                    return false;
            }
        }
    } else if (field->type == KRUL_TYPE_UNION) {
        /* Объединения допустимы только в результате (ввод их не разбирает). */
        const krul_field_desc_t* tag = field->schema.tagged_union.tag;
        if (!output || tag == NULL || tag->type != KRUL_TYPE_ENUM ||
            !descriptor_valid(tag, output, (uint8_t)(depth + 1U)))
            return false;
        uint16_t variant_count = field->schema.tagged_union.variant_count;
        const krul_variant_desc_t* variants =
            field->schema.tagged_union.variants;
        if (variant_count == 0U || variants == NULL) return false;
        for (uint16_t index = 0U; index < variant_count; ++index) {
            if (!variant_fields_valid(variants[index].fields,
                                      variants[index].field_count, output,
                                      (uint8_t)(depth + 1U)))
                return false;
            bool tag_declared = false;
            for (uint16_t value = 0U;
                 value < tag->constraints.enumeration.count; ++value) {
                if (tag->constraints.enumeration.values[value].value ==
                    variants[index].tag)
                    tag_declared = true;
            }
            if (!tag_declared) return false;
            for (uint16_t other = (uint16_t)(index + 1U);
                 other < variant_count; ++other) {
                if (variants[index].tag == variants[other].tag) return false;
            }
        }
    }
    /* Значение по умолчанию обязано проходить те же ограничения, что и вход
     * от клиента. */
    if (field->has_default) {
        switch (field->type) {
            case KRUL_TYPE_I32:
                if (field->has_constraints &&
                    (field->default_value.i32 < field->constraints.i32.min ||
                     field->default_value.i32 > field->constraints.i32.max))
                    return false;
                break;
            case KRUL_TYPE_U32:
                if (field->has_constraints &&
                    (field->default_value.u32 < field->constraints.u32.min ||
                     field->default_value.u32 > field->constraints.u32.max))
                    return false;
                break;
            case KRUL_TYPE_F32:
                if (!isfinite(field->default_value.f32) ||
                    (field->has_constraints &&
                     (field->default_value.f32 < field->constraints.f32.min ||
                      field->default_value.f32 > field->constraints.f32.max)))
                    return false;
                break;
            case KRUL_TYPE_STRING:
                if (field->default_value.string == NULL ||
                    (field->has_constraints &&
                     (strlen(field->default_value.string) <
                          field->constraints.string.min_length ||
                      strlen(field->default_value.string) >
                          field->constraints.string.max_length)))
                    return false;
                break;
            case KRUL_TYPE_ENUM: {
                bool found = false;
                for (uint16_t index = 0U;
                     index < field->constraints.enumeration.count; ++index) {
                    if (field->default_value.i32 ==
                        field->constraints.enumeration.values[index].value)
                        found = true;
                }
                if (!found) return false;
                break;
            }
            case KRUL_TYPE_ARRAY:
                break;
            case KRUL_TYPE_BOOL:
                break;
            default:
                return false;
        }
        if (field->validate != NULL) {
            krul_value_ref_t value = {
                .desc = field, .present = true, .direct = true};
            switch (field->type) {
                case KRUL_TYPE_I32:
                    value.value.i32 = field->default_value.i32;
                    break;
                case KRUL_TYPE_U32:
                    value.value.u32 = field->default_value.u32;
                    break;
                case KRUL_TYPE_F32:
                    value.value.f32 = field->default_value.f32;
                    break;
                case KRUL_TYPE_BOOL:
                    value.value.boolean = field->default_value.boolean;
                    break;
                case KRUL_TYPE_STRING:
                    value.value.string = field->default_value.string;
                    break;
                case KRUL_TYPE_ENUM:
                    value.value.i32 = field->default_value.i32;
                    break;
                case KRUL_TYPE_ARRAY:
                    value.value.count = 0U;
                    break;
                default:
                    return false;
            }
            krul_error_t error = {0};
            if (!field->validate(&value, &error, field->validate_context))
                return false;
        }
    }
    return true;
}

static bool field_list_valid(const krul_field_desc_t* fields, uint16_t count,
                             bool output) {
    /* Предел 64 связан с битовой маской seen во время выполнения/в результате. */
    if (count > KRUL_MAX_FIELDS_PER_OBJECT || (count > 0U && fields == NULL))
        return false;
    for (uint16_t outer = 0U; outer < count; ++outer) {
        if (fields[outer].name == NULL ||
            !descriptor_valid(&fields[outer], output, 0U))
            return false;
        for (uint16_t inner = (uint16_t)(outer + 1U); inner < count; ++inner) {
            if (strcmp(fields[outer].name, fields[inner].name) == 0 ||
                krul_field_tag(&fields[outer]) ==
                    krul_field_tag(&fields[inner]))
                return false;
        }
    }
    return true;
}

bool krul_server_init(krul_server_t* server,
                      const krul_server_config_t* config) {
    /* Копирование config не копирует таблицы: они обычно const и лежат во flash-памяти. */
    if (server == NULL || config == NULL || config->commands == NULL ||
        config->command_count == 0U || config->device_name == NULL ||
        config->firmware_version == NULL || config->protocol_version == 0U ||
        config->codec.mtab == NULL || config->response_buffer == NULL ||
        config->response_capacity == 0U ||
        ((config->pending_slot_count > 0U) &&
         (config->pending_slots == NULL || config->completion_queue == NULL)))
        return false;
    for (size_t outer = 0U; outer < config->command_count; ++outer) {
        const krul_command_t* command = config->commands[outer];
        if (command == NULL || command->name == NULL ||
            !field_list_valid(command->params, command->params_count, false) ||
            !field_list_valid(command->result, command->result_count, true) ||
            (command->type != KRUL_CMD_NORMAL &&
             command->type != KRUL_CMD_BUILTIN &&
             command->type != KRUL_CMD_NOGUI) ||
            (command->type != KRUL_CMD_BUILTIN && command->handler == NULL))
            return false;
        for (size_t inner = outer + 1U; inner < config->command_count; ++inner) {
            if (config->commands[inner] != NULL &&
                strcmp(command->name, config->commands[inner]->name) == 0)
                return false;
        }
    }
    *server = (krul_server_t){.config = *config,
                              .response_slot = UINT16_MAX,
                              .initialized = true};
    for (uint16_t index = 0U; index < config->pending_slot_count; ++index)
        config->pending_slots[index] = (krul_pending_slot_t){0};
    return true;
}
