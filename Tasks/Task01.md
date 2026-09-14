# Задача: интеграция с API Яндекс Метрики

Нужно реализовать в проекте полноценную интеграцию с API Яндекс Метрики, ориентированную на использование AI-агентом.

## 1. Источники документации

Использовать только актуальную официальную документацию Яндекс:

* API overview: https://yandex.com/dev/metrika/ru/
* Authorization: https://yandex.com/dev/metrika/ru/intro/authorization
* Quick Start: https://yandex.com/dev/metrika/ru/intro/quick-start
* Management API: https://yandex.com/dev/metrika/ru/management/
* Goals API: https://yandex.com/dev/metrika/ru/management/openapi/goal/
* Reports API: https://yandex.com/dev/metrika/ru/stat/
* Reports API OpenAPI reference: https://yandex.com/dev/metrika/ru/stat/openapi/
* Quotas: https://yandex.com/dev/metrika/ru/intro/quotas
* Logs API: https://yandex.com/dev/metrika/ru/logs/

Не придумывать endpoint'ы, параметры, типы целей или метрики. Если есть расхождение между локальной реализацией и официальной документацией, приоритет имеет актуальная официальная документация.

---

# 2. Архитектура

Не давать AI-агенту прямой доступ ко всем низкоуровневым HTTP-вызовам.

Создать отдельный сервис/клиент:

`YandexMetrikaClient`

Он должен инкапсулировать:

* OAuth;
* HTTP;
* Authorization headers;
* обработку ошибок;
* retry;
* rate limiting;
* pagination;
* преобразование API-ответов;
* типизацию;
* работу с Management API;
* работу с Reports API;
* при необходимости Logs API.

Архитектура должна быть примерно такой:

AI Agent
→ YandexMetrikaService
→ YandexMetrikaClient
→ HTTP API Yandex Metrica

---

# 3. OAuth

Поддержать OAuth-токен Яндекс Метрики.

Токен НЕ должен попадать:

* в логи;
* в сообщения AI-агента;
* в frontend;
* в exception message;
* в telemetry;
* в git;
* в API response.

Хранить токен безопасно в backend storage / encrypted credentials.

Предусмотреть два уровня доступа:

`metrika:read`

для:

* получения счётчиков;
* чтения настроек;
* получения целей;
* получения статистики;
* Reports API.

`metrika:write`

для:

* создания/изменения/удаления целей;
* изменения объектов Метрики;
* операций, требующих записи.

Если пользователю нужен только просмотр статистики, не запрашивать write access.

---

# 4. Counters API

Создать методы:

```text
listCounters()
getCounter(counterId)
```

`listCounters()` должен возвращать нормализованные объекты:

```typescript
interface MetrikaCounter {
  id: number;
  name: string;
  site: string;
  domain?: string;
  status?: string;
  ownerLogin?: string;
  permission?: string;
  timezone?: string;
}
```

AI-агент должен уметь определить, какой счётчик относится к какому сайту.

Если пользователь говорит:

"Покажи статистику example.com"

агент должен:

1. получить список доступных счётчиков;
2. найти подходящий счётчик;
3. если найден один — использовать его;
4. если найдено несколько — запросить уточнение.

Не просить пользователя вручную вводить `counterId`, если его можно определить через API.

---

# 5. Goals API

Реализовать полноценный CRUD:

```text
listGoals(counterId)
getGoal(counterId, goalId)
createGoal(counterId, goal)
updateGoal(counterId, goalId, goal)
deleteGoal(counterId, goalId)
```

Поддержать типы целей, которые реально поддерживает API Метрики.

Минимально корректно типизировать:

* action / JavaScript event;
* url;
* number / depth;
* step / composite;
* phone;
* email;
* file;
* search;
* social;
* messenger;
* payment_system;
* visit_duration;
* chat;
* другие типы, присутствующие в актуальной OpenAPI-схеме.

Не хардкодить непроверенные типы.

---

# 6. Удобная модель создания целей

AI-агенту не нужно заставлять пользователя знать внутренний JSON Яндекс.

Добавить высокоуровневые операции.

Например:

```typescript
createActionGoal(counterId, {
  name: "Отправка формы",
  event: "lead_submit"
})
```

должна внутри преобразовываться в соответствующий Yandex Metrica Goal API request.

Также поддержать:

```typescript
createUrlGoal(counterId, {
  name: "Посещение страницы благодарности",
  url: "/thank-you",
  match: "exact"
})
```

```typescript
createPhoneGoal(counterId, {
  name: "Клик по телефону",
  phone: "+79990000000"
})
```

```typescript
createEmailGoal(counterId, {
  name: "Клик по email",
  email: "sales@example.com"
})
```

```typescript
createFileGoal(counterId, {
  name: "Скачивание презентации",
  file: "presentation.pdf"
})
```

```typescript
createDurationGoal(counterId, {
  name: "Визит более 90 секунд",
  seconds: 90
})
```

```typescript
createDepthGoal(counterId, {
  name: "Просмотр 5 страниц",
  depth: 5
})
```

Перед созданием цели проверять существующие цели, чтобы AI-агент случайно не создавал дубликаты.

---

# 7. Goal planning

Добавить отдельный слой:

```text
GoalPlanner
```

Его задача — переводить человеческое описание цели в структуру Яндекс Метрики.

Например пользователь говорит:

"Мне нужна цель на отправку формы заявки"

AI должен определить:

```text
goal type = action
event = lead_submit
```

Если пользователь говорит:

"Цель при попадании на /thank-you"

→ URL goal.

Если:

"Цель при клике на WhatsApp"

→ messenger goal или соответствующий тип согласно актуальной документации.

Если:

"Составная цель: просмотр каталога → карточка товара → заявка"

→ composite/step goal, если структура поддерживается актуальным API.

AI НЕ должен молча создавать цель, если для её корректной работы требуется событие, URL, идентификатор или другое значение, которого нет.

В таком случае сначала запросить необходимые данные.

---

# 8. Reports API

Создать универсальный низкоуровневый метод:

```typescript
getReport(params)
```

Но поверх него обязательно создать AI-friendly методы.

Например:

```text
getTraffic()
getTrafficByDay()
getTrafficSources()
getTopPages()
getDevices()
getGeography()
getGoalConversions()
getGoalConversionRate()
getNewUsers()
getBounceRate()
```

Каждый метод должен самостоятельно преобразовывать человеческие параметры в правильные `dimensions`, `metrics`, `filters`.

Например:

```typescript
getTraffic({
  counterId,
  dateFrom,
  dateTo
})
```

внутри формирует запрос с необходимыми:

```text
metrics
dimensions
date1
date2
```

---

# 9. Нормализация метрик

Не заставлять AI-агента работать напрямую с:

```text
ym:s:visits
ym:s:users
ym:s:pageviews
ym:s:goal<id>reaches
```

Создать внутренний словарь:

```typescript
{
  visits: "ym:s:visits",
  users: "ym:s:users",
  pageviews: "ym:s:pageviews",
  bounceRate: "...",
  goalReaches: "...",
  goalConversionRate: "..."
}
```

Но значения брать только из актуальной документации API.

То же самое сделать для dimensions.

Например:

```typescript
trafficSource
searchEngine
country
region
city
device
browser
os
page
landingPage
date
hour
```

AI должен использовать человеческие названия, а сервис — преобразовывать их в реальные идентификаторы API.

---

# 10. Universal Report

Должна существовать возможность сформировать произвольный отчёт:

```typescript
getReport({
  counterId,
  dateFrom,
  dateTo,
  metrics: ["visits", "users"],
  dimensions: ["trafficSource"],
  filters: [...]
})
```

При этом валидировать:

* существование metrics;
* существование dimensions;
* совместимость metrics/dimensions;
* количество metrics;
* количество dimensions;
* длину filters;
* остальные ограничения API.

Если запрос некорректен — не отправлять его в API, а вернуть понятную ошибку AI-агенту.

---

# 11. Human-friendly analytics API

Добавить операции:

```text
analyzeWebsite()
comparePeriods()
getTrafficOverview()
getTrafficSources()
getTopLandingPages()
getTopPages()
getGoalPerformance()
getGoalFunnel()
```

Например:

```typescript
analyzeWebsite({
  counterId,
  dateFrom,
  dateTo
})
```

может вернуть структурированный результат:

```json
{
  "period": {
    "from": "2026-09-01",
    "to": "2026-09-14"
  },
  "visits": 12345,
  "users": 9876,
  "pageviews": 23456,
  "bounceRate": 42.3,
  "topSources": [],
  "topPages": [],
  "goals": []
}
```

Это позволит AI-агенту анализировать данные без знания внутреннего формата Яндекс Метрики.

---

# 12. Filters

Reports API использует собственный синтаксис фильтрации.

Не позволять AI свободно конструировать filter string без валидации.

Создать внутренний DSL:

```typescript
{
  field: "trafficSource",
  operator: "equals",
  value: "organic"
}
```

или:

```typescript
{
  field: "page",
  operator: "contains",
  value: "/catalog"
}
```

После этого backend преобразует DSL в настоящий `filters`.

Это значительно уменьшит количество ошибок AI.

---

# 13. Comparison API

Добавить:

```text
comparePeriods()
compareSegments()
```

Например:

```typescript
comparePeriods({
  counterId,
  periodA: {
    from: "2026-08-01",
    to: "2026-08-31"
  },
  periodB: {
    from: "2026-09-01",
    to: "2026-09-14"
  },
  metrics: ["visits", "users"]
})
```

Возвращать не только сырые значения, но и:

```json
{
  "metric": "visits",
  "periodA": 10000,
  "periodB": 12000,
  "difference": 2000,
  "differencePercent": 20
}
```

---

# 14. Quotas / rate limiting

Обязательно реализовать rate limiter.

Согласно актуальной документации Яндекс Метрики:

* существует ограничение количества параллельных запросов;
* есть суточные квоты;
* для Reports API есть отдельное ограничение на количество запросов за 5 минут;
* разные группы API расходуют квоты отдельно.

Не допускать, чтобы AI-агент в цикле генерировал сотни одинаковых запросов.

Добавить:

```text
request deduplication
caching
rate limiting
retry with backoff
```

Перед массовым получением данных использовать один агрегированный запрос, если это возможно.

Не делать:

```text
100 запросов по одной метрике
```

если тот же результат можно получить:

```text
1 запрос
metrics=a,b,c,d
```

---

# 15. Caching

Кэшировать результаты чтения там, где это безопасно.

Особенно:

```text
counters
goals
counter metadata
available dimensions
available metrics
```

Не кэшировать слишком долго статистику, если пользователь явно запросил свежие данные.

---

# 16. Logs API

Не реализовывать Logs API на первом этапе как основной механизм аналитики.

Сначала использовать Reports API.

Logs API подключать только для задач, где нужны неагрегированные данные.

AI-агент должен понимать разницу:

Reports API:
→ агрегированная аналитика.

Logs API:
→ сырые/неагрегированные данные.

Не использовать Logs API для обычного вопроса:

"Сколько было посетителей вчера?"

---

# 17. Ошибки

Создать нормализованные ошибки:

```text
MetrikaAuthError
MetrikaPermissionError
MetrikaNotFoundError
MetrikaRateLimitError
MetrikaValidationError
MetrikaApiError
```

AI должен получать человеческое объяснение.

Например:

```json
{
  "error": "MetrikaPermissionError",
  "message": "У пользователя нет прав на чтение этого счётчика.",
  "counterId": 123456
}
```

Но НЕ включать OAuth token в ошибку.

---

# 18. Dry-run для destructive operations

Для:

```text
deleteGoal()
updateGoal()
deleteCounter()
```

должен существовать механизм подтверждения.

AI не должен удалять цель только потому, что пользователь написал:

"Удали старую цель"

если не определена конкретная цель.

Сначала:

```text
listGoals()
```

найти подходящую цель и показать:

```text
Название
ID
Тип
Условия
```

Затем запросить подтверждение перед destructive action.

---

# 19. AI Tool Layer

Поверх `YandexMetrikaService` создать инструменты, которые непосредственно доступны агенту.

Минимальный набор:

```text
metrika_list_counters
metrika_get_counter

metrika_list_goals
metrika_create_goal
metrika_update_goal
metrika_delete_goal

metrika_get_report

metrika_get_traffic
metrika_get_sources
metrika_get_pages
metrika_get_goal_stats
metrika_compare_periods
```

Каждый tool должен иметь очень строгую JSON schema.

Не давать tool:

```text
request(method, url, arbitraryBody)
```

если этого можно избежать.

Чем меньше свободы у AI на уровне HTTP, тем меньше случайных ошибок.

---

# 20. Agent behavior

AI-агент должен действовать следующим образом.

### Пример 1

Пользователь:

"Покажи посещаемость сайта за последние 7 дней."

Agent:

1. определить счётчик;
2. определить даты;
3. вызвать `metrika_get_traffic`;
4. вернуть результат.

### Пример 2

Пользователь:

"Какие источники привели больше всего посетителей?"

Agent:

1. определить счётчик;
2. вызвать `metrika_get_sources`;
3. использовать visits/users;
4. отсортировать результат;
5. объяснить результат.

### Пример 3

Пользователь:

"Создай цель на отправку формы."

Agent НЕ должен сразу вызывать API.

Сначала определить, какое событие отправляется сайтом.

Если известно:

```text
lead_submit
```

создать:

```text
action goal
```

Если неизвестно — спросить:

"Какое JavaScript-событие отправляет сайт при успешной отправке формы?"

### Пример 4

Пользователь:

"Создай цель на /thank-you."

Agent может самостоятельно создать URL goal с соответствующим условием.

---

# 21. Idempotency

Создание целей должно быть максимально идемпотентным на уровне приложения.

Перед:

```text
createGoal()
```

проверять существующие цели.

Например:

```text
"Заявка отправлена"
action
lead_submit
```

уже существует.

В таком случае не создавать вторую такую же цель.

Вернуть существующую цель.

---

# 22. OpenAPI

Исследовать официальные OpenAPI reference страницы Яндекс Метрики.

Если возможно получить официальную OpenAPI schema/spec:

1. сохранить её в проекте как внешний источник схем;
2. использовать для генерации типов;
3. не копировать вручную сотни API-моделей;
4. добавить генерацию клиента только там, где это реально упрощает поддержку.

Но AI-friendly API всё равно должен находиться поверх сгенерированного клиента.

Архитектура:

```text
Official Yandex OpenAPI
        ↓
Generated low-level client
        ↓
YandexMetrikaClient
        ↓
YandexMetrikaService
        ↓
AI Tools
        ↓
KodaCode Agent
```

---

# 23. Тестирование

Создать unit/integration tests минимум для:

* OAuth headers;
* list counters;
* list goals;
* create action goal;
* create URL goal;
* duplicate goal detection;
* get traffic report;
* get sources report;
* get goal statistics;
* filter conversion;
* API errors;
* 401;
* 403;
* 429/rate limit;
* pagination;
* caching;
* retry.

Для API-интеграционных тестов не использовать реальные production credentials.

---

# 24. Документация для AI

Создать файл:

`docs/yandex-metrika-ai.md`

В нём описать:

1. какие tools доступны агенту;
2. когда какой tool использовать;
3. какие параметры обязательны;
4. какие данные нужно спросить у пользователя;
5. какие операции требуют подтверждения;
6. как выбирать счётчик;
7. как работать с целями;
8. как строить отчёты;
9. как интерпретировать ошибки;
10. какие операции нельзя выполнять напрямую.

Главный принцип:

AI должен работать не с "API Яндекс Метрики", а с понятными бизнес-операциями.

---

# 25. Первый этап реализации

Не пытаться реализовать весь API сразу.

Сначала сделать MVP:

### Auth

```text
OAuth token
```

### Counters

```text
listCounters
getCounter
```

### Goals

```text
listGoals
createGoal
updateGoal
deleteGoal
```

### Analytics

```text
getReport
getTraffic
getSources
getPages
getGoalStats
comparePeriods
```

После этого проверить реальные сценарии через API.

Только после успешной работы MVP добавлять:

```text
Segments
Imports
Logs API
CRM data
offline conversions
```

---

# 26. Главное требование

Не реализовывать API как набор случайных HTTP wrappers.

Нужен именно слой, оптимизированный под работу AI-агента.

Плохой интерфейс:

```text
yandex_request({
  method,
  path,
  query,
  body
})
```

Хороший интерфейс:

```text
metrika_get_traffic()
metrika_get_sources()
metrika_list_goals()
metrika_create_goal()
metrika_get_goal_stats()
```

AI должен оперировать понятиями:

```text
сайт
счётчик
цель
визиты
посетители
страницы
источники
конверсия
период
сегмент
```

а не:

```text
counterId
ym:s:visits
ym:s:trafficSource
filters
dimensions
metrics
```

Последние детали должны быть скрыты внутри интеграционного слоя.

Цель реализации — чтобы AI-агент мог получать от пользователя задачу на естественном языке и самостоятельно переводить её в корректные операции Яндекс Метрики, не требуя от пользователя знания API.










1. По языку и стеку: выбирай сам наиболее эффективный вариант для этой задачи. Я не привязан к TypeScript/Node.js или какому-то другому языку. Для меня важнее, чтобы решение было качественным, простым в дальнейшем развитии и хорошо подходило для AI-агента.

2. MCP не является обязательным требованием. Я сначала рассматривал MCP как удобный способ дать AI-агенту инструменты для работы с Метрикой, но не хочу добавлять его только ради самого MCP.

Сделай основной слой интеграции независимым от MCP: нормальный Yandex Metrika client/service + AI-friendly tools. Если потом окажется, что MCP действительно полезен, его можно будет добавить сверху без переделки основной логики.

3. Авторизацию хочу сделать нормально через OAuth Яндекса.

Сначала я думал сделать ввод логина/пароля прямо в нашем приложении, но сейчас считаю правильнее открывать браузер и использовать стандартную авторизацию Яндекса.

Желаемый UX:

* пользователь нажимает «Подключить Яндекс Метрику»;
* открывается браузер со страницей Яндекса;
* пользователь входит в свой аккаунт и подтверждает доступ;
* после этого приложение автоматически получает OAuth token через callback;
* пользователь ничего вручную не копирует;
* наше приложение никогда не получает пароль Яндекса.

Нужно предусмотреть, что в будущем пользователей/подключений может быть много.

Я не разбираюсь глубоко в OAuth, поэтому здесь не хочу сам выбирать техническую реализацию. Предложи наиболее правильный вариант для нашего типа приложения и сначала объясни мне простыми словами:

* какой OAuth flow использовать;
* как будет происходить возврат из браузера в приложение;
* где хранить token;
* как это расширяется до нескольких пользователей.

После этого можно реализовывать.

4. Да, сначала базовый MVP. Потом будем расширять.

В MVP включить то, что ты перечислила:

* Auth;
* Counters;
* Goals CRUD;
* GoalPlanner;
* защита от дубликатов;
* Reports: getReport, getTraffic, getSources, getPages, getGoalStats, comparePeriods;
* filter DSL;
* rate limiting;
* retry/backoff;
* cache;
* request deduplication;
* нормализованные ошибки;
* dry-run;
* tests;
* documentation.

Logs API, Segments, Imports пока не нужны. Сделаем позже.

5. Я не очень понимаю, что именно это за инструменты. Используй Vitest + mock HTTP + ESLint + Prettier, если это стандартный и разумный набор для выбранного тобой стека.

Мне важно, чтобы код нормально тестировался, проверялся и форматировался. Не нужно подбирать инструменты специально под мои знания — выбери стандартный вариант.

6. Насколько я понял, вопрос про OpenAPI/Swagger: можно ли взять официальное описание API Яндекс Метрики и автоматически сгенерировать по нему клиент и типы.

Я правильно понял, что у Яндекса нет удобного официального готового OpenAPI-файла, который можно просто скачать и использовать для полной генерации клиента?

Если так, то не надо искусственно делать полноценную OpenAPI-схему только ради Swagger.

Сделай наиболее надёжный вариант сам. Главное, чтобы в проекте было зафиксировано проверенное описание:

* endpoint'ов;
* request/response моделей;
* типов целей;
* metrics;
* dimensions;
* filters;
* ошибок и ограничений API.

При этом желательно разделить внутренние низкоуровневые модели Яндекс API и понятные для AI модели, чтобы AI работал с понятиями вроде «посетители», «источники», «цель», «конверсия», «период», а не напрямую с ym:s:*.

И ещё: если перед началом реализации есть какие-то архитектурные решения, которые существенно влияют на проект и которые мне нужно выбрать самому, объясни их простыми словами и предложи варианты. Я не архитектор, поэтому не хочу случайно принять техническое решение, последствия которого не понимаю.



