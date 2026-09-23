"""Typed Python client for the Yandex Metrika API.

Reusable capability layer: OAuth, encrypted token storage, HTTP transport with
retry/rate-limit, typed services for counters, goals, and reports.

Public entry points::

    from yandex_metrika_agent import (
        MetrikaClient,
        CounterService,
        GoalService,
        ReportService,
    )

Business orchestration and natural-language interaction live outside
this repository.
"""

from yandex_metrika_agent.client import MetrikaClient
from yandex_metrika_agent.counters import CounterService
from yandex_metrika_agent.errors import (
    ApiError,
    AuthError,
    ConfigError,
    MetrikaError,
    NotFoundError,
    RateLimitedError,
    ScopeError,
    TokenStorageError,
    TransportError,
    ValidationError,
)
from yandex_metrika_agent.goals import GoalService
from yandex_metrika_agent.models import GOAL_TYPES, Goal, GoalCondition, GoalType, ReportCommand
from yandex_metrika_agent.reports import ReportService
from yandex_metrika_agent.tokens import TokenRecord

__version__ = "0.3.0"

__all__ = [
    "GOAL_TYPES",
    "ApiError",
    "AuthError",
    "ConfigError",
    "CounterService",
    "Goal",
    "GoalCondition",
    "GoalService",
    "GoalType",
    "MetrikaClient",
    "MetrikaError",
    "NotFoundError",
    "RateLimitedError",
    "ReportCommand",
    "ReportService",
    "ScopeError",
    "TokenRecord",
    "TokenStorageError",
    "TransportError",
    "ValidationError",
    "__version__",
]
