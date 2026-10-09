# Практическое составление команд Krul на C {#command_authoring_guide}

Этот раздел показывает, как добавить команду в Krul: описать
входные параметры и результат, написать обработчик, зарегистрировать команду и
передать Starset подсказки для обычных и специальных виджетов.

Для общения между двумя устройствами предусмотрены команды и события. 
Этот документ про команды. Команды намного сложнее событий.
Хост (Обычно ПК, далее буду писать просто ПК) отправляет команды серверу (некое embedded устройство, буду писать дальше сервер или МК).

Команда описывается на стороне сервера, ПК ничего о ней не знает.
Он узнает о ней средствами самоописания команд. 
Об этом должно быть подробно написано в других документах.
Сейчас это нас не волнует. 
Главное, что достаточно описать команду на сервере, её аргументы, выходные параметры, добавить обработчик и всё, она уже будет полноценно работать.

## Из чего состоит команда

У команды есть три части:

1. `params` - схема объекта `params` входного запроса; (её аргументы)
2. `result` - схема объекта `result` успешного ответа; (значения, которые возвращает команда)
3. `handler` - обработчик команды.

Также у неё есть ряд параметров. Я приведу их в виде С структуры команды:
```c
struct krul_command {
    const char* name;       /**< Внутреннее имя команды, пользователь его не увидит. */
    krul_cmd_type_t type;   /**< Тип команды: Обычная, встроенная или скрытая в GUI команда. */
    /* Следующие четыре поля используются GUI-клиентом для компоновки UI. */
    *const char* tab;        /**< Вкладка в клиенте. */
    *const char* title;      /**< Имя команды, которое увидит пользователь. */
    *const char* description; /**< Описание команды. */
    *const char* group;      /**< Группа, куда должна быть помещена команда. */
    *krul_widget_t widget;   /**< Задать конкретный виджет команде. */
    *uint16_t order;         /**< Этим числом определяется положение команды в GUI. */
    /* Схемы входного объекта params и выходного объекта result. */
    const krul_field_desc_t* params; /**< Дескрипторы входных полей. (аргументы) */
    uint16_t params_count;           /**< Число входных дескрипторов. */
    const krul_field_desc_t* result; /**< Дескрипторы выходных полей. (возвращаемое) */
    uint16_t result_count;           /**< Число выходных дескрипторов. */
    /* Метаданные периодического опроса; сам KRUL таймеры не запускает. */
    bool autoupdate;             /**< Разрешён ли автоматический опрос клиентами. */
    uint32_t min_period_ms;      /**< Минимальный объявленный период опроса. */
    uint32_t max_period_ms;      /**< Максимальный объявленный период опроса. */
    uint32_t default_period_ms;  /**< Рекомендуемый период опроса. (по-умолчанию) */
    /* Рекомендуемый клиентский таймаут; 0 означает стандарт клиента. */
    // сколько клиент должен ждать ответа от прошивки, прежде чем считать, что команда не удалась
    uint32_t timeout_ms;         /**< Таймаут клиента; ноль означает стандартный. */
    krul_handler_t handler;      /**< Указатель на обработчик */
};
```

В самом Krul это должно быть прописано подробнее, здесь этот список приведён лишь для быстрого доступа для разработки.
"\*" помечены необязательные параметры.


Минимальная команда без параметров и результата:

```c
static bool reset_handler(const krul_args_t *args, krul_result_t *result, krul_error_t *error) {
    if (!board_reset_peripherals()) {
        krul_error_set(error, KRUL_ERROR_EXECUTION, "Peripheral reset failed");
        return false;
    }
    
    return true;
}

static const krul_command_t cmd_reset = {
    .name = "RESET_PERIPHERALS",
    .type = KRUL_CMD_NORMAL,
    .tab = "Сервис",
    .title = "Перезапустить периферию",
    .description = "Сбрасывает состояние периферийных интерфейсов платы.",
    .handler = reset_handler,
};
```

Если мы не задаём `params`/`result` и их счётчики Krul считает, что это команда ничего не принимает, и ничего не возвращает.

JSON-запрос и успешный ответ:

```json
{"cmd":"RESET_PERIPHERALS","id":1}
```

```json
{"id":1,"success":true,"result":{}}
```

`"result":{}` - пустой, значит команда ничего не вернула.

## Давайте добавим аргументы и возвращаемые значения

Для этого создадим массив из объектов krul_field_desc_t. 
Не буду здесь приводить описание самого krul_field_desc_t, просто смотрите примеры ниже.


### Параметры примитивных типов

В Krul есть примитивные типы:
- I32 - signed 32 bit
- U32 - unsigned 32 bit
- BOOL
- F32 - float 32 bit
- STRING

Есть два способа создать параметр в массиве:
- С помощью специальных макросов, их список описан в документации. Это быстрее
- Ручной инициализацией структуры. Это более гибкий способ.

```c
// Это аргументы на вход команде
static const krul_field_desc_t gain_params[] = {
    KRUL_I32_REQUIRED("offset", "Смещение", -100, 100),
    // "offset" - внутреннее имя поля, пользователь не увидит  
    // "Смещение" - имя поля в GUI, которое увидит пользователь
    // -100 - мин. значение
    // 100 - макс. значение
    // REQUIRED - значит, что этот параметр обязателен
    KRUL_I32_DEFAULT("gain", "Усиление", 1, 16, 1),
    // 1 в конце - значение по-умолчанию
    // DEFAULT - значит, что этот параметр НЕ обязателен. Будет заменён default значением при отсутствии.
    KRUL_STRING_DEFAULT("profile", "Профиль", 0, 31, "main"),
    // Вот ниже уже ручная инициализация структуры
    {
        .name = "reference_voltage",
        .label = "Опорное напряжение",
        .type = KRUL_TYPE_F32,
        .has_default = true,
        .has_constraints = true, // Включить ли ограничения на мин. и макс. значение
        .default_value.f32 = 3.3f,
        .constraints.f32 = {.min = 1.0f, .max = 5.0f, .step = 0.1f}, // ограничения
    },
    {
        .name = "enabled",
        .label = "Включено",
        .type = KRUL_TYPE_BOOL,
        .has_default = true,
        .default_value.boolean = true,
    },
    {
        .name = "sample_count",
        .label = "Число выборок",
        .type = KRUL_TYPE_U32,
        .has_default = true,
        .default_value.u32 = 16U,
        // А вот здесь нет .has_constraints = true. Пожно любое число в диапазоне U32
    },
};

// Это то, что она возвращает. Заметьте, что тип krul_field_desc_t один и тот же
static const krul_field_desc_t gain_result[] = {
    // Здесь используйте специальные макросы с RESULT.
    KRUL_RESULT_BOOL("applied", "Настройки применены"),
};

static bool gain_handler(const krul_args_t *args, krul_result_t *result, krul_error_t *error) {
    int32_t offset, gain;
    float reference_voltage;
    bool enabled;
    uint32_t sample_count;
    char profile[32]; /* max_length + завершающий ноль */

    // Если Krul запустил обработчик, то Krul уже гарантированно проверил все поля по описанию команды.
    // Этот if рекомендуется использовать, чтобы гарантировать, что сам разработчик в обработчике не перепутал, случайно 
    // тип параметра или имя аргумента. По крайней мере ошибка станет явной
    if (!krul_args_get_i32(args, "offset", &offset) ||
        !krul_args_get_i32(args, "gain", &gain) ||
        !krul_args_get_f32(args, "reference_voltage", &reference_voltage) ||
        !krul_args_get_bool(args, "enabled", &enabled) ||
        !krul_args_get_u32(args, "sample_count", &sample_count) ||
        !krul_args_get_string(args, "profile", profile, sizeof(profile)))
        return false;

    // сама полезная работа
    device_apply_gain(offset, gain, reference_voltage, enabled, sample_count, profile);
    
    // возвращаем значения
    if (!krul_result_put_bool(result, "applied", true)) {
        return false;
    }
    
    return true;
}
```

Полный JSON-запрос:

```json
{
  "cmd": "GAIN_SET",
  "id": 2,
  "params": {
    "offset": -2,
    "gain": 4,
    "profile": "measurement",
    "reference_voltage": 3.3,
    "enabled": true,
    "sample_count": 64
  }
}
```

Можно передать только обязательный `offset`; остальные значения возьмутся из
define:

```json
{"cmd":"GAIN_SET","id":2,"params":{"offset":-2}}
```

Ответ в случае успеха:

```json
{"id":2,"success":true,"result":{"applied":true}}
```

Заметьте, что:
`has_default = false` делает входное поле обязательным. Если
`has_default = true`, Krul вернёт default при отсутствии поля в запросе.


## Enum

В Krul v4 enum передаётся как знаковый 32-битный код. `title` - то, что будет видеть пользователь в GUI.

```c
enum { MODE_OFF = 0, MODE_AUTO = 10, MODE_TEST = 20 };

static const krul_enum_value_t mode_values[] = {
    {.value = MODE_OFF,  .title = "Выключено"},
    {.value = MODE_AUTO, .title = "Автоматически"},
    {.value = MODE_TEST, .title = "Тест"},
};

static const krul_field_desc_t mode_params[] = {
    KRUL_ENUM_DEFAULT("mode", "Режим", mode_values, MODE_AUTO),
};

static const krul_field_desc_t mode_result[] = {{
    .name = "active_mode",
    .label = "Активный режим",
    .type = KRUL_TYPE_ENUM, // Тут нет макроса удобного :(
    // Для enum это ограничение - это его список.
    // Предпологается всего вы никогда не будете использовать enum c .has_constraints = false
    .has_constraints = true, 
    .constraints.enumeration = {
        .values = mode_values,
        .count = KRUL_ARRAY_SIZE(mode_values), // макрос, чтобы считать автоматически
    },
}};

static bool mode_handler(const krul_args_t *args, krul_result_t *result, krul_error_t *error) {
    int32_t mode;
    
    if (!krul_args_get_enum(args, "mode", &mode)) {
        return false;
    }
    // mode это число из mode_values[]
        
    device_set_mode(mode);
    return krul_result_put_enum(result, "active_mode", mode);
}
```

В JSON отправляется `value`:

```json
{"cmd":"MODE_SET","id":4,"params":{"mode":20}}
```

```json
{"id":4,"success":true,"result":{"active_mode":20}}
```

При `{}` либо отсутствии `mode` обработчик получит default `10`:

```json
{"cmd":"MODE_SET","id":5,"params":{}}
```

### Больше возвращаемых значений

```c
static const krul_field_desc_t status_result[] = {
    KRUL_RESULT_I32("temperature_mC", "Температура, м°C"),
    KRUL_RESULT_U32("uptime_s", "Время работы, с"),
    KRUL_RESULT_F32("voltage", "Напряжение, В"),
    KRUL_RESULT_BOOL("ready", "Готово"),
    KRUL_RESULT_STRING("serial", "Серийный номер", 24),
};

static bool status_handler(const krul_args_t *args, krul_result_t *result, krul_error_t *error) {
    return krul_result_put_i32(result, "temperature_mC", read_temp_mc()) &&
           krul_result_put_u32(result, "uptime_s", uptime_seconds()) &&
           krul_result_put_f32(result, "voltage", read_voltage()) &&
           krul_result_put_bool(result, "ready", device_ready()) &&
           krul_result_put_string(result, "serial", board_serial()) &&
           krul_result_ok(result);
}
```

Команда не имеет параметров, поэтому JSON выглядит так:

```json
{"cmd":"STATUS_READ","id":6}
```

Пример результата:

```json
{
  "id": 6,
  "success": true,
  "result": {
    "temperature_mC": 24750,
    "uptime_s": 3600,
    "voltage": 3.294,
    "ready": true,
    "serial": "DEVICE-0001"
  }
}
```

## Результаты и ошибки



Krul запрещает необъявленные и повторные поля, несовпадающие типы, выход за
ограничения и пропуск обязательных полей результата. 
Другими словами, если ваш handler не вернёт какое-либо поле из описания команды - будет ошибка. 
Вернёт лишнее - будет ошибка. Вернёт не тот тип - будет ошибка и т. д.. Ошибка будет отправлена с сервера на ПК о том, что обработчик не смог сформировать правильный пакет. Krul проверяет данные с каждого handler-а.

А как намеренно вернуть ошибку?
Представим, что мы в обработчике, и `HAL_ADC_Start(&hadc1)` вернул ошибку:

```c
if (HAL_ADC_Start(&hadc1) != HAL_OK) {
    // тогда вот здесь мы намеренно возвращаем ошибку
    krul_error_set(error, KRUL_ERROR_EXECUTION, "ADC start failed");
    return false;
}
```

Соответствующий JSON-ответ при отказе HAL:

```json
{
  "id": 6,
  "success": false,
  "error": {"code": 7, "message": "ADC start failed"}
}
```

Ошибки формы запроса Krul создаёт до вызова handler. Другими словами, если Krul нашёл ошибку в изначальном запросе, например были пропущены какие-либо поля для аргументов, то, он даже handler вызывать не будет.

## Массив объектов (словарей)

Команда принимает список `{channel, state}` и возвращает установленные
состояния. То есть делает так:
```json
{
  "cmd": "OUTPUTS_SET",
  "id": 7,
  "params": {
    "outputs": [
      {"channel": 0, "state": 1},
      {"channel": 2, "state": 0}
    ]
  }
}
```

```json
{
  "id": 7,
  "success": true,
  "result": {
    "outputs": [
      {"channel": 0, "state": 1},
      {"channel": 2, "state": 0}
    ]
  }
}
```

Вот пример как создать такие поля (создание самой команды пропущено, так как оно аналогично примерам выше):

```c
// Значения enum для выбора канала
static const krul_enum_value_t channel_values[] = {
    {.value = 0, .title = "OUT_A"},
    {.value = 1, .title = "OUT_B"},
    {.value = 2, .title = "OUT_C"},
};

// enum канала и состояние
static const krul_field_desc_t set_item_fields[] = {
    {
        .name = "channel",
        .type = KRUL_TYPE_ENUM,
        .has_constraints = true,
        .constraints.enumeration = {
            .values = channel_values,
            .count = (uint16_t)KRUL_ARRAY_SIZE(channel_values),
        },
    },
    KRUL_I32_REQUIRED("state", "Состояние", 0, 1),
};

// Map (объект/словарь) c enum-ом канала и состоянием
/* У элемента массива не моет быть поля name. */
static const krul_field_desc_t set_item = {
    .type = KRUL_TYPE_OBJECT,
    .schema.object = {
        // set_item_fields - это описание поля с enum-ом канала и состоянием
        .fields = set_item_fields,
        .count = (uint16_t)KRUL_ARRAY_SIZE(set_item_fields),
    },
};

// Уже массив объектов/словарей
static const krul_field_desc_t set_many_params[] = {{
    .name = "outputs",
    .label = "Выходы",
    .type = KRUL_TYPE_ARRAY,
    .has_constraints = true,
    // У нас каждый элемент массива будет форматом set_item
    .schema.array = {.element = &set_item},
    // От 1 до 3 элементов массива
    .constraints.array = {.min_count = 1, .max_count = 3},
}};

// Аналогично для выходный параметров
static const krul_field_desc_t set_many_result[] = {{
    .name = "outputs",
    .label = "Установленные выходы",
    .type = KRUL_TYPE_ARRAY,
    .has_constraints = true,
    .schema.array = {.element = &set_item},
    .constraints.array = {.min_count = 1U, .max_count = 3U},
}};
```

```c
static bool set_many_handler(const krul_args_t *args,
                             krul_result_t *result,
                             krul_error_t *error)
{
    krul_array_t outputs;
    if (!krul_args_get_array(args, "outputs", &outputs) ||
        !krul_result_begin_array(result, "outputs"))
        return false;

    for (size_t i = 0; i < krul_array_get_size(&outputs); ++i) {
        krul_object_t item;
        int32_t channel, state;
        if (!krul_array_get_object(&outputs, i, &item) ||
            !krul_object_get_enum(&item, "channel", &channel) ||
            !krul_object_get_i32(&item, "state", &state))
            return false;

        if (!gpio_write(channel, state != 0)) {
            krul_error_set(error, KRUL_ERROR_EXECUTION,
                           "GPIO write failed");
            return false;
        }

        /* NULL означает текущий безымянный элемент массива. */
        if (!krul_result_begin_object(result, NULL) ||
            !krul_result_put_enum(result, "channel", channel) ||
            !krul_result_put_i32(result, "state", state) ||
            !krul_result_end_object(result))
            return false;
    }
    return krul_result_end_array(result) && krul_result_ok(result);
}
```

Ещё раз приведу здесь JSON-запрос (здесь `OUTPUTS_SET` — предполагаемое имя команды для приведённой
схемы):

```json
{
  "cmd": "OUTPUTS_SET",
  "id": 7,
  "params": {
    "outputs": [
      {"channel": 0, "state": 1},
      {"channel": 2, "state": 0}
    ]
  }
}
```
Ответ:
```json
{
  "id": 7,
  "success": true,
  "result": {
    "outputs": [
      {"channel": 0, "state": 1},
      {"channel": 2, "state": 0}
    ]
  }
}
```


## Пользовательское ограничение

Можно добавить своё собственное ограничение для любого аргумента, добавив функцию валидатор для него.

Допусти мы хотим, чтобы нам приходили в обработчик только чётные числа, тогда этого можно добиться так:

```c
// Пользовательский валидатор
static bool even_value(const krul_value_ref_t *value, krul_error_t *error, void *context) {
    int32_t number;
    (void)context; // не используем
    
    // Заметьте, что здесь значения извлекаются из аргументов так же, как и в обработчике
    // , но мы используем функции с _validator
    if (!krul_user_validator_get_i32(value, &number)) {
        return false;
    }
    if (number % 2 != 0) {
        // Если нам не нравиться что-то, то генерируем ошибку
        krul_error_set(error, KRUL_ERROR_OUT_OF_RANGE, "Value must be even");
        return false;
    }
    return true;
}

static const krul_field_desc_t even_params[] = {{
    .name = "value",
    .label = "Чётное значение",
    .type = KRUL_TYPE_I32,
    .has_constraints = true,
    .constraints.i32 = {.min = 0, .max = 100},
    // Чтобы добавить пользовательский валидатор к полю, просто доваьте указатель на функцию валидации
    .validate = even_value,
}};
```

Допустимый JSON-запрос:

```json
{"cmd":"EVEN_SET","id":8,"params":{"value":42}}
```

Значение `41` пройдёт проверку диапазона, но будет отклонено пользовательским
валидатором:

```json
{"cmd":"EVEN_SET","id":9,"params":{"value":41}}
```

```json
{
  "id": 9,
  "success": false,
  "error": {"code": 3, "message": "Value must be even"}
}
```

`.validate_context` позволяет передать валидатору постоянную таблицу или
конфигурацию. Но это не обязательно, как в примере выше.

## Размещение в GUI и автоопрос

По-умолчанию, чтобы выполнить любую команду, в том числе команду для получения данных, скажем от АЦП, он должен вручную каждый раз нажимать на кнопку выполнить в GUI.
А если нужно получать данные раз 1 секунду и строить графики? 

Тогда нам нужен автоопрос для команды. 
Заметьте, что клиент Starset может строить графики в реальном времени.
Он сам регистрирует все числовые возвращаемые значения для возможности строить по ним графики.
Но возвращаемые значение из команд без автоопроса НЕ будут зарегистрированы.
Поэтому, если команда возвращает значения, по которым могут строиться графики, всегда добавляйте автоопрос.

Делается это проце-простого:

```c
static const krul_command_t cmd_gain = {
    .name = "GAIN_SET", /* стабильное wire-имя */
    .type = KRUL_CMD_NORMAL,
    .tab = "Аналоговые сигналы",
    .title = "Настроить усилитель",
    .description = "Настройки применяются после нажатия кнопки.",
    .group = "Усилитель",
    .order = 20,
    .params = gain_params,
    .params_count = KRUL_ARRAY_SIZE(gain_params),
    .result = gain_result,
    .result_count = KRUL_ARRAY_SIZE(gain_result),
    .handler = gain_handler,
};

static const krul_command_t cmd_status = {
    .name = "STATUS_READ",
    .type = KRUL_CMD_NORMAL,
    .tab = "Мониторинг",
    .title = "Состояние устройства",
    .group = "Общее",
    .order = 10,
    .result = status_result,
    .result_count = KRUL_ARRAY_SIZE(status_result),
    // ЗДЕСЬ ВСЁ, ЧТО ВАМ НУЖНО ДЛЯ АВТООПРОСА
    .autoupdate = true,
    .min_period_ms = 100,
    .max_period_ms = 5000,
    .default_period_ms = 500,
    // Конец автоопроса
    .handler = status_handler,
};
```

Сами запросы не меняются. Ручной вызов `GAIN_SET` выглядит так:

```json
{"cmd":"GAIN_SET","id":10,"params":{"offset":5}}
```

При автоопросе Starset периодически отправляет новый `STATUS_READ` с новым
ненулевым transaction ID:

```json
{"cmd":"STATUS_READ","id":11}
```

Если команда имеет обязательные поля, тогда Starset запускает автоопрос с текущими значениями полей формы. 

В описание команды есть следующие для управления поведением команды в GUI:

- `tab` — название вкладки GUI, на которой будет размещена команда. Например, команды работы с памятью можно поместить на вкладку «Память», а команды АЦП — на вкладку «Измерения».
- `title` — отображаемое название команды. Оно предназначено для пользователя и может быть понятнее технического имени команды. Например, команда ADC_READ может иметь название «Считать значения АЦП».
- `description` — пояснение под названием команды. Здесь можно написать, что именно делает команда, какие данные возвращает или на что следует обратить внимание при её использовании.
- `group` — группа команд внутри вкладки. Она позволяет разделить команды одной вкладки на смысловые блоки, например «Чтение», «Настройка» и «Диагностика».
- `widget` — определяет, как команда будет выглядеть в GUI. Обычно используется стандартная форма с параметрами и кнопкой «Выполнить», но специальный виджет может превратить её, например, в панель GPIO, набор регуляторов PWM или другой специализированный интерфейс.
- `order` — определяет порядок отображения команд внутри вкладки или группы. Команды с меньшим значением располагаются раньше или выше. Например, команда с order = 10 будет показана перед командой с order = 20.

## Специальные виджеты Starset

`widget` - подсказка клиенту, как следует отображать команду в GUI.
Если виджета не будет, команда будет отображена по-умолчанию.
Часто виджет требует определённого формата команды или поля.
При неизвестном имени виджета или несовместимом дескрипторе 
Starset предупреждает и использует стандартное представление.

Список:

| Define в C                      | Где должен быть определён        | Значение `widget_hint` в `DESCRIBE`  | Точные требования Starset                                                                                                                                                                                                                                                                      | Отображение |
|---------------------------------|----------------------------------|--------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|---|
| `KRUL_WIDGET_SPECIAL_DAC`       | отдельное поле параметра команды | `special_dac`                        | Тип поля — `KRUL_TYPE_I32`; должны присутствовать ограничения `min` и `max`; обязательно `min < max`. Значение `default` желательно и должно попадать в диапазон                                                                                                                               | Связанные между собой горизонтальный слайдер и числовое поле |
| `KRUL_WIDGET_SPECIAL_ADC`       | отдельное поле результата        | `special_adc`                        | Тип поля — строго `KRUL_TYPE_I32`. Дополнительной структуры не требуется                                                                                                                                                                                                                       | RAW-значение и поправочный коэффициент пересчёта |
| `KRUL_WIDGET_SPECIAL_ADC_GROUP` | поле результата                  | `special_adc_group`                  | Тип верхнего поля — `KRUL_TYPE_OBJECT`; список вложенных `fields` должен быть непустым; каждое вложенное поле должно иметь непустое имя и тип `KRUL_TYPE_I32`                                                                                                                                  | Группа каналов с общим единым поправочным коэффициентом для всех вложенных RAW-значений |
| `KRUL_WIDGET_SPECIAL_GPIO`      | команда целиком                  | `special_gpio`                       | Команды с одинаковым непустым `group` образуют независимую пару: минимум одна команда чтения и ровно одна команда записи. Запись определяется по наличию параметра `state`, включая вложенные поля. Чтение принимает `pins` либо пару `target` + `name`. Команды без `group` остаются в одной общей legacy-паре | Специализированная GPIO-панель для чтения входов и управления выходами |
| `KRUL_WIDGET_SPECIAL_PWM`       | команда целиком                  | `special_pwm`                        | С этой подсказкой должна быть ровно одна команда. Она должна содержать ровно три параметра: `channel`, `duty_cycle`, `period_counter`. `channel` должен иметь тип `enum`, остальные два — `i32`. Никаких дополнительных параметров быть не должно                                              | Отдельная строка для каждого PWM-канала: скважность, период и кнопка применения |
| `KRUL_WIDGET_SLIDER`            | поле параметра или результата    | `slider`                             | TODO                                                                                                                                                                                                                                                                                           | Выводится предупреждение, затем используется стандартный редактор для типа поля |
| `KRUL_WIDGET_SPINBOX`           | поле параметра или результата    | `spinbox`                            | TODO                                                                                                                                                                                                                                                                                           | Выводится предупреждение, затем используется стандартный редактор для типа поля |

### Где задавать подсказку?

Для виджета отдельного параметра:

```C
static const krul_field_desc_t params[] = {
    {
        .name = "value",
        .label = "Значение ЦАП",
        .type = KRUL_TYPE_I32,
        .widget = KRUL_WIDGET_SPECIAL_DAC,
        .has_default = true,
        .default_value.i32 = 0,
        .has_constraints = true,
        .constraints.i32 = {
            .min = 0,
            .max = 4095,
        },
    },
};
```

Для поля результата:

```C
static const krul_field_desc_t result[] = {
    {
        .name = "raw",
        .label = "Код АЦП",
        .type = KRUL_TYPE_I32,
        .widget = KRUL_WIDGET_SPECIAL_ADC,
    },
};
```

Для команды целиком:

```C
static const krul_command_t command = {
    .name = "PWM_SET",
    .type = KRUL_CMD_NORMAL,
    .widget = KRUL_WIDGET_SPECIAL_PWM,
    .params = pwm_params,
    .params_count = KRUL_ARRAY_SIZE(pwm_params),
    .handler = pwm_set_handler,
};
```

### Что происходит при несоответствии требованиям?

Starset сначала проверяет дескриптор перед созданием специализированного интерфейса.
Если проверка не пройдена:
- приложение не должно аварийно завершиться;
- в терминал Starset выводится предупреждение с причиной;
- для special_dac, special_adc и special_adc_group используется стандартное отображение соответствующего поля;
- для special_gpio и special_pwm связанные команды отображаются обычными формами.
Например, special_pwm будет отклонён, если:
- отсутствует period_counter;
- channel объявлен как integer, а не enum;
- добавлен четвёртый параметр;

### Виджет DAC: slider и spinbox

Виджет ставится на числовое поле:

```c
static const krul_enum_value_t dac_channels[] = {
    {.value = 0, .title = "DAC1"},
    {.value = 1, .title = "DAC2"},
};

static const krul_field_desc_t dac_params[] = {
    KRUL_ENUM_DEFAULT("channel", "Канал", dac_channels, 0),
    {
        .name = "value",
        .label = "Код ЦАП",
        .type = KRUL_TYPE_I32,
        .widget = KRUL_WIDGET_SPECIAL_DAC,
        .has_default = true,
        .has_constraints = true,
        .default_value.i32 = 0,
        .constraints.i32 = {.min = 0, .max = 4095},
    },
};
```

Положение slider задаёт обычное значение `value`:

```json
{"cmd":"DAC_SET","id":12,"params":{"channel":1,"value":2048}}
```

```json
{"id":12,"success":true,"result":{}}
```

Handler читает `value` обычным `krul_args_get_i32()`.

### Виджет ADC: одно RAW-поле

```c
static const krul_field_desc_t adc_result[] = {{
    .name = "raw",
    .label = "ADC1 RAW",
    .type = KRUL_TYPE_I32,
    .widget = KRUL_WIDGET_SPECIAL_ADC,
}};

static bool adc_handler(const krul_args_t *args,
                        krul_result_t *result,
                        krul_error_t *error)
{
    (void)args;
    (void)error;
    return krul_result_put_i32(result, "raw", adc_read_raw());
}
```

```json
{"cmd":"ADC_READ","id":13}
```

```json
{"id":13,"success":true,"result":{"raw":2047}}
```

GUI показывает RAW и пересчёт `RAW * Коэфф`, где поправочный коэффициент
принадлежит GUI, а прошивка возвращает исходный код АЦП.

### Виджет ADC group: единый поправочный коэффициент каналов

```c
static const krul_field_desc_t voltage_fields[] = {
    KRUL_RESULT_I32("value_24V", "24V"),
    KRUL_RESULT_I32("value_12V", "12V"),
    KRUL_RESULT_I32("value_5V", "5V"),
};

static const krul_field_desc_t adc_group_result[] = {{
    .name = "Voltage",
    .label = "Напряжения",
    .type = KRUL_TYPE_OBJECT,
    .widget = KRUL_WIDGET_SPECIAL_ADC_GROUP,
    .schema.object = {
        .fields = voltage_fields,
        .count = (uint16_t)KRUL_ARRAY_SIZE(voltage_fields),
    },
}};

static bool adc_group_handler(const krul_args_t *args, krul_result_t *result, krul_error_t *error) {
    (void)args;
    (void)error;
    return krul_result_begin_object(result, "Voltage") &&
           krul_result_put_i32(result, "value_24V", adc_24v_raw()) &&
           krul_result_put_i32(result, "value_12V", adc_12v_raw()) &&
           krul_result_put_i32(result, "value_5V", adc_5v_raw()) &&
           krul_result_end_object(result) && krul_result_ok(result);
}
```

```json
{"cmd":"ADC_GROUP_READ","id":14}
```

```json
{
  "id": 14,
  "success": true,
  "result": {
    "Voltage": {
      "value_24V": 3010,
      "value_12V": 1498,
      "value_5V": 622
    }
  }
}
```

Непосредственные дочерние поля объекта должны быть именованными `i32`.

GUI показывает RAW каждого канала и пересчёт `RAW * Коэфф`, где единый
поправочный коэффициент принадлежит GUI и применяется одинаково ко всем каналам
группы.

### Виджет PWM: строка для каждого канала

Требуются точные имена и типы:

```c
static const krul_enum_value_t pwm_channels[] = {
    {.value = 0, .title = "PWM_A"},
    {.value = 1, .title = "PWM_B"},
};

static const krul_field_desc_t pwm_params[] = {
    KRUL_ENUM_DEFAULT("channel", "Канал", pwm_channels, 0),
    KRUL_I32_DEFAULT("duty_cycle", "Скважность, %", 0, 100, 0),
    KRUL_I32_DEFAULT("period_counter", "Период, тики", 1, 65535, 100),
};

static const krul_command_t cmd_pwm_set = {
    .name = "PWM_SET",
    .type = KRUL_CMD_NORMAL,
    .widget = KRUL_WIDGET_SPECIAL_PWM,
    .tab = "PWM",
    .title = "Управление ШИМ",
    .group = "PWM",
    .order = 10,
    .params = pwm_params,
    .params_count = KRUL_ARRAY_SIZE(pwm_params),
    .handler = pwm_set_handler,
};
```

Кнопка одной строки виджета отправляет одну обычную команду:

```json
{
  "cmd": "PWM_SET",
  "id": 15,
  "params": {"channel": 1, "duty_cycle": 35, "period_counter": 1000}
}
```

```json
{"id":15,"success":true,"result":{}}
```

Starset развернёт enum в строки duty/period/apply. Лишнее поле или другое имя
сделает виджет нерабочим.

### Виджет GPIO: пары чтения и записи

`KRUL_WIDGET_SPECIAL_GPIO` ставится на команды целиком. Starset определяет
роль команды по её параметрам: команда с полем `state` считается записью,
остальные команды — чтением. Поиск `state` рекурсивный, поэтому оно может
находиться внутри элемента массива `pins`.

Непустое поле команды `group` является идентификатором пары. В каждой такой
группе должны находиться:

- минимум одна команда чтения;
- ровно одна команда записи;
- только команды с `widget = KRUL_WIDGET_SPECIAL_GPIO`.

Это позволяет одному устройству объявить несколько независимых комплектов,
например `Local GPIO` и `Remote GPIO`. Значение `group` одновременно является
заголовком группы в GUI, поэтому оно должно быть понятным пользователю и
уникальным среди GPIO-пар устройства. Если `group` не задан, команда попадает
в общую legacy-пару. Так сохраняется совместимость со старыми устройствами,
но несколько команд записи без `group` создадут неоднозначность, и Starset
покажет их стандартными формами.

#### Полная схема варианта с массивом `pins`

Ниже приведён самодостаточный пример схемы. Числовые значения enum являются
стабильными значениями протокола, а `title` используется только в GUI.

```c
static const krul_enum_value_t all_pin_values[] = {
    {0, "BUTTON"},
    {1, "LED_GREEN"},
    {2, "LED_RED"},
};

/* Код 0 зарезервирован для одной компактной операции над всеми выходами. */
static const krul_enum_value_t writable_pin_values[] = {
    {0, "ALL"},
    {1, "LED_GREEN"},
    {2, "LED_RED"},
};

/* Starset использует направления IN и OUT для разнесения карточек. */
static const krul_enum_value_t pin_direction_values[] = {
    {0, "IN"},
    {1, "OUT"},
};

/* PIN_GET.params.pins[]: enum имени пина. */
static const krul_field_desc_t pin_get_param_item = {
    .type = KRUL_TYPE_ENUM,
    .has_constraints = true,
    .constraints.enumeration = {
        .values = all_pin_values,
        .count = KRUL_ARRAY_SIZE(all_pin_values),
    },
};

static const krul_field_desc_t pin_get_params[] = {{
    .name = "pins",
    .label = "Выводы",
    .type = KRUL_TYPE_ARRAY,
    .has_default = true, /* default для массива — пустой массив */
    .has_constraints = true,
    .schema.array = {.element = &pin_get_param_item},
    .constraints.array = {
        .min_count = 0,
        .max_count = KRUL_ARRAY_SIZE(all_pin_values),
    },
}};

/* PIN_GET.result.pins[]: объект name/type/state. */
static const krul_field_desc_t pin_get_result_fields[] = {
    {
        .name = "name",
        .type = KRUL_TYPE_ENUM,
        .has_constraints = true,
        .constraints.enumeration = {
            .values = all_pin_values,
            .count = KRUL_ARRAY_SIZE(all_pin_values),
        },
    },
    {
        .name = "type",
        .type = KRUL_TYPE_ENUM,
        .has_constraints = true,
        .constraints.enumeration = {
            .values = pin_direction_values,
            .count = KRUL_ARRAY_SIZE(pin_direction_values),
        },
    },
    KRUL_I32_REQUIRED("state", "Состояние", 0, 1),
};

static const krul_field_desc_t pin_get_result_item = {
    .type = KRUL_TYPE_OBJECT,
    .schema.object = {
        .fields = pin_get_result_fields,
        .count = KRUL_ARRAY_SIZE(pin_get_result_fields),
    },
};

static const krul_field_desc_t pin_get_result[] = {{
    .name = "pins",
    .type = KRUL_TYPE_ARRAY,
    .has_constraints = true,
    .schema.array = {.element = &pin_get_result_item},
    .constraints.array = {
        .min_count = 0,
        .max_count = KRUL_ARRAY_SIZE(all_pin_values),
    },
}};

/* PIN_SET.params.pins[]: объект name/state. */
static const krul_field_desc_t pin_set_param_fields[] = {
    {
        .name = "name",
        .type = KRUL_TYPE_ENUM,
        .has_constraints = true,
        .constraints.enumeration = {
            .values = writable_pin_values,
            .count = KRUL_ARRAY_SIZE(writable_pin_values),
        },
    },
    KRUL_I32_REQUIRED("state", "Состояние", 0, 1),
};

static const krul_field_desc_t pin_set_param_item = {
    .type = KRUL_TYPE_OBJECT,
    .schema.object = {
        .fields = pin_set_param_fields,
        .count = KRUL_ARRAY_SIZE(pin_set_param_fields),
    },
};

static const krul_field_desc_t pin_set_params[] = {{
    .name = "pins",
    .type = KRUL_TYPE_ARRAY,
    .has_constraints = true,
    .schema.array = {.element = &pin_set_param_item},
    .constraints.array = {
        .min_count = 1,
        .max_count = KRUL_ARRAY_SIZE(writable_pin_values),
    },
}};

/* Успешный результат записи перечисляет фактически изменённые выходы. */
static const krul_field_desc_t pin_set_result_fields[] = {
    {
        .name = "name",
        .type = KRUL_TYPE_ENUM,
        .has_constraints = true,
        .constraints.enumeration = {
            .values = writable_pin_values + 1, /* без служебного ALL */
            .count = KRUL_ARRAY_SIZE(writable_pin_values) - 1,
        },
    },
    KRUL_I32_REQUIRED("state", "Состояние", 0, 1),
};

static const krul_field_desc_t pin_set_result_item = {
    .type = KRUL_TYPE_OBJECT,
    .schema.object = {
        .fields = pin_set_result_fields,
        .count = KRUL_ARRAY_SIZE(pin_set_result_fields),
    },
};

static const krul_field_desc_t pin_set_result[] = {{
    .name = "pins",
    .type = KRUL_TYPE_ARRAY,
    .has_constraints = true,
    .schema.array = {.element = &pin_set_result_item},
    .constraints.array = {
        .min_count = 1,
        .max_count = KRUL_ARRAY_SIZE(writable_pin_values) - 1,
    },
}};

static const krul_command_t cmd_pin_get = {
    .name = "PIN_GET",
    .type = KRUL_CMD_NORMAL,
    .widget = KRUL_WIDGET_SPECIAL_GPIO,
    .tab = "GPIO",
    .title = "Дискретные сигналы",
    .group = "Локальные GPIO",
    .order = 10,
    .params = pin_get_params,
    .params_count = KRUL_ARRAY_SIZE(pin_get_params),
    .result = pin_get_result,
    .result_count = KRUL_ARRAY_SIZE(pin_get_result),
    .handler = pin_get_handler,
};

static const krul_command_t cmd_pin_set = {
    .name = "PIN_SET",
    .type = KRUL_CMD_NORMAL,
    .widget = KRUL_WIDGET_SPECIAL_GPIO,
    .tab = "GPIO",
    .title = "Управление выходами",
    .group = "Локальные GPIO", /* тот же идентификатор пары */
    .order = 20,
    .params = pin_set_params,
    .params_count = KRUL_ARRAY_SIZE(pin_set_params),
    .result = pin_set_result,
    .result_count = KRUL_ARRAY_SIZE(pin_set_result),
    .handler = pin_set_handler,
};
```

Контракт варианта с `pins`:

| Команда | Поле | Требование |
|---|---|---|
| `PIN_GET` | `params.pins` | Массив enum-кодов пинов. Пустой массив должен возвращать полный перечень пинов — так Starset первоначально строит панель |
| `PIN_GET` | `result.pins[]` | Каждый объект содержит `name`, направление `type` и целочисленный `state` в диапазоне `0..1` |
| `PIN_SET` | `params.pins[]` | Непустой массив объектов `name`/`state`; `name` перечисляет только выходы и специальное значение `ALL` |
| `PIN_SET` | `result.pins[]` | Перечень фактически изменённых выходов с полями `name` и `state`; при записи `ALL` желательно вернуть отдельный элемент для каждого выхода |

Запрос чтения и ответ:

```json
{"cmd":"PIN_GET","id":16,"params":{"pins":[0,1]}}
```

```json
{
  "id": 16,
  "success": true,
  "result": {
    "pins": [
      {"name": 0, "type": 0, "state": 1},
      {"name": 1, "type": 1, "state": 0}
    ]
  }
}
```

Запись одного выхода:

```json
{"cmd":"PIN_SET","id":17,"params":{"pins":[{"name":1,"state":1}]}}
```

```json
{"id":17,"success":true,"result":{"pins":[{"name":1,"state":1}]}}
```

Компактная запись всех выходов использует специальный код `ALL` и допускается
только одним элементом массива:

```json
{"cmd":"PIN_SET","id":18,"params":{"pins":[{"name":0,"state":0}]}}
```

Ограничение «`ALL` только в одиночном элементе» зависит от нескольких полей и
должно проверяться обработчиком или пользовательским валидатором. Обычная
enum-валидация Krul сама этого правила не выражает.

#### Вариант шлюза с `target` и `name`

Starset также поддерживает целевую схему, применяемую шлюзами:

- команда чтения принимает верхнеуровневые параметры `target` и `name`;
- `name = "ALL"` возвращает все пины выбранной цели;
- `name = "IN"` и `name = "OUT"` используются для автоопроса входов и
  отдельного чтения выходов;
- команда записи принимает `target`, `name` конкретного выхода и `state`;
- для кнопок «Активировать все» и «Деактивировать все» запись должна принимать
  `name = "ALL"`;
- результат чтения всё равно содержит массив `result.pins`; каждый элемент
  содержит `name`, `direction` (либо `type`) со значением `IN`/`OUT` и `state`;
- перед построением панелей Starset вызывает `TARGET_LIST` и ожидает
  `result.targets[]` с полями `name` и `available`.

Минимальная форма параметров такой пары:

```c
static const krul_field_desc_t pin_get_params[] = {
    KRUL_ENUM_DEFAULT("target", "Контроллер", target_values, 0),
    KRUL_STRING_DEFAULT("name", "Пин или ALL/IN/OUT", 1, 47, "ALL"),
};

static const krul_field_desc_t pin_set_params[] = {
    KRUL_ENUM_DEFAULT("target", "Контроллер", target_values, 0),
    KRUL_STRING_REQUIRED("name", "Выход", 1, 47),
    KRUL_I32_REQUIRED("state", "Состояние", 0, 1),
};
```

У обеих команд должны совпадать `widget = KRUL_WIDGET_SPECIAL_GPIO` и
непустой `group`, как в варианте с массивом `pins`.

## Регистрация команд

Делается она очень просто:

```c
static const krul_command_t *const commands[] = {
    &cmd_reset,
    &cmd_gain,
    &cmd_mode,
    &cmd_status,
    &cmd_adc_read,
    &cmd_dac_set,
    &cmd_pwm_set,
    &cmd_pin_get,
    &cmd_pin_set,
};

const krul_server_config_t config = {
    .commands = commands,
    .command_count = KRUL_ARRAY_SIZE(commands),
    .device_name = "MY_BOARD",
    .firmware_version = "1.0.0",
    .protocol_version = 4,
    .codec = serde_json(&json),
    .response_buffer = response_buffer,
    .response_capacity = sizeof(response_buffer),
};

if (!krul_server_init(&server, &config))
    fatal_error(); /* Некорректная схема, дубли имён или тегов. */
```

После регистрации наличие команды проверяется встроенными JSON-запросами:

```json
{"cmd":"CMD_LIST","id":18}
```

```json
{"cmd":"DESCRIBE","id":19,"params":{"name":"GAIN_SET"}}
```

## Чек-лист новой команды

1. `krul_server_init()` принимает таблицу.
2. `CMD_LIST` содержит новое имя.
3. `DESCRIBE` показывает типы, ограничения, defaults и `widget_hint`.
4. Запрос без обязательного поля отклоняется до handler.
5. Проверены границы и выход за диапазон.
6. Handler выдаёт результат строго по схеме.
7. Ошибка оборудования имеет понятные `code` и `message`.
8. Проверены обычная форма, special widget и fallback Starset.
9. Payload остаётся меньше 10 КиБ.
10. Автопрос и аппаратные ожидания всегда ограничены по времени.

```json
{"cmd":"GAIN_SET","id":1,"params":{"offset":-2,"gain":4}}
```

```json
{"id":1,"success":true,"result":{"applied":true}}
```

Deferred-команды описаны в @ref request_lifecycle, полная инициализация codec и
transport - в @ref getting_started.
