#pragma once

/**
 * @file krul.h
 * @brief Диспетчер команд без кучи, не зависящий от транспорта и формата.
 *
 * Krul проверяет декодированный запрос по декларативным дескрипторам команд,
 * вызывает обработчик приложения, проверяет его результат и сериализует ответ
 * через serde. Обработчик может завершиться сразу или отложить завершение в
 * ограниченный слот, принадлежащий серверу.
 *
 * @see request_lifecycle
 * @see getting_started
 */

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "serde.h"

#ifdef __cplusplus
extern "C" {
#endif

/** @defgroup krul Диспетчер команд Krul
 * @brief Декларативная диспетчеризация команд с ограниченным отложенным выполнением.
 * @{ */

/** Число элементов в C-массиве, известном во время компиляции. */
#define KRUL_ARRAY_SIZE(value) (sizeof(value) / sizeof((value)[0]))
/** Ёмкость обычного сообщения об ошибке с учётом завершающего нуля. */
#define KRUL_MAX_ERROR_MESSAGE 192
/** Ёмкость ошибки, сохраняемой в отложенном слоте. */
#define KRUL_MAX_PENDING_ERROR_MESSAGE 96
/** Ёмкость временного форматированного сообщения журнала. */
#define KRUL_MAX_LOG_MESSAGE 384
/** Максимальное число отслеживаемых полей в одном объекте результата. */
#define KRUL_MAX_FIELDS_PER_OBJECT 64
/** Максимальная глубина вложенности, допустимая для записи результата. */
#define KRUL_MAX_RESULT_DEPTH 8

/** Семантические типы полей, поддерживаемые схемами команд Krul. */
typedef enum {
    KRUL_TYPE_I32,
    KRUL_TYPE_U32,
    KRUL_TYPE_F32,
    KRUL_TYPE_STRING,
    KRUL_TYPE_BOOL,
    KRUL_TYPE_ENUM,
    KRUL_TYPE_ARRAY,
    KRUL_TYPE_OBJECT,
    /** Строка результата, которую клиент отображает в консоли с заданной важностью. */
    KRUL_TYPE_CONSOLE_STRING,
    /**
     * Тегированное объединение (sum type): поле несёт дискриминатор-enum и
     * полезную нагрузку ровно одного варианта. Допустимо только в результате.
     */
    KRUL_TYPE_UNION
} krul_type_t;

/** Необязательная подсказка отображения поля или команды в UI; не влияет на runtime-проверку. */
typedef enum {
    KRUL_WIDGET_DEFAULT,
    KRUL_WIDGET_SLIDER,
    KRUL_WIDGET_SPINBOX,
    KRUL_WIDGET_SPECIAL_ADC,
    KRUL_WIDGET_SPECIAL_ADC_GROUP,
    KRUL_WIDGET_SPECIAL_GPIO,
    KRUL_WIDGET_SPECIAL_DAC,
    KRUL_WIDGET_SPECIAL_PWM,
} krul_widget_t;

/** Определяет назначение команды и её видимость в клиентском GUI. */
typedef enum {
    KRUL_CMD_NORMAL,
    KRUL_CMD_BUILTIN,
    KRUL_CMD_NOGUI,
} krul_cmd_type_t;

/** Уровень важности событий консоли и журнала. */
typedef enum {
    KRUL_CONSOLE_DEBUG,
    KRUL_CONSOLE_INFO,
    KRUL_CONSOLE_WARNING,
    KRUL_CONSOLE_ERROR
} krul_console_type_t;

/** Стабильные коды состояния и ошибок протокола, возвращаемые клиентам. */
typedef enum {
    KRUL_OK = 0,
    KRUL_ERROR_MISSING_FIELD = 1,
    KRUL_ERROR_INVALID_TYPE = 2,
    KRUL_ERROR_OUT_OF_RANGE = 3,
    KRUL_ERROR_MALFORMED_PAYLOAD = 4,
    KRUL_ERROR_INVALID_REQUEST = 5,
    KRUL_ERROR_UNKNOWN_COMMAND = 6,
    KRUL_ERROR_EXECUTION = 7,
    KRUL_ERROR_RESPONSE_TOO_LARGE = 8,
    KRUL_ERROR_INVALID_RESULT = 9
} krul_status_t;

/** Одно значение в канале связи и его необязательный человекочитаемый заголовок. */
typedef struct {
    /** Стабильный числовой код в JSON, BSON, CBOR и прошивке. */
    int32_t value;
    /** Необязательная человекочитаемая подпись для клиентского UI. */
    const char* title;
} krul_enum_value_t;

/** Хранилище типизированного значения поля по умолчанию. */
typedef union {
    int32_t i32;
    uint32_t u32;
    float f32;
    const char* string;
    bool boolean;
} krul_default_value_t;

typedef struct krul_field_desc krul_field_desc_t;
typedef struct krul_error krul_error_t;

/** Один вариант тегированного объединения: значение тега и его поля. */
typedef struct {
    int32_t tag;                      /**< Дискриминатор, выбирающий вариант. */
    const krul_field_desc_t* fields;  /**< Поля полезной нагрузки варианта. */
    uint16_t field_count;             /**< Число полей варианта. */
} krul_variant_desc_t;

/** Значение, передаваемое функции обратного вызова пользовательского ограничения. */
typedef struct {
    /*
     * Универсальное представление для пользовательского валидатора.
     * direct=false: значение ещё находится в serde_node_t входного сообщения.
     * direct=true: проверяется готовое значение default/result из объединения value.
     */
    serde_codec_t codec;
    serde_node_t node;
    const krul_field_desc_t* desc;
    bool present;
    bool direct;
    union {
        int32_t i32;
        uint32_t u32;
        float f32;
        bool boolean;
        const char* string;
        size_t count;
    } value;
} krul_value_ref_t;

/**
 * @brief Дополнительное ограничение поля, проверяемое после встроенной проверки.
 * @param value Заимствованное типизированное представление проверяемого значения.
 * @param error Объект ошибки, который функция обратного вызова может заполнить при отказе.
 * @param context Непрозрачный указатель из krul_field_desc_t::validate_context.
 * @return `true`, если значение корректно; иначе установите @p error и верните
 * `false`.
 */
typedef bool (*krul_constraint_fn)(const krul_value_ref_t* value,
                                   krul_error_t* error, void* context);

/** Код ошибки протокола и диагностическое сообщение с завершающим нулём. */
struct krul_error {
    krul_status_t code;                    /**< Стабильное состояние протокола. */
    char message[KRUL_MAX_ERROR_MESSAGE];  /**< Диагностика с завершающим нулём. */
};

/**
 * @brief Декларативная схема параметра, поля результата или элемента массива.
 *
 * Деревья дескрипторов и все строки и таблицы, на которые они ссылаются,
 * должны оставаться действительными всё время жизни сервера. Активные члены
 * schema, constraints и default_value выбираются полем `type`.
 */
struct krul_field_desc {
    const char* name;       /**< Имя в канале связи; NULL для элемента массива. */
    const char* label;      /**< Необязательная человекочитаемая подпись клиента. */
    uint16_t tag;           /**< Необязательный ключ для компактных двоичных кодеков. */
    krul_type_t type;       /**< Семантический тип значения. */
    krul_widget_t widget;   /**< Необязательная подсказка UI клиента. */
    bool has_default;       /**< False делает входной параметр обязательным. */
    bool has_constraints;   /**< Включает соответствующую ветку ограничений. */
    krul_default_value_t default_value; /**< Входное значение по умолчанию, если включено. */
    union {
        /* Рекурсивная схема контейнеров или оформление console_string. */
        struct {                         // массив
            const krul_field_desc_t* element;
        } array;
        struct {                         // map
            const krul_field_desc_t* fields;
            uint16_t count;
        } object;
        struct {
            krul_console_type_t severity; // хранит уровень severity для console_string, который я думал вырезать
        } console;
        struct {                         // тегированное объединение
            const krul_field_desc_t* tag;
            const krul_variant_desc_t* variants;
            uint16_t variant_count;
        } tagged_union;
    } schema;
    // Ограничения хранятся в объединении и имеют смысл только при
    // has_constraints = true.
    union {
        /* Используемая ветка определяется полем type. */
        struct {
            int32_t min;
            int32_t max;
        } i32;
        struct {
            uint32_t min;
            uint32_t max;
        } u32;
        struct {
            float min;
            float max;
            float step;
        } f32;
        struct {
            uint16_t min_length;
            uint16_t max_length;
        } string;
        struct {
            const krul_enum_value_t* values;
            uint16_t count;
        } enumeration;
        struct {
            uint16_t min_count;
            uint16_t max_count;
        } array;
        struct {
            uint16_t max_length;
        } console;
    } constraints;
    krul_constraint_fn validate; /**< Необязательное ограничение приложения. */
    void* validate_context;      /**< Непрозрачный аргумент для `validate`. */
};

typedef struct krul_args krul_args_t;
typedef struct krul_result krul_result_t;
typedef struct krul_server krul_server_t;
typedef struct krul_command krul_command_t;
typedef struct krul_pending_slot krul_pending_slot_t;

/**
 * @brief Обработчик команды приложения.
 *
 * @param args Заимствованные проверенные аргументы, действительные только во время вызова.
 * @param result Защищённая запись результата для немедленного или отложенного завершения.
 * @param error Объект ошибки, заполняемый перед возвратом `false`.
 *
 * Верните `true` после записи немедленного результата или успешного вызова
 * krul_result_defer(). При ошибке верните `false` и при необходимости задайте
 * @p error.
 * @return `true`, если команда успешно завершена или отложена.
 */
typedef bool (*krul_handler_t)(const krul_args_t* args,
                               krul_result_t* result, krul_error_t* error);

/** Полное декларативное описание одной команды. */
struct krul_command {
    /* Уникальное wire-имя команды. */
    const char* name;       /**< Уникальное имя команды в канале связи. */
    krul_cmd_type_t type;   /**< Обычная, встроенная или скрытая в GUI команда. */
    /* Следующие четыре поля используются GUI-клиентом для компоновки UI. */
    const char* tab;        /**< Необязательная вкладка UI клиента. */
    const char* title;      /**< Необязательный человекочитаемый заголовок. */
    const char* description; /**< Необязательная подсказка после заголовка. */
    const char* group;      /**< Необязательная группа UI клиента. */
    krul_widget_t widget;   /**< Необязательная подсказка представления команды. */
    uint16_t order;         /**< Порядок сортировки в UI клиента. */
    /* Схемы входного объекта params и выходного объекта result. */
    const krul_field_desc_t* params; /**< Дескрипторы входных полей. */
    uint16_t params_count;           /**< Число входных дескрипторов. */
    const krul_field_desc_t* result; /**< Дескрипторы выходных полей. */
    uint16_t result_count;           /**< Число выходных дескрипторов. */
    /* Метаданные периодического опроса; сам KRUL таймеры не запускает. */
    bool autoupdate;             /**< Разрешён ли автоматический опрос клиентами. */
    uint32_t min_period_ms;      /**< Минимальный объявленный период опроса. */
    uint32_t max_period_ms;      /**< Максимальный объявленный период опроса. */
    uint32_t default_period_ms;  /**< Рекомендуемый период опроса. */
    /* Рекомендуемый клиентский таймаут; 0 означает стандарт клиента. */
    // сколько клиент должен ждать ответа от прошивки, прежде чем считать, что команда не удалась
    uint32_t timeout_ms;         /**< Таймаут клиента; ноль означает стандартный. */
    krul_handler_t handler;      /**< Реализация приложения. */
};

/**
 * @brief Сериализовать окончательный результат отложенной команды.
 * @param result Запись результата, управляемая исходной схемой команды.
 * @param error Ошибка при невозможности создать корректный результат.
 * @param context Непрозрачный контекст приложения из krul_result_defer().
 * @return `true`, если результат успешно создан.
 */
typedef bool (*krul_completion_t)(krul_result_t* result,
                                  krul_error_t* error, void* context);

/**
 * @brief Освободить память приложения, удерживаемую отложенным запросом.
 * @param context Непрозрачный указатель, переданный в krul_result_defer().
 *
 * После успешного откладывания Krul вызывает эту функцию не более одного раза
 * при освобождении ответа или отмене. Функция обратного вызова может вернуть объект в пул
 * приложения; он не должен параллельно обращаться к тому же серверу.
 */
typedef void (*krul_context_release_t)(void* context);

/**
 * @brief Проверяемая по поколению ссылка на отложенный слот.
 *
 * Храните этот дескриптор в контексте операции приложения. Поколение не даёт
 * позднему завершению повлиять на новый запрос в повторно используемом слоте.
 */
typedef struct {
    uint16_t index;       /**< Индекс в массиве слотов. */
    uint16_t generation;  /**< Поколение для отклонения устаревшего дескриптора. */
} krul_pending_handle_t;

/** @return Дескриптор, который не может ссылаться на активный отложенный слот. */
static inline krul_pending_handle_t krul_pending_handle_invalid(void) {
    krul_pending_handle_t handle = {UINT16_MAX, 0U};
    return handle;
}

/** Удобный инициализатор некорректного отложенного дескриптора. */
#define KRUL_PENDING_HANDLE_INVALID krul_pending_handle_invalid()

/** Внутреннее состояние жизненного цикла предоставленного приложением слота. */
typedef enum {
    KRUL_PENDING_FREE,
    KRUL_PENDING_ACTIVE,
    KRUL_PENDING_COMPLETION_QUEUED,
    KRUL_PENDING_ENCODED
} krul_pending_state_t;

/**
 * @brief Хранилище одной отложенной команды.
 *
 * Приложение выделяет массив этих структур, но после krul_server_init() должно
 * считать их содержимое принадлежащим Krul. Аргументы и результаты приложения
 * хранятся отдельно в непрозрачном `context`.
 */
struct krul_pending_slot {
    const krul_command_t* command;
    void* context;
    krul_completion_t completion;
    krul_context_release_t release;
    uint32_t id;
    uint16_t generation;
    krul_pending_state_t state;
    bool failed;
    krul_status_t failure_code;
    char failure_message[KRUL_MAX_PENDING_ERROR_MESSAGE];
};

/**
 * @brief Неизменяемые ресурсы для инициализации сервера.
 *
 * Сама структура копируется. Все указанные буферы, таблицы дескрипторов,
 * строки, слоты и очереди должны быть действительны всё время жизни сервера.
 * При ненулевом pending_slot_count массивы pending_slots и completion_queue
 * должны содержать ровно столько элементов.
 */
typedef struct {
    const krul_command_t* const* commands; /**< Долгоживущие указатели дескрипторов. */
    size_t command_count;                 /**< Число указателей команд. */
    const char* device_name;              /**< Имя устройства для обнаружения. */
    const char* device_id;                /**< Стабильный ID экземпляра или NULL. */
    const char* firmware_version;         /**< Версия прошивки для обнаружения. */
    uint32_t protocol_version;            /**< Объявляемая версия протокола. */
    serde_codec_t codec;                  /**< Инициализированный кодек сериализации. */
    uint8_t* response_buffer;             /**< Общий временный буфер ответа. */
    size_t response_capacity;             /**< Ёмкость временного буфера в байтах. */
    krul_pending_slot_t* pending_slots;   /**< Массив отложенных слотов или NULL. */
    uint16_t pending_slot_count;          /**< Число слотов и элементов очереди. */
    uint16_t* completion_queue;           /**< FIFO-хранилище индексов слотов. */
} krul_server_config_t;

/**
 * @brief Состояние Krul во время выполнения.
 *
 * Выделяйте по одному экземпляру на независимый канал команд. После
 * инициализации обращайтесь к нему только через открытые функции ниже. Вызовы
 * не синхронизируются внутри и должны сериализоваться приложением; не вызывайте
 * API отложенных операций прямо из ISR, если сервер доступен основному контексту.
 */
struct krul_server {
    krul_server_config_t config;
    size_t response_size;
    uint16_t response_slot;
    uint16_t completion_head;
    uint16_t completion_tail;
    uint16_t completion_count;
    bool initialized;
    bool response_ready;
};

/** Результат обработки одного кадра запроса. */
typedef enum {
    KRUL_DISPATCH_RESPONSE_READY, /**< Обработан; ответ опубликован. */
    KRUL_DISPATCH_DEFERRED,       /**< Обработан; контекст принадлежит слоту. */
    KRUL_DISPATCH_BUSY,           /**< Не обработан; освободите вывод и повторите. */
    KRUL_DISPATCH_FAILED          /**< Обработан; ответ не удалось закодировать. */
} krul_dispatch_status_t;

/** Заимствованное представление проверенного параметра-массива. */
typedef struct {
    /* Не владеет данными: node и codec ссылаются на текущий декодированный запрос. */
    // он не нужен для описание команд или полей. Он для динамической обработки массивов
    serde_codec_t codec;
    serde_node_t node;
    const krul_field_desc_t* element_desc;
    bool present;
} krul_array_t;

/** Заимствованное представление проверенного параметра-объекта. */
typedef struct {
    /* Представление вложенного object вместе с его декларативной схемой. */
    // аналогично массивам
    serde_codec_t codec;
    serde_node_t node;
    const krul_field_desc_t* fields;
    uint16_t field_count;
} krul_object_t;

/** Одно скалярное поле в незапрошенном событии. */
typedef struct {
    /* Простое поле асинхронного event; контейнеры здесь намеренно не нужны. */
    const char* name;  /**< Ключ данных события. */
    krul_type_t type;  /**< Активная скалярная ветка в `value`. */
    union {
        int32_t i32;
        uint32_t u32;
        float f32;
        bool boolean;
        const char* string;
    } value;
} krul_event_field_t;

/**
 * @brief Инициализировать сервер команд и проверить конфигурацию его памяти.
 * @param server Инициализируемое runtime-состояние.
 * @param config Долгоживущие дескрипторы, кодек и память вызывающего кода.
 * @return `true`, если конфигурацию можно использовать.
 */
bool krul_server_init(krul_server_t* server,
                      const krul_server_config_t* config);

/**
 * @brief Принять и диспетчеризовать полные данные одного запроса.
 * @param server Инициализированный сервер.
 * @param request Байты данных без разделителей транспортного кадра.
 * @param request_length Размер данных в байтах.
 * @return Результат диспетчеризации. При KRUL_DISPATCH_BUSY запрос не принят;
 * его можно повторить после освобождения текущего ответа.
 *
 * Немедленные обработчики публикуют ответ до возврата. Отложенные обработчики
 * резервируют отложенный слот и возвращают KRUL_DISPATCH_DEFERRED. Входной
 * буфер заимствуется только на время этого вызова.
 */
krul_dispatch_status_t krul_dispatch(krul_server_t* server,
                                     const uint8_t* request,
                                     size_t request_length);

/**
 * @brief Передать контекст приложения свободному отложенному слоту.
 * @param result Объект результата, переданный текущему обработчику.
 * @param context Стабильное состояние аргументов/результата приложения; может
 * быть NULL, если обе функции обратного вызова намеренно это поддерживают.
 * @param completion Функция обратного вызова, позднее записывающая итоговый результат.
 * @param release Необязательная функция, вызываемая при освобождении @p context.
 * @param handle Принимает проверяемый по поколению дескриптор слота.
 * @return `true` при успехе. При ошибке владение остаётся у вызывающего кода.
 *
 * После успеха @p context принадлежит Krul до освобождения ответа или отмены.
 * Обработчик не должен использовать заимствованные данные krul_args_t как контекст.
 */
bool krul_result_defer(krul_result_t* result,
                       void* context, 
                       krul_completion_t completion,
                       krul_context_release_t release,
                       krul_pending_handle_t* handle);

/**
 * @brief Проверить, указывает ли дескриптор на слот, принадлежащий Krul.
 * @param server Инициализированный сервер, выдавший @p handle.
 * @param handle Проверяемый по поколению отложенный дескриптор.
 * @return `true` для активных, поставленных в очередь или закодированных
 * отложенных запросов; `false` для некорректных, устаревших, свободных или уже
 * освобождённых дескрипторов.
 */
bool krul_pending_is_active(const krul_server_t* server,
                         krul_pending_handle_t handle);

/**
 * @brief Проверить, может ли приложение ещё сообщить об успехе или ошибке.
 * @param server Инициализированный сервер, выдавший @p handle.
 * @param handle Проверяемый по поколению отложенный дескриптор.
 * @return `true`, только пока слот активен и ещё не поставлен в очередь.
 */
bool krul_pending_is_waiting(const krul_server_t* server,
                          krul_pending_handle_t handle);

/**
 * @brief Поставить успешное отложенное завершение в очередь.
 * @param server Инициализированный сервер, выдавший @p handle.
 * @param handle Ожидающий отложенный запрос для постановки в очередь.
 * @return `false` для некорректного/устаревшего дескриптора или завершённого слота.
 *
 * После успеха приложение не должно изменять контекст. Сериализация произойдёт
 * позднее в krul_pending_encode_next() через зарегистрированную функцию завершения.
 */
bool krul_pending_complete(krul_server_t* server,
                        krul_pending_handle_t handle);

/**
 * @brief Поставить ошибочное отложенное завершение в очередь.
 * @param server Инициализированный сервер, выдавший @p handle.
 * @param handle Ожидающий отложенный запрос для постановки в очередь.
 * @param code Стабильный код ошибки; для ошибок устройства используйте KRUL_ERROR_EXECUTION.
 * @param message Диагностика, копируемая в отложенный слот.
 * @return `true`, если ошибка поставлена в очередь; `false` для некорректных,
 * устаревших или уже завершённых дескрипторов.
 */
bool krul_pending_fail(krul_server_t* server,
                       krul_pending_handle_t handle, krul_status_t code,
                       const char* message);

/**
 * @brief Отменить отложенный запрос и освободить его контекст приложения.
 * @param server Инициализированный сервер, выдавший @p handle.
 * @param handle Активный отложенный запрос, ещё не поставленный в очередь.
 * @return `true`, если соответствующий слот отменён.
 *
 * Зарегистрированная функция освобождения вызывается до освобождения слота.
 * Поставленные в очередь или уже опубликованные завершения отменить нельзя.
 */
bool krul_pending_cancel(krul_server_t* server,
                         krul_pending_handle_t handle);

/**
 * @brief Опубликовать следующий отложенный ответ, когда временный буфер свободен.
 * @param server Обслуживаемый инициализированный сервер.
 * @return `true`, если после вызова опубликован ответ.
 *
 * Если ответ уже опубликован, функция не изменяет его и возвращает `true`.
 * Иначе кодируется не более одного завершения из очереди.
 */
bool krul_pending_encode_next(krul_server_t* server);

/**
 * @brief Проверить, опубликован ли общий временный буфер ответа.
 * @param server Проверяемый инициализированный сервер.
 * @return `true`, если данные и размер ответа доступны транспорту.
 */
bool krul_response_ready(const krul_server_t* server);

/**
 * @brief Получить байты опубликованного ответа.
 * @param server Проверяемый инициализированный сервер.
 * @return Заимствованные байты ответа или `NULL`, если ответ не готов.
 *
 * Указатель действителен до krul_response_release() и не должен изменяться транспортом.
 */
const uint8_t* krul_response_get_data(const krul_server_t* server);

/**
 * @brief Получить размер опубликованного ответа.
 * @param server Проверяемый инициализированный сервер.
 * @return Размер опубликованного ответа в байтах или ноль, если ответа нет.
 */
size_t krul_response_get_size(const krul_server_t* server);

/**
 * @brief Освободить текущий ответ и его слот для отложенного ответа.
 * @param server Сервер, ответ которого принят транспортом.
 *
 * Вызывайте только после копирования транспортом или принятия им во владение
 * всех байтов из krul_response_get_data(). Указатель ответа становится недействительным.
 */
void krul_response_release(krul_server_t* server);

/**
 * @brief Найти дескриптор команды по точному имени в канале связи.
 * @param server Инициализированный сервер с таблицей команд.
 * @param name Регистрозависимое имя команды с завершающим нулём.
 * @return Соответствующий долгоживущий дескриптор или `NULL`, если он не найден.
 */
const krul_command_t* krul_find_command(const krul_server_t* server,
                                        const char* name);

/** @name Автономные кодировщики конвертов
 * Эти функции кодируют данные в память вызывающего кода и при ошибке
 * возвращают ноль. Их вывод не публикуется через krul_response_get_data().
 * @{ */

/**
 * @brief Закодировать автономный ответ с ошибкой протокола.
 * @param server Инициализированный сервер, кодек которого используется.
 * @param id Идентификатор транзакции для копирования в конверт ответа.
 * @param code Стабильный код ошибки протокола.
 * @param message Диагностика с завершающим нулём; NULL выбирает общее сообщение.
 * @param output Целевой буфер, принадлежащий вызывающему коду.
 * @param capacity Размер @p output в байтах.
 * @return Число закодированных байтов или ноль при некорректных аргументах,
 * ошибке записи либо недостаточной ёмкости.
 */
size_t krul_encode_error(krul_server_t* server, uint32_t id,
                         krul_status_t code, const char* message,
                         uint8_t* output, size_t capacity);

/**
 * @brief Закодировать автономный конверт незапрошенного события.
 * @param server Инициализированный сервер, кодек которого используется.
 * @param event Имя события с завершающим нулём.
 * @param fields Скалярные поля данных; может быть NULL при нулевом @p field_count.
 * @param field_count Число элементов в @p fields.
 * @param output Целевой буфер, принадлежащий вызывающему коду.
 * @param capacity Размер @p output в байтах.
 * @return Число закодированных байтов или ноль при неподдерживаемых типах полей,
 * некорректных аргументах, ошибке записи либо недостаточной ёмкости.
 */
size_t krul_encode_event(krul_server_t* server, const char* event,
                         const krul_event_field_t* fields, size_t field_count,
                         uint8_t* output, size_t capacity);

/**
 * @brief Закодировать незапрошенное событие `log` из готового сообщения.
 * @param server Инициализированный сервер, кодек которого используется.
 * @param severity Уровень важности, сериализуемый строкой протокола.
 * @param message Сообщение с завершающим нулём; не должно быть NULL.
 * @param output Целевой буфер, принадлежащий вызывающему коду.
 * @param capacity Размер @p output в байтах.
 * @return Число закодированных байтов или ноль при ошибке.
 */
size_t krul_encode_log_event(krul_server_t* server,
                             krul_console_type_t severity,
                             const char* message, uint8_t* output,
                             size_t capacity);

/**
 * @brief Отформатировать и закодировать незапрошенное событие `log`.
 * @param server Инициализированный сервер, кодек которого используется.
 * @param severity Уровень важности, сериализуемый строкой протокола.
 * @param output Целевой буфер, принадлежащий вызывающему коду.
 * @param capacity Размер @p output в байтах.
 * @param format Формат сообщения в стиле printf; не должен быть NULL.
 * @param ... Значения, используемые @p format.
 * @return Число закодированных байтов или ноль при ошибке форматирования,
 * достижении сообщением KRUL_MAX_LOG_MESSAGE либо ошибке кодирования.
 */
size_t krul_encode_log_eventf(krul_server_t* server,
                              krul_console_type_t severity,
                              uint8_t* output, size_t capacity,
                              const char* format, ...);

/** @} */

/**
 * @brief Задать код ошибки и ограниченную диагностику в стиле printf.
 * @param error Обновляемый объект ошибки; NULL допустим как отсутствие действия.
 * @param code Сохраняемое стабильное состояние протокола.
 * @param message_format Формат в стиле printf; NULL сохраняет пустое сообщение.
 * @param ... Значения, используемые @p message_format.
 *
 * Сообщение всегда ограничено KRUL_MAX_ERROR_MESSAGE и дополняется завершающим
 * нулём нижележащей реализацией `vsnprintf`.
 */
void krul_error_set(krul_error_t* error, krul_status_t code,
                    const char* message_format, ...);

/** @name Чтение значений для ограничений
 * Чтение входного узла serde или непосредственного значения default/result.
 * @{ */
/**
 * @brief Прочитать знаковое целое, переданное пользовательскому ограничению.
 * @param value Ссылка на значение ограничения со схемой KRUL_TYPE_I32.
 * @param output Принимает знаковое значение.
 * @return `true`, если ссылка присутствует, совместима по типу и доступна для чтения.
 */
bool krul_user_validator_get_i32(const krul_value_ref_t* value, int32_t* output);

/**
 * @brief Прочитать беззнаковое целое, переданное пользовательскому ограничению.
 * @param value Ссылка на значение ограничения со схемой KRUL_TYPE_U32.
 * @param output Принимает беззнаковое значение.
 * @return `true`, если ссылка присутствует, совместима по типу и доступна для чтения.
 */
bool krul_user_validator_get_u32(const krul_value_ref_t* value, uint32_t* output);

/**
 * @brief Прочитать значение с плавающей точкой для пользовательского ограничения.
 * @param value Ссылка на значение ограничения со схемой KRUL_TYPE_F32.
 * @param output Принимает значение с плавающей точкой.
 * @return `true`, если ссылка присутствует, совместима по типу и доступна для чтения.
 */
bool krul_user_validator_get_f32(const krul_value_ref_t* value, float* output);

/**
 * @brief Прочитать логическое значение для пользовательского ограничения.
 * @param value Ссылка на значение ограничения со схемой KRUL_TYPE_BOOL.
 * @param output Принимает логическое значение.
 * @return `true`, если ссылка присутствует, совместима по типу и доступна для чтения.
 */
bool krul_user_validator_get_bool(const krul_value_ref_t* value, bool* output);

/** Read a numeric enum code for a custom validator. */
bool krul_user_validator_get_enum(const krul_value_ref_t* value,
                                  int32_t* output);

/**
 * @brief Скопировать строковое значение для пользовательского ограничения.
 * @param value Ссылка ограничения типа STRING или CONSOLE_STRING.
 * @param output Место назначения декодированной строки с завершающим нулём.
 * @param capacity Размер места назначения с учётом завершающего нуля.
 * @return `true`, если полное значение помещается и доступно для чтения.
 */
bool krul_user_validator_get_string(const krul_value_ref_t* value, char* output,
                           size_t capacity);

/**
 * @brief Прочитать число элементов/членов для пользовательского ограничения.
 * @param value Ссылка ограничения типа ARRAY или OBJECT.
 * @param output Принимает число элементов массива или членов объекта.
 * @return `true`, если ссылка присутствует и совместима по типу.
 */
bool krul_user_validator_get_count(const krul_value_ref_t* value, size_t* output);
/** @} */

/** @name Чтение аргументов обработчика
 * Чтение уже проверенных параметров, включая объявленные значения по умолчанию.
 * Представления массивов и объектов остаются заимствованными из текущего вызова диспетчера.
 * @{ */
/**
 * @brief Прочитать проверенный знаковый целый параметр команды.
 * @param args Заимствованные аргументы текущего обработчика.
 * @param name Точное имя параметра в канале связи.
 * @param value Принимает значение запроса или объявленное значение по умолчанию.
 * @return `true`, если именованное поле есть в схеме и доступно для чтения.
 */
bool krul_args_get_i32(const krul_args_t* args, const char* name,
                       int32_t* value);

/**
 * @brief Прочитать проверенный беззнаковый целый параметр команды.
 * @param args Заимствованные аргументы текущего обработчика.
 * @param name Точное имя параметра в канале связи.
 * @param value Принимает значение запроса или объявленное значение по умолчанию.
 * @return `true`, если именованное поле есть в схеме и доступно для чтения.
 */
bool krul_args_get_u32(const krul_args_t* args, const char* name,
                       uint32_t* value);

/**
 * @brief Прочитать проверенный параметр команды с плавающей точкой.
 * @param args Заимствованные аргументы текущего обработчика.
 * @param name Точное имя параметра в канале связи.
 * @param value Принимает значение запроса или объявленное значение по умолчанию.
 * @return `true`, если именованное поле есть в схеме и доступно для чтения.
 */
bool krul_args_get_f32(const krul_args_t* args, const char* name, float* value);

/**
 * @brief Прочитать проверенный логический параметр команды.
 * @param args Заимствованные аргументы текущего обработчика.
 * @param name Точное имя параметра в канале связи.
 * @param value Принимает значение запроса или объявленное значение по умолчанию.
 * @return `true`, если именованное поле есть в схеме и доступно для чтения.
 */
bool krul_args_get_bool(const krul_args_t* args, const char* name, bool* value);

/** Read a validated numeric enum parameter or its default. */
bool krul_args_get_enum(const krul_args_t* args, const char* name,
                        int32_t* value);

/**
 * @brief Скопировать проверенный строковый параметр команды.
 * @param args Заимствованные аргументы текущего обработчика.
 * @param name Точное имя параметра STRING или CONSOLE_STRING.
 * @param output Место назначения значения или default с завершающим нулём.
 * @param capacity Размер места назначения с учётом завершающего нуля.
 * @return `true`, если полное именованное значение помещается и читается.
 */
bool krul_args_get_string(const krul_args_t* args, const char* name,
                          char* output, size_t capacity);

/**
 * @brief Получить заимствованное представление проверенного параметра-массива.
 * @param args Заимствованные аргументы текущего обработчика.
 * @param name Точное имя параметра-массива в канале связи.
 * @param array Принимает учитывающее схему представление массива.
 * @return `true`, если массив присутствует или имеет объявленное значение по умолчанию.
 *
 * Возвращённое представление нельзя сохранять после возврата обработчика.
 * Отсутствующий массив со значением по умолчанию не имеет материализованных элементов.
 */
bool krul_args_get_array(const krul_args_t* args, const char* name,
                         krul_array_t* array);
/** @} */

/** @name Чтение контейнеров
 * Обход проверенных вложенных данных без раскрытия специфичных для кодека узлов.
 * @{ */
/**
 * @brief Вернуть число материализованных элементов в представлении массива.
 * @param array Заимствованное представление из krul_args_get_array().
 * @return Число элементов или ноль для NULL/отсутствующего массива с default.
 */
size_t krul_array_get_size(const krul_array_t* array);

/**
 * @brief Скопировать строковый элемент из массива.
 * @param array Заимствованное представление массива со схемой элементов STRING или ENUM.
 * @param index Индекс элемента, начиная с нуля.
 * @param output Место назначения декодированной строки с завершающим нулём.
 * @param capacity Размер места назначения с учётом завершающего нуля.
 * @return `true`, если индекс, схема и место назначения корректны.
 */
bool krul_array_get_string(const krul_array_t* array, size_t index,
                           char* output, size_t capacity);

/** Read a validated numeric enum array element. */
bool krul_array_get_enum(const krul_array_t* array, size_t index,
                         int32_t* value);

/**
 * @brief Получить учитывающее схему представление объекта из элемента массива.
 * @param array Заимствованное представление массива со схемой элементов OBJECT.
 * @param index Индекс элемента, начиная с нуля.
 * @param object Принимает заимствованное представление объекта.
 * @return `true`, если объект с данным индексом существует и аргументы корректны.
 *
 * Возвращённый объект имеет то же время жизни, что @p array и текущий вызов обработчика.
 */
bool krul_array_get_object(const krul_array_t* array, size_t index,
                           krul_object_t* object);

/**
 * @brief Прочитать знаковое целое поле вложенного объекта.
 * @param object Заимствованное учитывающее схему представление объекта.
 * @param name Точное имя дочернего поля в канале связи.
 * @param value Принимает знаковое значение.
 * @return `true`, если объявленное поле присутствует, уникально и доступно для чтения.
 */
bool krul_object_get_i32(const krul_object_t* object, const char* name,
                         int32_t* value);

/**
 * @brief Прочитать беззнаковое целое поле вложенного объекта.
 * @param object Заимствованное учитывающее схему представление объекта.
 * @param name Точное имя дочернего поля в канале связи.
 * @param value Принимает беззнаковое значение.
 * @return `true`, если объявленное поле присутствует, уникально и доступно для чтения.
 */
bool krul_object_get_u32(const krul_object_t* object, const char* name,
                         uint32_t* value);

/**
 * @brief Прочитать логическое поле вложенного объекта.
 * @param object Заимствованное учитывающее схему представление объекта.
 * @param name Точное имя дочернего поля в канале связи.
 * @param value Принимает логическое значение.
 * @return `true`, если объявленное поле присутствует, уникально и доступно для чтения.
 */
bool krul_object_get_bool(const krul_object_t* object, const char* name,
                          bool* value);

/** Read a validated numeric enum field from a nested object. */
bool krul_object_get_enum(const krul_object_t* object, const char* name,
                          int32_t* value);

/**
 * @brief Скопировать строковое поле вложенного объекта.
 * @param object Заимствованное учитывающее схему представление объекта.
 * @param name Точное имя дочернего поля STRING или ENUM в канале связи.
 * @param output Место назначения декодированной строки с завершающим нулём.
 * @param capacity Размер места назначения с учётом завершающего нуля.
 * @return `true`, если поле присутствует, уникально и полностью помещается.
 */
bool krul_object_get_string(const krul_object_t* object, const char* name,
                            char* output, size_t capacity);
/** @} */

/** @name Запись результата
 * Потоковая запись типизированных полей в ответ. Krul сверяет имена, типы,
 * ограничения, дубликаты, вложенность и полноту обязательных полей со схемой
 * результата команды.
 * @{ */
/**
 * @brief Записать знаковое целое значение результата.
 * @param result Запись результата для обработчика/функции завершения.
 * @param name Объявленное имя поля или NULL для элемента массива.
 * @param value Значение для проверки и сериализации.
 * @return `true` при успехе; `false` отмечает или сохраняет ошибку результата.
 */
bool krul_result_put_i32(krul_result_t* result, const char* name,
                         int32_t value);

/**
 * @brief Записать беззнаковое целое значение результата.
 * @param result Запись результата для обработчика/функции завершения.
 * @param name Объявленное имя поля или NULL для элемента массива.
 * @param value Значение для проверки и сериализации.
 * @return `true` при успехе; `false` отмечает или сохраняет ошибку результата.
 */
bool krul_result_put_u32(krul_result_t* result, const char* name,
                         uint32_t value);

/**
 * @brief Записать конечное значение результата с плавающей точкой.
 * @param result Запись результата для обработчика/функции завершения.
 * @param name Объявленное имя поля или NULL для элемента массива.
 * @param value Конечное значение для проверки и сериализации.
 * @return `true` при успехе; `false` для неконечных, нарушающих ограничения или
 * некорректных значений, а также при ошибке записи.
 */
bool krul_result_put_f32(krul_result_t* result, const char* name, float value);

/**
 * @brief Записать логическое значение результата.
 * @param result Запись результата для обработчика/функции завершения.
 * @param name Объявленное имя поля или NULL для элемента массива.
 * @param value Логическое значение для сериализации.
 * @return `true` при успехе; `false` отмечает или сохраняет ошибку результата.
 */
bool krul_result_put_bool(krul_result_t* result, const char* name, bool value);

/** Write a numeric enum code declared by the result descriptor. */
bool krul_result_put_enum(krul_result_t* result, const char* name,
                          int32_t value);

/**
 * @brief Записать значение результата STRING или CONSOLE_STRING.
 * @param result Запись результата для обработчика/функции завершения.
 * @param name Объявленное имя поля или NULL для элемента массива.
 * @param value Значение с завершающим нулём, сразу копируемое сериализатором.
 * @return `true`, если тип, ограничения перечисления/длины, пользовательские
 * ограничения и ёмкость вывода допускают значение.
 */
bool krul_result_put_string(krul_result_t* result, const char* name,
                            const char* value);

/**
 * @brief Начать объявленное значение результата-массива.
 * @param result Запись результата для обработчика/функции завершения.
 * @param name Объявленное имя поля или NULL для элемента массива массивов.
 * @return `true`, если схема совпадает и остаётся место для вложенности.
 * @note Каждому успешному началу должен соответствовать krul_result_end_array().
 */
bool krul_result_begin_array(krul_result_t* result, const char* name);

/**
 * @brief Завершить текущий массив результата и проверить ограничения числа элементов.
 * @param result Запись результата, текущий вложенный контейнер которой — массив.
 * @return `true`, если контейнер совпадает, его размер корректен и сериализатор
 * успешно его закрывает.
 */
bool krul_result_end_array(krul_result_t* result);

/**
 * @brief Начать объявленное значение результата-объекта.
 * @param result Запись результата для обработчика/функции завершения.
 * @param name Объявленное имя поля или NULL для объекта — элемента массива.
 * @return `true`, если схема совпадает и остаётся место для вложенности.
 * @note Каждому успешному началу должен соответствовать krul_result_end_object().
 */
bool krul_result_begin_object(krul_result_t* result, const char* name);

/**
 * @brief Завершить текущий объект результата и проверить обязательные дочерние поля.
 * @param result Запись результата, текущий вложенный контейнер которой — объект.
 * @return `true`, если объект полон и успешно закрывается.
 */
bool krul_result_end_object(krul_result_t* result);

/**
 * @brief Начать объявленное тегированное объединение (результат).
 * @param result Запись результата.
 * @param name Объявленное имя поля или NULL для элемента массива.
 * @return `true`, если поле-объединение найдено и запись открыта.
 * @note После начала нужно один раз вызвать krul_result_union_tag(), затем
 * записать поля выбранного варианта и вызвать krul_result_end_union().
 */
bool krul_result_begin_union(krul_result_t* result, const char* name);

/**
 * @brief Записать дискриминатор объединения и выбрать вариант.
 * @param result Запись результата, текущий контейнер которой — свежее union.
 * @param tag Значение тега; должно быть объявлено и иметь вариант.
 * @return `true`, если тег допустим и записан.
 */
bool krul_result_union_tag(krul_result_t* result, int32_t tag);

/**
 * @brief Завершить тегированное объединение и проверить полноту варианта.
 * @param result Запись результата, текущий контейнер которой — union.
 * @return `true`, если вариант полон и успешно закрыт.
 */
bool krul_result_end_union(krul_result_t* result);

/**
 * @brief Проверить, остаётся ли построение результата корректным на данный момент.
 * @param result Проверяемая запись результата.
 * @return `true`, если проверка Krul и нижележащая запись serde не завершились
 * ошибкой. Итоговая проверка обязательных полей выполняется после обработчика.
 */
bool krul_result_ok(const krul_result_t* result);
/** @} */

/** @name Вспомогательные инициализаторы дескрипторов
 * Используйте эти макросы внутри статических массивов krul_field_desc_t.
 * @{ */

/** Обязательное знаковое 32-битное входное значение с включёнными границами. */
#define KRUL_I32_REQUIRED(_name, _label, _min, _max)                     \
    {.name = (_name),                                                     \
     .label = (_label),                                                   \
     .type = KRUL_TYPE_I32,                                              \
     .has_constraints = true,                                            \
     .constraints.i32 = {.min = (_min), .max = (_max)}}

/** Необязательное знаковое 32-битное входное значение с границами и значением по умолчанию. */
#define KRUL_I32_DEFAULT(_name, _label, _min, _max, _default)           \
    {.name = (_name),                                                     \
     .label = (_label),                                                   \
     .type = KRUL_TYPE_I32,                                              \
     .has_default = true,                                                \
     .has_constraints = true,                                            \
     .default_value.i32 = (_default),                                    \
     .constraints.i32 = {.min = (_min), .max = (_max)}}

/** Обязательная входная строка с включёнными ограничениями длины. */
#define KRUL_STRING_REQUIRED(_name, _label, _min, _max)                 \
    {.name = (_name),                                                     \
     .label = (_label),                                                   \
     .type = KRUL_TYPE_STRING,                                           \
     .has_constraints = true,                                            \
     .constraints.string = {.min_length = (_min), .max_length = (_max)}}

/** Необязательная входная строка с ограничениями длины и значением по умолчанию. */
#define KRUL_STRING_DEFAULT(_name, _label, _min, _max, _default)        \
    {.name = (_name),                                                     \
     .label = (_label),                                                   \
     .type = KRUL_TYPE_STRING,                                           \
     .has_default = true,                                                \
     .has_constraints = true,                                            \
     .default_value.string = (_default),                                 \
     .constraints.string = {.min_length = (_min), .max_length = (_max)}}

/** Необязательное числовое перечисление со значениями из compile-time массива. */
#define KRUL_ENUM_DEFAULT(_name, _label, _values, _default)             \
    {.name = (_name),                                                     \
     .label = (_label),                                                   \
     .type = KRUL_TYPE_ENUM,                                             \
     .has_default = true,                                                \
     .has_constraints = true,                                            \
     .default_value.i32 = (_default),                                    \
     .constraints.enumeration = {                                        \
         .values = (_values),                                            \
         .count = (uint16_t)KRUL_ARRAY_SIZE(_values)}}

/** Знаковое 32-битное поле результата. */
#define KRUL_RESULT_I32(_name, _label)                                  \
    {.name = (_name), .label = (_label), .type = KRUL_TYPE_I32}
/** Беззнаковое 32-битное поле результата. */
#define KRUL_RESULT_U32(_name, _label)                                  \
    {.name = (_name), .label = (_label), .type = KRUL_TYPE_U32}
/** Поле результата с плавающей точкой. */
#define KRUL_RESULT_F32(_name, _label)                                  \
    {.name = (_name), .label = (_label), .type = KRUL_TYPE_F32}
/** Логическое поле результата. */
#define KRUL_RESULT_BOOL(_name, _label)                                 \
    {.name = (_name), .label = (_label), .type = KRUL_TYPE_BOOL}
/** Строковое поле результата с включённой максимальной длиной. */
#define KRUL_RESULT_STRING(_name, _label, _max)                         \
    {.name = (_name),                                                     \
     .label = (_label),                                                   \
     .type = KRUL_TYPE_STRING,                                           \
     .has_constraints = true,                                            \
     .constraints.string = {.min_length = 0U, .max_length = (_max)}}

/** @} */

/** @} */

#ifdef __cplusplus
}
#endif
