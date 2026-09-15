Task04 audit принят. Статус `NOT READY` подтверждаю.

P1 `is_favorite` исправлен корректно. Документация и regression-тесты тоже приняты.

Но P1 по destructive safety действительно блокирующий: `metrika_delete_goal` сейчас может выполнить DELETE без подтверждения пользователя. Это нельзя оставлять на уровне инструкции для AI-агента — guard должен быть технически enforced внутри AI Tool Layer.

Следующий шаг — только исправление destructive safety, без расширения функциональности.

Требования:

1. Реализовать обязательный confirmation guard для destructive AI tools.
2. `metrika_delete_goal` без валидного подтверждения НЕ ДОЛЖЕН выполнять DELETE.
3. Простого `confirmed=true` недостаточно, если агент может сам сформировать это значение.
4. Confirmation должен быть привязан как минимум к:

   * конкретному destructive action;
   * connection_id;
   * counter_id;
   * goal_id;
   * конкретной pending operation/confirmation.
5. Подтверждение должно иметь ограниченный срок действия и не должно быть переиспользуемым для другой операции.
6. До подтверждения tool должен вернуть структурированный `confirmation_required`, а не выполнять API-запрос.
7. После корректного подтверждения DELETE может быть выполнен.
8. Неверное, просроченное или подтверждение для другого ресурса должно приводить к `confirmation_required`/validation error и НЕ должно вызывать DELETE.
9. Guard должен находиться ниже уровня конкретного handler'а, чтобы новый destructive tool в будущем нельзя было случайно реализовать без safety-проверки.
10. Не добавлять сейчас `metrika_delete_counter` — текущий запрет на его AI exposure оставить.

Обязательные regression-тесты:

* delete_goal без confirmation → DELETE не вызывается;
* первый вызов создаёт confirmation_required;
* confirmation другого goal → DELETE не вызывается;
* confirmation другого counter → DELETE не вызывается;
* confirmation другого connection → DELETE не вызывается;
* просроченный confirmation → DELETE не вызывается;
* повторное использование уже использованного confirmation → DELETE не вызывается;
* корректное подтверждение exact operation → ровно один DELETE;
* обычные read-only tools не требуют confirmation;
* обычный update_goal/create_goal не должны случайно попасть под destructive guard.

После реализации обязательно:

* pytest;
* ruff;
* mypy strict;
* compileall;
* отдельный regression/acceptance сценарий именно для destructive safety.

Обновить Task04_REPORT.md или отдельный следующий report с точным описанием механизма.

Критерий готовности: невозможно выполнить `metrika_delete_goal` через AI Tool Layer без валидного пользовательского подтверждения, даже если AI самостоятельно передаст `confirmed=true` или изменит аргументы операции.

До выполнения этих условий статус остаётся `NOT READY`.
