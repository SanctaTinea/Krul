#pragma once

/*
 * Внутренние структуры KRUL. Они скрыты от обработчика, чтобы прикладной код не мог
 * обойти проверку схемы и писать напрямую средствами записи serde.
 */

#include "krul.h"

typedef enum { RESULT_FRAME_OBJECT, RESULT_FRAME_ARRAY } result_frame_kind_t;

typedef struct {
    /* Один уровень стека потоковой записи результата. */
    result_frame_kind_t kind;
    const krul_field_desc_t* fields;
    uint16_t field_count;
    const krul_field_desc_t* element;
    const krul_field_desc_t* container_desc;
    /* Для контейнера KRUL_TYPE_UNION: выбран ли уже вариант тегом. */
    bool union_tagged;
    /* Бит на каждое поле объекта: обнаруживает дубликаты и пропуски. */
    uint64_t seen;
    size_t item_count;
} result_frame_t;

struct krul_args {
    /* params может отсутствовать целиком, если все поля имеют значения по умолчанию. */
    serde_codec_t codec;
    serde_node_t params;
    const krul_command_t* command;
    bool params_present;
};

struct krul_result {
    /* Стек ограничивает рекурсию и хранит ожидаемую схему каждого контейнера. */
    serde_writer_t writer;
    result_frame_t frames[KRUL_MAX_RESULT_DEPTH];
    uint8_t depth;
    bool failed;
    krul_error_t* error;
    krul_server_t* server;
    const krul_command_t* command;
    uint32_t transaction_id;
    /* Ненулевой указатель означает, что запись пока не должна завершаться. */
    krul_pending_slot_t* deferred;
};

static inline serde_key_t krul_named_key(const char* name) {
    return (serde_key_t){.name = name, .tag = serde_key_tag(name)};
}

static inline serde_key_t krul_field_key(const krul_field_desc_t* field) {
    return (serde_key_t){.name = field->name,
                         .tag = field->tag != 0U
                                    ? field->tag
                                    : serde_key_tag(field->name)};
}

static inline uint16_t krul_field_tag(const krul_field_desc_t* field) {
    return field->tag != 0 ? field->tag : serde_key_tag(field->name);
}

const krul_field_desc_t* krul_find_field(const krul_field_desc_t* fields,
                                         uint16_t count, const char* name,
                                         uint16_t* found_index);
const char* krul_field_name(const krul_field_desc_t* field);

bool krul_validate_input_object(serde_codec_t codec, serde_node_t object,
                                const krul_field_desc_t* fields,
                                uint16_t field_count, krul_error_t* error);
bool krul_builtin_dispatch(const krul_server_t* server,
                           const krul_command_t* command,
                           const krul_args_t* args, serde_writer_t writer,
                           krul_error_t* error);

void krul_result_fail(krul_result_t* result, const char* message_format, ...);
bool krul_result_object_complete(const result_frame_t* frame);
krul_pending_slot_t* krul_pending_allocate(
    krul_server_t* server, const krul_command_t* command, uint32_t id,
    void* context, krul_completion_t completion,
    krul_context_release_t release, krul_pending_handle_t* handle);
