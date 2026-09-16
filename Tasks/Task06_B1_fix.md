Task06 audit принят.

Статус `NOT READY` подтверждаю из-за B1.

Нужно сделать только точечный fix B1. Новые функции, MCP и расширение scope НЕ добавлять.

Проблема:
`ReportService._sort_token()` умеет резолвить sort token только как metric, поэтому `date` не проходит валидацию, хотя `date` является dimension. Из-за этого `metrika_get_traffic_by_day` гарантированно падает до HTTP-запроса.

Сделай:

1. Исправь `_sort_token()` так, чтобы sort token мог быть:

   * metric;
   * ИЛИ dimension.

2. Сохрани текущую поддержку сортировки по метрикам.

3. Для `date` и других dimensions должна работать сортировка по dimension.

4. Не ослабляй валидацию произвольных неизвестных токенов.

5. Добавь regression tests:

   * `get_traffic_by_day()` успешно формирует сортировку по `date`;
   * `get_report()` с `dimensions=["date"]` и `sort=["date"]` проходит валидацию;
   * неизвестный sort token по-прежнему вызывает ValidationError;
   * существующая сортировка по metric продолжает работать.

6. После фикса обязательно повторить live e2e для:

   * `metrika_get_traffic_by_day`;
   * `metrika_get_report` с сортировкой по dimension;
   * остальные основные сценарии можно прогнать существующим acceptance script.

7. Повторить quality gates:

   * pytest;
   * ruff;
   * mypy strict;
   * compileall.

8. Обновить Task06_REPORT.md:

   * указать причину B1;
   * указать внесённый fix;
   * указать regression tests;
   * указать результаты live e2e;
   * итоговый статус поставить READY только если весь Task06 после фикса проходит.

Важно:
Это точечный bugfix. Не добавляй новые AI tools, MCP, новые API или другую функциональность.
