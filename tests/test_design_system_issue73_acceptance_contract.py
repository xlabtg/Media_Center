from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from web_cabinet import create_web_cabinet_app

from libs.shared import ServiceTemplateConfig, encode_hs256_jwt

ROOT = Path(__file__).resolve().parents[1]
JWT_SECRET = "design-system-issue-73-secret"


def test_issue_73_design_system_exposes_tokens_components_and_ui_kit() -> None:
    client = TestClient(_app())

    contract = client.get(
        "/design-system/tokens",
        headers=_headers(subject="designer-1", roles=("board",)),
    )
    ui_kit = client.get(
        "/design-system/ui-kit",
        headers=_headers(subject="designer-1", roles=("board",)),
    )

    assert contract.status_code == 200
    body = contract.json()
    assert body["version"] == "0.1.0"
    assert body["source"] == "docs/UX_RESEARCH.md#5"
    assert body["accessibility_baseline"] == [
        "keyboard_focus_visible",
        "contrast_minimum_4_5_1",
        "status_not_color_only",
        "stable_layout_dimensions",
    ]

    tokens = {item["name"]: item for item in body["tokens"]}
    assert tokens["color.bg.canvas"]["value"] == "#F6F7F9"
    assert tokens["color.brand.primary"]["css_variable"] == "--mc-color-brand-primary"
    assert tokens["font.family.base"]["category"] == "typography"
    assert tokens["space.4"]["value"] == "16px"
    assert tokens["radius.card"]["value"] == "8px"
    assert tokens["shadow.focus"]["value"] == "0 0 0 3px rgba(107, 70, 193, 0.35)"

    components = {item["name"]: item for item in body["components"]}
    for name in (
        "AppShell",
        "MetricTile",
        "DataTable",
        "StatusBadge",
        "HITLQueueItem",
        "VetoActionBar",
        "AuditHash",
        "Timeline",
        "ConsentControl",
        "EmptyState",
        "InlineAlert",
    ):
        assert name in components

    assert "/cabinet" in components["MetricTile"]["reused_in"]
    assert "/analytics/dashboard" in components["MetricTile"]["reused_in"]
    assert "aria_label_or_role" in components["DataTable"]["accessibility"]
    assert "not_color_only" in components["StatusBadge"]["accessibility"]

    assert ui_kit.status_code == 200
    assert ui_kit.headers["content-type"].startswith("text/html")
    assert 'data-design-system="nmc-ui"' in ui_kit.text
    assert "--mc-color-bg-canvas: #F6F7F9;" in ui_kit.text
    assert ":focus-visible" in ui_kit.text
    assert 'aria-label="Компоненты UI-кита"' in ui_kit.text
    assert 'data-component="MetricTile"' in ui_kit.text
    assert 'data-component="StatusBadge"' in ui_kit.text
    assert 'data-component="DataTable"' in ui_kit.text
    assert 'data-component="InlineAlert"' in ui_kit.text


def test_issue_73_existing_web_cabinet_pages_reuse_design_system_components() -> None:
    client = TestClient(_app())

    pages: tuple[tuple[str, dict[str, str], tuple[str, ...]], ...] = (
        ("/cabinet", {"period": "2026-06"}, ("member_full",)),
        ("/council/panel", {}, ("council",)),
        ("/analytics/dashboard", {"period": "2026-W25"}, ("board",)),
        ("/onboarding", {}, ("member_assoc",)),
        ("/voice-assistant", {}, ("member_full",)),
    )

    for path, params, roles in pages:
        response = client.get(
            path,
            headers=_headers(subject="member-a", roles=roles),
            params=params,
        )

        assert response.status_code == 200
        assert 'data-design-system="nmc-ui"' in response.text
        assert "--mc-color-focus: #6B46C1;" in response.text
        assert ":focus-visible" in response.text
        assert 'data-component="AppShell"' in response.text
        assert 'data-component="MetricTile"' in response.text
        assert 'data-component="Panel"' in response.text


def test_issue_73_design_system_documentation_is_linked() -> None:
    design_system = (ROOT / "docs/modules/design-system.md").read_text(encoding="utf-8")
    web_cabinet = (ROOT / "docs/modules/web-cabinet.md").read_text(encoding="utf-8")
    readme = (ROOT / "services/web-cabinet/README.md").read_text(encoding="utf-8")

    for marker in (
        "реализовано для #73",
        "GET** `/design-system/tokens`",
        "GET** `/design-system/ui-kit`",
        "color.bg.canvas",
        "MetricTile",
        "Доступность",
        "keyboard_focus_visible",
    ):
        assert marker in design_system

    for marker in (
        "дизайн-система #73",
        "GET** `/design-system/tokens`",
        "GET** `/design-system/ui-kit`",
        "переиспользуемые UI-компоненты",
    ):
        assert marker in web_cabinet

    for marker in (
        "Дизайн-система и UI-кит",
        "GET /design-system/tokens",
        "GET /design-system/ui-kit",
        "web_cabinet.design_system",
    ):
        assert marker in readme


def _app() -> FastAPI:
    return create_web_cabinet_app(
        ServiceTemplateConfig(
            service_name="web-cabinet",
            version="0.1.0",
            jwt_secret=JWT_SECRET,
            prometheus_enabled=True,
        ),
    )


def _headers(
    *,
    tenant_id: str = "tenant-a",
    subject: str,
    roles: tuple[str, ...],
    correlation_id: str = "corr-design-system-issue-73",
) -> dict[str, str]:
    token = encode_hs256_jwt(
        {
            "tenant_id": tenant_id,
            "sub": subject,
            "roles": list(roles),
        },
        JWT_SECRET,
    )
    return {
        "Authorization": f"Bearer {token}",
        "X-Tenant-Id": tenant_id,
        "X-Correlation-Id": correlation_id,
    }
