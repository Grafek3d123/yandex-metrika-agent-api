"""Yandex Metrika agent.

Пакет-агент для работы с API Яндекс Метрики: OAuth, хранилище токенов,
типизированные модели запросов/ответов, transport со retry.
"""

from yandex_metrika_agent.errors import (
    AgentError,
    ApiError,
    AuthError,
    ConfigError,
    RateLimitedError,
    TokenStorageError,
    TransportError,
    ValidationError,
)
from yandex_metrika_agent.models import GOAL_TYPES, Goal, GoalCondition, GoalType
from yandex_metrika_agent.planner import GoalPlan, GoalPlanner, PlanStatus

__version__ = "0.1.0"

__all__ = [
    "AgentError",
    "ApiError",
    "AuthError",
    "ConfigError",
    "RateLimitedError",
    "TokenStorageError",
    "TransportError",
    "ValidationError",
    "GOAL_TYPES",
    "Goal",
    "GoalCondition",
    "GoalType",
    "GoalPlan",
    "GoalPlanner",
    "PlanStatus",
    "__version__",
]
