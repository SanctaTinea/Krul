#include "krul_internal.h"

/*
 * Проверяемая потоковая запись результата обработчика.
 *
 * Каждый krul_result_* сначала сопоставляет имя с дескриптором, проверяет тип,
 * диапазон и повторную запись, и только затем вызывает запись serde. Стек
 * result_frame_t повторяет вложенность массивов/объектов и позволяет проверить,
 * что обработчик закрыл контейнеры и заполнил все обязательные поля.
 */

#include <math.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

void krul_result_fail(krul_result_t* result, const char* message_format, ...) {
    if (result == NULL || result->failed) return;
    /* Первая ошибка «прилипает», чтобы последующие записи не скрыли причину. */
    result->failed = true;
    if (result->error == NULL) return;
    result->error->code = KRUL_ERROR_INVALID_RESULT;
    va_list args;
    va_start(args, message_format);
    (void)vsnprintf(result->error->message, sizeof(result->error->message),
                    message_format, args);
    va_end(args);
}

bool krul_result_object_complete(const result_frame_t* frame) {
    /* Для объекта до 64 полей ожидаем, что установлены все биты seen. */
    uint64_t expected = frame->field_count == 64U
                            ? UINT64_MAX
                            : ((UINT64_C(1) << frame->field_count) - 1U);
    return frame->seen == expected;
}

static const krul_field_desc_t* result_prepare(krul_result_t* result,
                                               const char* name,
                                               krul_type_t expected) {
    /* Общая точка контроля имени/типа/дубликата для всех put-функций. */
    if (result == NULL || result->failed || result->depth == 0U) return NULL;
    result_frame_t* frame = &result->frames[result->depth - 1U];
    const krul_field_desc_t* field = NULL;
    if (frame->kind == RESULT_FRAME_OBJECT) {
        uint16_t index = 0U;
        field = krul_find_field(frame->fields, frame->field_count, name, &index);
        if (field == NULL) {
            krul_result_fail(result, "Handler returned unknown field '%s'",
                             name != NULL ? name : "<null>");
            return NULL;
        }
        uint64_t bit = UINT64_C(1) << index;
        if ((frame->seen & bit) != 0U) {
            krul_result_fail(result, "Handler returned field '%s' twice", name);
            return NULL;
        }
        frame->seen |= bit;
    } else {
        if (name != NULL) {
            krul_result_fail(result, "Named value used inside result array");
            return NULL;
        }
        field = frame->element;
        ++frame->item_count;
    }
    if (field == NULL || field->type != expected) {
        krul_result_fail(result, "Handler returned wrong type for '%s'",
                         field != NULL ? krul_field_name(field) : "array item");
        return NULL;
    }
    return field;
}

static const serde_key_t* result_key(const krul_field_desc_t* field,
                                     serde_key_t* key) {
    if (field->name == NULL) return NULL;
    *key = krul_field_key(field);
    return key;
}

static bool validate_direct_result(krul_result_t* result,
                                   const krul_field_desc_t* field,
                                   krul_value_ref_t value) {
    /* Пользовательский валидатор получает непосредственное значение, потому что результат не имеет узла. */
    if (field->validate == NULL) return true;
    value.desc = field;
    value.present = true;
    value.direct = true;
    if (!field->validate(&value, result->error, field->validate_context)) {
        if (result->error == NULL || result->error->message[0] == '\0')
            krul_result_fail(result,
                             "Custom constraint rejected field '%s'",
                             krul_field_name(field));
        else {
            result->error->code = KRUL_ERROR_INVALID_RESULT;
            result->failed = true;
        }
        return false;
    }
    return true;
}

bool krul_result_put_i32(krul_result_t* result, const char* name, int32_t value) {
    const krul_field_desc_t* field = result_prepare(result, name, KRUL_TYPE_I32);
    if (field == NULL) return false;
    if (field->has_constraints &&
        (value < field->constraints.i32.min ||
         value > field->constraints.i32.max)) {
        krul_result_fail(result, "Handler returned out-of-range field '%s'",
                         krul_field_name(field));
        return false;
    }
    if (!validate_direct_result(result, field,
                                (krul_value_ref_t){.value.i32 = value}))
        return false;
    serde_key_t key;
    if (!serde_put_i32(result->writer, result_key(field, &key), value)) {
        krul_result_fail(result, "Serializer rejected field '%s'",
                         krul_field_name(field));
        return false;
    }
    return true;
}

bool krul_result_put_u32(krul_result_t* result, const char* name, uint32_t value) {
    const krul_field_desc_t* field = result_prepare(result, name, KRUL_TYPE_U32);
    if (field == NULL) return false;
    if (field->has_constraints &&
        (value < field->constraints.u32.min ||
         value > field->constraints.u32.max)) {
        krul_result_fail(result, "Handler returned out-of-range field '%s'",
                         krul_field_name(field));
        return false;
    }
    if (!validate_direct_result(result, field,
                                (krul_value_ref_t){.value.u32 = value}))
        return false;
    serde_key_t key;
    if (!serde_put_u32(result->writer, result_key(field, &key), value)) {
        krul_result_fail(result, "Serializer rejected field '%s'",
                         krul_field_name(field));
        return false;
    }
    return true;
}

bool krul_result_put_f32(krul_result_t* result, const char* name, float value) {
    const krul_field_desc_t* field = result_prepare(result, name, KRUL_TYPE_F32);
    if (field == NULL) return false;
    if (!isfinite(value) ||
        (field->has_constraints &&
         (value < field->constraints.f32.min ||
          value > field->constraints.f32.max))) {
        krul_result_fail(result, "Handler returned invalid float field '%s'",
                         krul_field_name(field));
        return false;
    }
    if (!validate_direct_result(result, field,
                                (krul_value_ref_t){.value.f32 = value}))
        return false;
    serde_key_t key;
    if (!serde_put_f32(result->writer, result_key(field, &key), value)) {
        krul_result_fail(result, "Serializer rejected field '%s'",
                         krul_field_name(field));
        return false;
    }
    return true;
}

bool krul_result_put_bool(krul_result_t* result, const char* name, bool value) {
    const krul_field_desc_t* field =
        result_prepare(result, name, KRUL_TYPE_BOOL);
    if (field == NULL) return false;
    if (!validate_direct_result(result, field,
                                (krul_value_ref_t){.value.boolean = value}))
        return false;
    serde_key_t key;
    if (!serde_put_bool(result->writer, result_key(field, &key), value)) {
        krul_result_fail(result, "Serializer rejected field '%s'",
                         krul_field_name(field));
        return false;
    }
    return true;
}

bool krul_result_put_enum(krul_result_t* result, const char* name,
                          int32_t value) {
    const krul_field_desc_t* field =
        result_prepare(result, name, KRUL_TYPE_ENUM);
    if (field == NULL) return false;
    bool matched = false;
    for (uint16_t index = 0U;
         index < field->constraints.enumeration.count; ++index) {
        if (value == field->constraints.enumeration.values[index].value) {
            matched = true;
            break;
        }
    }
    if (!matched) {
        krul_result_fail(result, "Handler returned invalid enum field '%s'",
                         krul_field_name(field));
        return false;
    }
    if (!validate_direct_result(result, field,
                                (krul_value_ref_t){.value.i32 = value}))
        return false;
    serde_key_t key;
    if (!serde_put_i32(result->writer, result_key(field, &key), value)) {
        krul_result_fail(result, "Serializer rejected field '%s'",
                         krul_field_name(field));
        return false;
    }
    return true;
}

bool krul_result_put_string(krul_result_t* result, const char* name,
                        const char* value) {
    if (result == NULL || result->failed || result->depth == 0U || value == NULL)
        return false;
    result_frame_t* frame = &result->frames[result->depth - 1U];
    const krul_field_desc_t* candidate =
        frame->kind == RESULT_FRAME_ARRAY
            ? frame->element
            : krul_find_field(frame->fields, frame->field_count, name, NULL);
    if (candidate == NULL ||
        (candidate->type != KRUL_TYPE_STRING &&
         candidate->type != KRUL_TYPE_CONSOLE_STRING)) {
        krul_result_fail(result, "Handler returned wrong string field '%s'",
                         name != NULL ? name : "array item");
        return false;
    }
    const krul_field_desc_t* field =
        result_prepare(result, name, candidate->type);
    if (field == NULL) return false;
    size_t length = strlen(value);
    if (field->has_constraints) {
        uint16_t minimum = field->type == KRUL_TYPE_STRING
                               ? field->constraints.string.min_length
                               : 0U;
        uint16_t maximum = field->type == KRUL_TYPE_STRING
                               ? field->constraints.string.max_length
                               : field->constraints.console.max_length;
        if (length < minimum || length > maximum) {
            krul_result_fail(
                result, "Handler returned invalid length for field '%s'",
                krul_field_name(field));
            return false;
        }
    }
    if (!validate_direct_result(result, field,
                                (krul_value_ref_t){.value.string = value}))
        return false;
    serde_key_t key;
    if (!serde_put_string_n(result->writer, result_key(field, &key), value,
                            length)) {
        krul_result_fail(result, "Serializer rejected field '%s'",
                         krul_field_name(field));
        return false;
    }
    return true;
}

static bool result_begin_container(krul_result_t* result, const char* name,
                                   krul_type_t type) {
    /* Сначала регистрируем container как значение родителя, затем кладём его
     * собственную ожидаемую схему на стек. */
    if (result == NULL || result->depth >= KRUL_MAX_RESULT_DEPTH) {
        krul_result_fail(result, "Result nesting is too deep");
        return false;
    }
    const krul_field_desc_t* field = result_prepare(result, name, type);
    if (field == NULL) return false;
    serde_key_t key;
    bool encoded = type == KRUL_TYPE_ARRAY
                       ? serde_begin_array(result->writer,
                                           result_key(field, &key))
                       : serde_begin_object(result->writer,
                                            result_key(field, &key));
    if (!encoded) {
        krul_result_fail(result, "Serializer rejected container '%s'",
                         krul_field_name(field));
        return false;
    }
    result_frame_t* frame = &result->frames[result->depth++];
    if (type == KRUL_TYPE_ARRAY) {
        *frame = (result_frame_t){.kind = RESULT_FRAME_ARRAY,
                                  .element = field->schema.array.element,
                                  .container_desc = field};
    } else {
        *frame = (result_frame_t){
            .kind = RESULT_FRAME_OBJECT,
            .fields = field->schema.object.fields,
            .field_count = field->schema.object.count,
            .container_desc = field};
    }
    return true;
}

bool krul_result_begin_array(krul_result_t* result, const char* name) {
    return result_begin_container(result, name, KRUL_TYPE_ARRAY);
}

bool krul_result_begin_object(krul_result_t* result, const char* name) {
    return result_begin_container(result, name, KRUL_TYPE_OBJECT);
}

bool krul_result_end_array(krul_result_t* result) {
    if (result == NULL || result->failed || result->depth <= 1U) return false;
    result_frame_t* frame = &result->frames[result->depth - 1U];
    if (frame->kind != RESULT_FRAME_ARRAY) {
        krul_result_fail(result, "Mismatched result array end");
        return false;
    }
    const krul_field_desc_t* array_field = frame->container_desc;
    if (array_field != NULL && array_field->has_constraints &&
        (frame->item_count < array_field->constraints.array.min_count ||
         frame->item_count > array_field->constraints.array.max_count)) {
        krul_result_fail(result,
                         "Handler returned invalid array length for '%s'",
                         krul_field_name(array_field));
        return false;
    }
    if (array_field != NULL &&
        !validate_direct_result(
            result, array_field,
            (krul_value_ref_t){.value.count = frame->item_count}))
        return false;
    --result->depth;
    if (!serde_end_array(result->writer)) {
        krul_result_fail(result, "Serializer failed to end result array");
        return false;
    }
    return true;
}

bool krul_result_end_object(krul_result_t* result) {
    if (result == NULL || result->failed || result->depth <= 1U) return false;
    result_frame_t* frame = &result->frames[result->depth - 1U];
    if (frame->kind != RESULT_FRAME_OBJECT) {
        krul_result_fail(result, "Mismatched result object end");
        return false;
    }
    if (!krul_result_object_complete(frame)) {
        krul_result_fail(result,
                         "Handler omitted a required nested result field");
        return false;
    }
    if (frame->container_desc != NULL &&
        !validate_direct_result(
            result, frame->container_desc,
            (krul_value_ref_t){.value.count = frame->field_count}))
        return false;
    --result->depth;
    if (!serde_end_object(result->writer)) {
        krul_result_fail(result, "Serializer failed to end result object");
        return false;
    }
    return true;
}

/*
 * Тегированное объединение кодируется как объект: поле-дискриминатор плюс поля
 * выбранного варианта. Тег пишется напрямую (вне маски seen), после чего кадр
 * становится обычным объектом с полями варианта.
 */
bool krul_result_begin_union(krul_result_t* result, const char* name) {
    if (result == NULL || result->depth >= KRUL_MAX_RESULT_DEPTH) {
        krul_result_fail(result, "Result nesting is too deep");
        return false;
    }
    const krul_field_desc_t* field =
        result_prepare(result, name, KRUL_TYPE_UNION);
    if (field == NULL) return false;
    serde_key_t key;
    if (!serde_begin_object(result->writer, result_key(field, &key))) {
        krul_result_fail(result, "Serializer rejected union '%s'",
                         krul_field_name(field));
        return false;
    }
    result_frame_t* frame = &result->frames[result->depth++];
    *frame = (result_frame_t){.kind = RESULT_FRAME_OBJECT,
                              .container_desc = field};
    return true;
}

bool krul_result_union_tag(krul_result_t* result, int32_t tag) {
    if (result == NULL || result->failed || result->depth == 0U) return false;
    result_frame_t* frame = &result->frames[result->depth - 1U];
    const krul_field_desc_t* union_field = frame->container_desc;
    if (frame->kind != RESULT_FRAME_OBJECT || union_field == NULL ||
        union_field->type != KRUL_TYPE_UNION || frame->union_tagged) {
        krul_result_fail(result, "union tag used outside a fresh union");
        return false;
    }
    const krul_field_desc_t* tag_field = union_field->schema.tagged_union.tag;
    bool known = false;
    for (uint16_t index = 0U;
         index < tag_field->constraints.enumeration.count; ++index) {
        if (tag_field->constraints.enumeration.values[index].value == tag) {
            known = true;
            break;
        }
    }
    const krul_variant_desc_t* variant = NULL;
    for (uint16_t index = 0U;
         index < union_field->schema.tagged_union.variant_count; ++index) {
        if (union_field->schema.tagged_union.variants[index].tag == tag) {
            variant = &union_field->schema.tagged_union.variants[index];
            break;
        }
    }
    if (!known || variant == NULL) {
        krul_result_fail(result, "Handler returned unknown union tag %ld",
                         (long)tag);
        return false;
    }
    serde_key_t key = krul_field_key(tag_field);
    if (!serde_put_i32(result->writer, &key, tag)) {
        krul_result_fail(result, "Serializer rejected union tag");
        return false;
    }
    frame->union_tagged = true;
    frame->fields = variant->fields;
    frame->field_count = variant->field_count;
    frame->seen = 0U;
    return true;
}

bool krul_result_end_union(krul_result_t* result) {
    if (result == NULL || result->failed || result->depth <= 1U) return false;
    result_frame_t* frame = &result->frames[result->depth - 1U];
    if (frame->kind != RESULT_FRAME_OBJECT || frame->container_desc == NULL ||
        frame->container_desc->type != KRUL_TYPE_UNION ||
        !frame->union_tagged) {
        krul_result_fail(result, "Mismatched union end");
        return false;
    }
    if (!krul_result_object_complete(frame)) {
        krul_result_fail(result,
                         "Handler omitted a required union variant field");
        return false;
    }
    if (frame->container_desc != NULL &&
        !validate_direct_result(
            result, frame->container_desc,
            (krul_value_ref_t){.value.count = frame->field_count}))
        return false;
    --result->depth;
    if (!serde_end_object(result->writer)) {
        krul_result_fail(result, "Serializer failed to end result union");
        return false;
    }
    return true;
}

bool krul_result_ok(const krul_result_t* result) {
    return result != NULL && !result->failed && serde_writer_ok(result->writer);
}

bool krul_result_defer(krul_result_t* result, void* context,
                       krul_completion_t completion,
                       krul_context_release_t release,
                       krul_pending_handle_t* handle) {
    /* Отложить можно только пустой результат: текущая запись принадлежит диспетчеризации,
     * и после возврата обработчика её буфер разрешено переиспользовать. */
    if (result == NULL || completion == NULL || handle == NULL || result->failed ||
        result->depth != 1U || result->frames[0].seen != 0U ||
        result->server == NULL || result->command == NULL ||
        result->deferred != NULL) {
        krul_result_fail(result, "A deferred result must be declared before writing");
        return false;
    }
    result->deferred = krul_pending_allocate(result->server, 
                                            result->command, 
                                            result->transaction_id, 
                                            context,
                                            completion, 
                                            release, 
                                            handle);

    if (result->deferred != NULL) return true;
    result->failed = true;
    krul_error_set(result->error, KRUL_ERROR_EXECUTION, "No deferred response slot is available");
    return false;
}
