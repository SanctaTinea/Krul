# Temperature list widget

The optional result hint `special_temperature_list` displays an array of
objects with string `id` and float `temperature` fields. Temperatures are in
Celsius. Each sensor occupies one row with a selectable ID and temperature;
an empty array displays “Датчики не найдены”. Command parameters and execution
use the standard descriptor-driven form.

C descriptors select `KRUL_WIDGET_SPECIAL_TEMPERATURE_LIST` in the field's
`widget` member. The enum is appended, retaining all existing values.
Krul v4 and transport v1 remain unchanged. Clients without the widget use
the existing structured-array fallback. Hardware commands belong to consumers.
