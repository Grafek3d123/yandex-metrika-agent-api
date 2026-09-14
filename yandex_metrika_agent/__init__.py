"""Yandex Metrika agent.

Пакет-агент для работы с API Яндекс Метрики: OAuth, зашифрованное хранилище
токенов, сервисы (счётчики, цели, отчёты), AI Tool Layer со строгими
JSON-схемами.

Публичный вход для интегратора::

    from yandex_metrika_agent import (
        MetrikaClient,
        CounterService,
        GoalService,
        ReportService,
        GoalPlanner,
        MetrikaTools,
    )
"""

from yandex_metrika_agent.ai_tools import MetrikaTools
from yandex_metrika_agent.client import MetrikaClient
from yandex_metrika_agent.counters import CounterService
from yandex_metrika_agent.errors import (
    AgentError,
    ApiError,
    AuthError,
    ConfigError,
    NotFoundError,
    RateLimitedError,
    ScopeError,
    TokenStorageError,
    TransportError,
    ValidationError,
)
from yandex_metrika_agent.goals import GoalService
from yandex_metrika_agent.models import GOAL_TYPES, Goal, GoalCondition, GoalType
from yandex_metrika_agent.planner import GoalPlan, GoalPlanner, PlanStatus
from yandex_metrika_agent.reports import ReportService
from yandex_metrika_agent.tokens import TokenRecord

__version__ = "0.2.0"

__all__ = [
    "GOAL_TYPES",
    "AgentError",
    "ApiError",
    "AuthError",
    "ConfigError",
    "CounterService",
    "Goal",
    "GoalCondition",
    "GoalPlan",
    "GoalPlanner",
    "GoalService",
    "GoalType",
    "MetrikaClient",
    "MetrikaTools",
    "NotFoundError",
    "PlanStatus",
    "RateLimitedError",
    "ReportService",
    "ScopeError",
    "TokenRecord",
    "TokenStorageError",
    "TransportError",
    "ValidationError",
    "__version__",
]
