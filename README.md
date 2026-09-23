# yandex-metrika-agent-api

Tipizirovanny Python-klient (SDK) dlya API Yandex Metrika:
Management API v1 + Reports API (stat/v1). Python >= 3.11.

## Vozmozhnosti

- **OAuth** — browser flow (PKCE) i device flow; shifrovanne hranilischa tokenov (AES-GCM).
- **Transport** — retry s backoff, rate-limiting, single-flight dlya GET, podderzhka async-otchyotov.
- **CounterService** — spisok schetchikov, poluchenie po ID / domenu / nazvaniyu.
- **GoalService** — CRUD celej (url, action, number, session_timeout, pages_view).
- **ReportService** — zaprosy k Reports API: traffic, istochniki, stranicy, konversii, sravnenie periodov.
- **Typed models** — pydantic-modeli s validaciej.
- **Typed errors** — ierarhiya isklyuchenij (MetrikaError, AuthError, NotFoundError, RateLimitedError).

## Ustanovka

```bash
pip install -e .
```

Zavisimosti: httpx, pydantic, cryptography.

Dev: pytest, pytest-asyncio, respx, ruff, mypy.

## Bystryi start

```python
from yandex_metrika_agent import MetrikaClient, CounterService, GoalService, ReportService

client = MetrikaClient()
counters = CounterService(client)
goals    = GoalService(client)
reports  = ReportService(client)

for counter in counters.list():
    print(counter.id, counter.name)

goal = goals.create_url_goal(counter_id=12345678, url="/thank-you", name="Pokupka")
data = reports.traffic(counter_id=12345678, date1="2025-01-01", date2="2025-01-31")
print(data.total_visits)
```

## Konfiguraciya

| Peremennaya | Naznachenie |
|---|---|
| `METRIKA_TOKEN_DIR` | Put k khranilishchu tokenov (umolch. `~/.metrika-agent/`) |
| `METRIKA_TOKEN` | Prjamoy access-token (obhodit OAuth) |
| `METRIKA_CLIENT_ID` | OAuth client ID |
| `METRIKA_CLIENT_SECRET` | OAuth client secret |

## Arhitektura

```
Agent / CLI
    |
Service (CounterService, GoalService, ReportService)
    |
MetrikaClient (HTTP + token + retry + rate-limit)
    |
Yandex Metrika API (api.metrika.yandex.ru / stat.yandex.ru)
```

## Komandy razrabora

```bash
ruff check .          # lint
mypy src/             # typy
pytest -q             # testy
```

## Testy

337 testov: unit (transport, models, errors) i integration (respx-mocki API).
