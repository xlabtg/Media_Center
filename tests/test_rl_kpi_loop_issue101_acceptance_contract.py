from __future__ import annotations

from pathlib import Path
from typing import cast

from analytics_engine import AnalyticsEngineAPIState, create_analytics_engine_app
from fastapi import FastAPI
from fastapi.testclient import TestClient
from policy_manager import PolicyManagerAPIState, create_policy_manager_app

from libs.shared import ServiceTemplateConfig, encode_hs256_jwt

ROOT = Path(__file__).resolve().parents[1]
ANALYTICS_JWT_SECRET = "rl-kpi-analytics-issue-101-secret"
POLICY_JWT_SECRET = "rl-kpi-policy-issue-101-secret"


def test_issue101_rl_kpi_loop_proposes_approved_changes_and_measures_effect() -> None:
    app = _analytics_app()
    client = TestClient(app)

    _collect_pilot_kpi(
        client,
        period="2026-W25",
        batch_id="pilot-batch-2026-W25",
        content_views=8_000,
        materials_published=18,
        reading_seconds=15_000,
        comments=42,
        tasks_completed=9,
    )
    _collect_pilot_kpi(
        client,
        period="2026-W26",
        batch_id="pilot-batch-2026-W26",
        content_views=9_000,
        materials_published=19,
        reading_seconds=16_200,
        comments=45,
        tasks_completed=9,
    )

    iteration_response = client.post(
        "/analytics/rl-kpi/iterations",
        headers=_analytics_headers(subject="board-1", roles=("board",)),
        json={
            "iteration_id": "rl-kpi-2026-w26",
            "periods": ["2026-W25", "2026-W26"],
            "window_days": 14,
            "policy_revision": "policy-rl-kpi-v1",
            "started_at": "2026-06-27T09:00:00Z",
            "metadata": {"issue": "101", "source": "pilot-retro"},
        },
    )

    assert iteration_response.status_code == 201
    iteration = iteration_response.json()
    assert iteration["status"] == "awaiting_council_approval"
    assert iteration["council_control"] == {
        "required_role": "council",
        "approval_status": "pending",
        "approved_by": None,
        "approved_at": None,
        "decision_ref": None,
    }
    assert iteration["monitoring"]["effect_status"] == "pending"
    assert iteration["monitoring"]["baseline_period"] == "2026-W26"
    assert iteration["monitoring"]["evaluation_period"] is None
    proposals = {item["metric_key"]: item for item in iteration["proposals"]}
    assert {"content_views", "comments", "tasks_completed"} <= proposals.keys()
    assert proposals["content_views"]["requires_council_approval"] is True
    assert proposals["content_views"]["status"] == "proposed"
    assert proposals["content_views"]["latest_value"] == 9_000
    assert proposals["content_views"]["target_min"] == 10_000
    assert "2026-W25" in proposals["content_views"]["xai_summary"]

    forbidden_approval = client.post(
        "/analytics/rl-kpi/iterations/rl-kpi-2026-w26/approval",
        headers=_analytics_headers(subject="member-1", roles=("member_full",)),
        json={"decision": "approve", "decision_ref": "policy://rl-kpi/denied"},
    )
    approval_response = client.post(
        "/analytics/rl-kpi/iterations/rl-kpi-2026-w26/approval",
        headers=_analytics_headers(subject="council-1", roles=("council",)),
        json={
            "decision": "approve",
            "decided_at": "2026-06-27T10:00:00Z",
            "decision_ref": "policy://rl-kpi/content-boost/v2",
            "comment": "Утверждено Советом для контролируемого запуска.",
        },
    )

    assert forbidden_approval.status_code == 403
    assert approval_response.status_code == 200
    approved = approval_response.json()
    assert approved["status"] == "approved"
    assert approved["council_control"]["approval_status"] == "approved"
    assert approved["council_control"]["approved_by"] == "council-1"
    assert {item["status"] for item in approved["proposals"]} == {"approved"}

    _collect_pilot_kpi(
        client,
        period="2026-W27",
        batch_id="pilot-batch-2026-W27",
        content_views=12_500,
        materials_published=25,
        reading_seconds=27_000,
        comments=64,
        tasks_completed=12,
    )
    effect_response = client.post(
        "/analytics/rl-kpi/iterations/rl-kpi-2026-w26/effect",
        headers=_analytics_headers(subject="board-1", roles=("board",)),
        json={
            "evaluation_period": "2026-W27",
            "measured_at": "2026-07-04T09:00:00Z",
            "metadata": {"release": "rl-kpi-content-boost-v2"},
        },
    )

    assert effect_response.status_code == 200
    measured = effect_response.json()
    measured_proposals = {item["metric_key"]: item for item in measured["proposals"]}
    content_effect = measured_proposals["content_views"]["effect"]
    assert measured["status"] == "effect_measured"
    assert measured["monitoring"]["effect_status"] == "improved"
    assert measured["monitoring"]["evaluation_period"] == "2026-W27"
    assert content_effect["evaluation_value"] == 12_500
    assert content_effect["absolute_delta"] == 3_500
    assert content_effect["relative_delta"] == 0.3889
    assert content_effect["status"] == "improved"
    assert content_effect["rollback_recommended"] is False

    state = cast(AnalyticsEngineAPIState, app.state.analytics_engine_api)
    audit_events = [record.event_type for record in state.audit_log_sink.records]
    published_events = [message.envelope.type for message in state.publisher.messages]
    for event_type in (
        "analytics.rl_kpi_iteration_created",
        "analytics.rl_kpi_council_decision_recorded",
        "analytics.rl_kpi_effect_measured",
    ):
        assert event_type in audit_events
        assert event_type in published_events


def test_issue101_rl_kpi_enforces_rbac_window_and_tenant_context() -> None:
    app = _analytics_app()
    client = TestClient(app)

    forbidden_create = client.post(
        "/analytics/rl-kpi/iterations",
        headers=_analytics_headers(subject="member-1", roles=("member_full",)),
        json={
            "iteration_id": "rl-kpi-forbidden",
            "periods": ["2026-W26"],
            "window_days": 14,
            "policy_revision": "policy-rl-kpi-v1",
        },
    )
    invalid_window = client.post(
        "/analytics/rl-kpi/iterations",
        headers=_analytics_headers(subject="board-1", roles=("board",)),
        json={
            "iteration_id": "rl-kpi-invalid-window",
            "periods": ["2026-W26"],
            "window_days": 6,
            "policy_revision": "policy-rl-kpi-v1",
        },
    )
    headers = _analytics_headers(subject="council-1", roles=("council",))
    headers["X-Tenant-Id"] = "tenant-b"
    tenant_override = client.get(
        "/analytics/rl-kpi/iterations/rl-kpi-forbidden",
        headers=headers,
    )

    assert forbidden_create.status_code == 403
    assert invalid_window.status_code == 400
    assert tenant_override.status_code == 403
    assert tenant_override.json()["error"]["code"] == "tenant_isolation_violation"

    _collect_pilot_kpi(
        client,
        period="2026-W26",
        batch_id="pilot-batch-on-track-2026-W26",
        content_views=10_000,
        materials_published=20,
        reading_seconds=18_000,
        comments=50,
        tasks_completed=10,
    )
    monitoring_only = client.post(
        "/analytics/rl-kpi/iterations",
        headers=_analytics_headers(subject="board-1", roles=("board",)),
        json={
            "iteration_id": "rl-kpi-monitoring-only",
            "periods": ["2026-W26"],
            "window_days": 7,
            "policy_revision": "policy-rl-kpi-v1",
        },
    )
    no_op_approval = client.post(
        "/analytics/rl-kpi/iterations/rl-kpi-monitoring-only/approval",
        headers=_analytics_headers(subject="council-1", roles=("council",)),
        json={
            "decision": "approve",
            "decision_ref": "policy://rl-kpi/no-op",
        },
    )

    assert monitoring_only.status_code == 201
    assert monitoring_only.json()["status"] == "monitoring_only"
    assert monitoring_only.json()["proposal_count"] == 0
    assert no_op_approval.status_code == 409
    assert (
        no_op_approval.json()["error"]["code"]
        == "rl_kpi_iteration_not_awaiting_council_approval"
    )

    state = cast(AnalyticsEngineAPIState, app.state.analytics_engine_api)
    assert state.tenant_audit_sink.events[0].event_type == "tenant.isolation_violation"


def test_issue101_policy_manager_guards_rl_kpi_window_and_approval() -> None:
    app = _policy_app()
    client = TestClient(app)

    defaults = client.get(
        "/policies",
        headers=_policy_headers(subject="board-1", roles=("board",)),
    )
    escalated = client.post(
        "/policies/apply",
        headers=_policy_headers(subject="agent-1", roles=("member_full",)),
        json={
            "policy_keys": [
                "rl_kpi.window_days",
                "rl_kpi.require_council_approval",
                "rl_kpi.min_effect_lift",
            ],
            "facts": {
                "window_days": 45,
                "has_council_approval": False,
                "effect_lift": 0.01,
            },
            "applied_at": "2026-06-27T09:00:00Z",
        },
    )
    allowed = client.post(
        "/policies/apply",
        headers=_policy_headers(subject="agent-1", roles=("member_full",)),
        json={
            "policy_keys": [
                "rl_kpi.window_days",
                "rl_kpi.require_council_approval",
                "rl_kpi.min_effect_lift",
            ],
            "facts": {
                "window_days": 14,
                "has_council_approval": True,
                "effect_lift": 0.08,
            },
            "applied_at": "2026-06-27T09:05:00Z",
        },
    )

    assert defaults.status_code == 200
    policies = {item["key"]: item for item in defaults.json()["items"]}
    assert policies["rl_kpi.window_days"]["value"]["min"] == 7
    assert policies["rl_kpi.window_days"]["value"]["max"] == 30
    assert policies["rl_kpi.require_council_approval"]["value"]["expected"] is True

    assert escalated.status_code == 200
    assert escalated.json()["decision"] == "escalate"
    assert escalated.json()["reasons"] == [
        "rl_kpi_window_out_of_range",
        "council_approval_required",
        "effect_lift_below_threshold",
    ]
    assert allowed.status_code == 200
    assert allowed.json()["decision"] == "allow"
    state = cast(PolicyManagerAPIState, app.state.policy_manager_api)
    assert state.publisher.messages == ()


def test_issue101_rl_kpi_contract_is_documented() -> None:
    analytics_spec = (ROOT / "docs/modules/analytics-engine.md").read_text(
        encoding="utf-8",
    )
    analytics_readme = (ROOT / "services/analytics-engine/README.md").read_text(
        encoding="utf-8",
    )
    policy_spec = (ROOT / "docs/modules/policy-manager.md").read_text(
        encoding="utf-8",
    )
    policy_readme = (ROOT / "services/policy-manager/README.md").read_text(
        encoding="utf-8",
    )
    governance = (ROOT / "docs/GOVERNANCE.md").read_text(encoding="utf-8")

    for marker in (
        "реализовано для #61, #92 и #101",
        "POST** `/analytics/rl-kpi/iterations`",
        "POST** `/analytics/rl-kpi/iterations/{iteration_id}/approval`",
        "POST** `/analytics/rl-kpi/iterations/{iteration_id}/effect`",
        "tenant-isolation контракт #101",
    ):
        assert marker in analytics_spec

    for marker in (
        "RL-KPI loop #101",
        "7-30 дней",
        "ручное approval Совета",
        "analytics.rl_kpi_iteration_created",
        "analytics.rl_kpi_effect_measured",
    ):
        assert marker in analytics_readme

    for content in (policy_spec, policy_readme):
        for marker in (
            "rl_kpi.window_days",
            "rl_kpi.require_council_approval",
            "rl_kpi.min_effect_lift",
            "#101",
        ):
            assert marker in content

    for marker in (
        "RL-KPI production-контур #101",
        "7-30 дней",
        "решение Совета",
        "измерение эффекта",
    ):
        assert marker in governance


def _collect_pilot_kpi(
    client: TestClient,
    *,
    period: str,
    batch_id: str,
    content_views: int,
    materials_published: int,
    reading_seconds: int,
    comments: int,
    tasks_completed: int,
) -> None:
    response = client.post(
        "/analytics/pilot/telemetry/collect",
        headers=_analytics_headers(subject="board-1", roles=("board",)),
        json={
            "batch_id": batch_id,
            "source": "pilot-observability",
            "period": period,
            "collected_at": "2026-06-26T18:00:00Z",
            "kpi": {
                "active_members": 20,
                "new_members": 4,
                "materials_published": materials_published,
                "content_views": content_views,
                "reading_seconds": reading_seconds,
                "reading_sessions": 100,
                "comments": comments,
                "tasks_completed": tasks_completed,
                "initiatives_created": 1,
            },
        },
    )
    assert response.status_code == 201


def _analytics_app() -> FastAPI:
    return create_analytics_engine_app(
        ServiceTemplateConfig(
            service_name="analytics-engine",
            version="0.1.0",
            jwt_secret=ANALYTICS_JWT_SECRET,
            prometheus_enabled=True,
        )
    )


def _policy_app() -> FastAPI:
    return create_policy_manager_app(
        ServiceTemplateConfig(
            service_name="policy-manager",
            version="0.1.0",
            jwt_secret=POLICY_JWT_SECRET,
            prometheus_enabled=True,
        )
    )


def _analytics_headers(
    *,
    subject: str,
    roles: tuple[str, ...],
    tenant_id: str = "tenant-a",
    correlation_id: str = "corr-rl-kpi-issue-101",
) -> dict[str, str]:
    return _headers(
        jwt_secret=ANALYTICS_JWT_SECRET,
        tenant_id=tenant_id,
        subject=subject,
        roles=roles,
        correlation_id=correlation_id,
    )


def _policy_headers(
    *,
    subject: str,
    roles: tuple[str, ...],
    tenant_id: str = "tenant-a",
    correlation_id: str = "corr-policy-rl-kpi-issue-101",
) -> dict[str, str]:
    return _headers(
        jwt_secret=POLICY_JWT_SECRET,
        tenant_id=tenant_id,
        subject=subject,
        roles=roles,
        correlation_id=correlation_id,
    )


def _headers(
    *,
    jwt_secret: str,
    tenant_id: str,
    subject: str,
    roles: tuple[str, ...],
    correlation_id: str,
) -> dict[str, str]:
    token = encode_hs256_jwt(
        {
            "tenant_id": tenant_id,
            "sub": subject,
            "roles": list(roles),
        },
        jwt_secret,
    )
    return {
        "Authorization": f"Bearer {token}",
        "X-Tenant-Id": tenant_id,
        "X-Correlation-Id": correlation_id,
    }
