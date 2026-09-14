"""Точка входа ``python -m yandex_metrika_agent``.

Минимальный CLI планировщика целей: по человеческому описанию печатает план
цели в JSON. Полезен для быстрой проверки намерения без сети и токена.

    python -m yandex_metrika_agent plan "цель при попадании на /thank-you"
    python -m yandex_metrika_agent plan "цель на отправку формы" --event submitForm
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from yandex_metrika_agent.errors import AgentError
from yandex_metrika_agent.planner import GoalPlanner


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="yandex_metrika_agent",
        description="Агент API Яндекс Метрики: планирование целей.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    plan_cmd = sub.add_parser("plan", help="Построить план цели по описанию.")
    plan_cmd.add_argument("description", help="Человеческое описание цели.")
    plan_cmd.add_argument("--name", help="Название цели.", default=None)
    plan_cmd.add_argument("--type", dest="goal_type", help="Явный тип цели.", default=None)
    plan_cmd.add_argument("--event", help="JS-событие для action-цели.", default=None)
    plan_cmd.add_argument("--url", help="URL для url-цели.", default=None)
    plan_cmd.add_argument("--phone", help="Телефон для phone-цели.", default=None)
    plan_cmd.add_argument("--email", help="Email для email-цели.", default=None)
    return parser


def _run_plan(args: argparse.Namespace) -> int:
    planner = GoalPlanner()
    overrides = {
        key: value
        for key, value in {
            "event": args.event,
            "url": args.url,
            "phone": args.phone,
            "email": args.email,
        }.items()
        if value
    }
    plan = planner.plan(
        args.description,
        name=args.name,
        goal_type=args.goal_type,
        **overrides,
    )
    print(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2))
    return 0 if plan.is_ready else 2


def main(argv: Sequence[str] | None = None) -> int:
    """Запустить CLI. Возвращает код выхода."""

    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            return _run_plan(args)
    except AgentError as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1
    parser.error(f"Неизвестная команда: {args.command}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
