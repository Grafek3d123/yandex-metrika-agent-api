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
    "__version__",
]
