from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "infra/local/fixtures/pilot-tenant.json"
DEV_FIXTURE_PATH = ROOT / "infra/local/fixtures/dev-fixtures.json"
PILOT_DOC_PATH = ROOT / "docs/PILOT_TENANT_ONBOARDING.md"
STAGE_DOC_PATH = ROOT / "docs/STAGE_7_ACCEPTANCE.md"


def test_issue91_pilot_tenant_fixture_registers_participants_and_roles() -> None:
    fixture = _read_pilot_fixture()

    tenant = _mapping(fixture["tenant"])
    participants = _sequence(fixture["participants"])
    roles = Counter(_mapping(participant)["role"] for participant in participants)
    onboarding = _mapping(fixture["onboarding"])
    thresholds = _mapping(fixture["council_thresholds"])

    assert tenant["tenant_id"] == "00000000-0000-4000-8000-000000000001"
    assert tenant["slug"] == "nmc-pilot"
    assert tenant["status"] == "pilot_ready"
    assert tenant["environment"] == "pilot"
    assert tenant["data_policy"] == "synthetic_no_pdn"

    assert 15 <= len(participants) <= 25
    assert len(
        {str(_mapping(participant)["participant_id"]) for participant in participants}
    ) == len(participants)
    assert len(
        {str(_mapping(participant)["handle"]) for participant in participants}
    ) == len(participants)
    assert roles == {
        "council": 6,
        "presidium": 2,
        "board": 3,
        "member_full": 5,
        "member_assoc": 4,
    }

    required_steps = {
        step["step_id"]
        for step in _sequence(onboarding["steps"])
        if _mapping(step)["required"]
    }
    assert required_steps == {
        "profile",
        "consents",
        "channels",
        "mentor_intro",
        "council_review",
    }
    assert onboarding["target_window_hours"] == 24
    assert onboarding["window_bounds_hours"] == {"min": 12, "max": 36}

    active_onboarding_statuses = {"scheduled", "in_progress", "ready_for_review"}
    for participant in participants:
        participant_map = _mapping(participant)
        assert participant_map["tenant_id"] == tenant["tenant_id"]
        assert participant_map["status"] == "registered"
        assert participant_map["onboarding_status"] in active_onboarding_statuses
        assert participant_map["mentor_handle"].startswith("council-")
        assert set(_mapping(participant_map["onboarding_checklist"])) == required_steps
        assert "@" not in json.dumps(participant_map, ensure_ascii=False)

    assert thresholds["strategic_quorum"] == {"numerator": 2, "denominator": 3}
    assert thresholds["veto_window_hours"] == 8
    assert thresholds["veto_window_bounds_hours"] == {"min": 4, "max": 12}
    assert thresholds["payout_confirmation"] == {
        "requires_2fa": True,
        "approvals_required": 2,
        "sensitive_operations": ["payout.execute", "policy.threshold.update"],
    }
    assert thresholds["policy_update"]["approvals_required"] == 3
    assert thresholds["policy_update"]["audit_target"] == "blockchain-auditor"


def test_issue91_local_seed_and_dev_fixture_match_pilot_packet() -> None:
    fixture = _read_pilot_fixture()
    dev_fixture = _read_json(DEV_FIXTURE_PATH)
    seed = (ROOT / "infra/local/postgres/seeds/001_dev_seed.sql").read_text(
        encoding="utf-8"
    )

    participants = _sequence(fixture["participants"])
    dev_participants = _sequence(dev_fixture["participants"])
    fixture_roles = Counter(
        _mapping(participant)["role"] for participant in participants
    )
    dev_roles = Counter(
        _mapping(participant)["role"] for participant in dev_participants
    )

    assert dev_fixture["tenant_status"] == "pilot_ready"
    assert dev_fixture["pilot_fixture"] == "infra/local/fixtures/pilot-tenant.json"
    assert len(dev_participants) == len(participants) == 20
    assert dev_roles == fixture_roles
    assert "'pilot_ready'" in seed
    assert "'member-assoc-04'" in seed
    assert "00000000-0000-4000-8000-000000000120" in seed


def test_issue91_pilot_tenant_docs_capture_onboarding_runbook_and_gates() -> None:
    pilot_doc = PILOT_DOC_PATH.read_text(encoding="utf-8")
    stage_doc = STAGE_DOC_PATH.read_text(encoding="utf-8")
    local_readme = (ROOT / "infra/local/README.md").read_text(encoding="utf-8")
    governance = (ROOT / "docs/GOVERNANCE.md").read_text(encoding="utf-8")

    for marker in (
        "# Пилотный tenant и онбординг",
        "issue #91",
        "`nmc-pilot`",
        "`infra/local/fixtures/pilot-tenant.json`",
        "20 синтетических участников",
        "15-25 участников",
        "`council`",
        "`presidium`",
        "`board`",
        "`member_full`",
        "`member_assoc`",
        "2/3",
        "8 часов",
        "12-36 часов",
        "без ПДн",
        "rollback",
        "tests/test_pilot_tenant_issue91_acceptance_contract.py",
    ):
        assert marker in pilot_doc

    for marker in (
        "Acceptance snapshot этапа 7",
        "issue #91",
        "`nmc-pilot`",
        "15-25 участников",
        "Роли и пороги Совета заданы",
        "KPI",
        "ручной go/no-go",
    ):
        assert marker in stage_doc

    assert "pilot-tenant.json" in local_readme
    assert "Пилотный tenant" in governance
    assert "issue #91" in governance


def _read_pilot_fixture() -> dict[str, Any]:
    return _read_json(FIXTURE_PATH)


def _read_json(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    return raw


def _mapping(value: Any) -> dict[str, Any]:
    assert isinstance(value, dict)
    return value


def _sequence(value: Any) -> list[Any]:
    assert isinstance(value, list)
    return value
