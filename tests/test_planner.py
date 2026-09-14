"""Тесты GoalPlanner: ready / needs_input / unknown, без угадывания."""

from __future__ import annotations

import pytest

from yandex_metrika_agent.errors import ValidationError
from yandex_metrika_agent.planner import GoalPlan, GoalPlanner, PlanStatus


def test_url_goal_with_url_in_text_is_ready() -> None:
    plan = GoalPlanner().plan("цель при попадании на /thank-you")
    assert plan.status is PlanStatus.READY
    assert plan.goal_type == "url"
    assert plan.params["url"] == "/thank-you"


def test_action_goal_without_event_needs_input() -> None:
    plan = GoalPlanner().plan("цель на отправку формы")
    assert plan.status is PlanStatus.NEEDS_INPUT
    assert plan.missing == ["event"]
    assert plan.question  # вопрос пользователю подготовлен


def test_action_goal_with_event_override_is_ready() -> None:
    plan = GoalPlanner().plan("цель на отправку формы", event="submitForm")
    assert plan.status is PlanStatus.READY
    goal = GoalPlanner().build_goal(plan)
    assert goal.type == "action"
    assert goal.name


def test_phone_goal_extracts_number_from_text() -> None:
    plan = GoalPlanner().plan("цель на клик по телефону +7 999 123-45-67")
    assert plan.status is PlanStatus.READY
    assert "7 999 123-45-67" in plan.params["phone"].replace("+", "")


def test_phone_goal_without_number_needs_input() -> None:
    plan = GoalPlanner().plan("цель на звонок")
    assert plan.status is PlanStatus.NEEDS_INPUT


def test_visit_duration_from_minutes() -> None:
    plan = GoalPlanner().plan("цель: 3 минуты на сайте")
    assert plan.status is PlanStatus.READY
    assert plan.params["seconds"] == 180


def test_depth_goal_default_without_number() -> None:
    plan = GoalPlanner().plan("цель по глубине просмотра")
    assert plan.status is PlanStatus.READY
    assert plan.params["depth"] >= 2


def test_payment_system_goal_has_no_conditions() -> None:
    plan = GoalPlanner().plan("цель возврат из платёжной системы")
    assert plan.status is PlanStatus.READY
    goal = GoalPlanner().build_goal(plan)
    assert goal.type == "payment_system"


def test_chat_goal_from_keywords() -> None:
    plan = GoalPlanner().plan("цель: переход в онлайн-чат")
    assert plan.status is PlanStatus.NEEDS_INPUT  # platform не угадываем
    plan2 = GoalPlanner().plan("цель: переход в онлайн-чат", platform="jivo")
    assert plan2.status is PlanStatus.READY
    goal = GoalPlanner().build_goal(plan2)
    assert goal.type == "chat"


def test_messenger_goal() -> None:
    plan = GoalPlanner().plan("цель переход в whatsapp", platform="whatsapp")
    assert plan.status is PlanStatus.READY
    assert GoalPlanner().build_goal(plan).type == "messenger"


def test_unknown_description_returns_unknown_status() -> None:
    plan = GoalPlanner().plan("сделай что-нибудь хорошее")
    assert plan.status is PlanStatus.UNKNOWN
    assert plan.question


def test_empty_description_rejected() -> None:
    with pytest.raises(ValidationError):
        GoalPlanner().plan("   ")


def test_explicit_goal_type_overrides_keywords() -> None:
    plan = GoalPlanner().plan("цель на форму", goal_type="url", url="/form")
    assert plan.goal_type == "url"
    assert plan.status is PlanStatus.READY


def test_invalid_goal_type_rejected() -> None:
    with pytest.raises(ValidationError):
        GoalPlanner().plan("цель", goal_type="nope")


def test_build_goal_rejects_not_ready_plan() -> None:
    plan = GoalPlan(status=PlanStatus.NEEDS_INPUT, missing=["event"])
    with pytest.raises(ValidationError):
        GoalPlanner().build_goal(plan)


def test_name_derived_from_description() -> None:
    plan = GoalPlanner().plan("цель при попадании на /thank-you")
    assert plan.name
    assert "цель" not in plan.name.lower() or plan.name == "при попадании на /thank-you"


def test_plan_to_dict_roundtrip() -> None:
    plan = GoalPlanner().plan("цель при попадании на /thank-you")
    data = plan.to_dict()
    assert data["status"] == "ready"
    assert data["goal_type"] == "url"
