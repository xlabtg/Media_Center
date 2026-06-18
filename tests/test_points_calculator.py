from __future__ import annotations

import pytest
from contribution_ledger.points_calculator import (
    BASE_POINTS,
    PLATFORM_MULTIPLIERS,
    ContributionAction,
    PointsCalculationInput,
    calculate_points,
    calculate_reach_multiplier,
)
from pydantic import ValidationError


def test_points_tables_match_economics_contract() -> None:
    assert BASE_POINTS == {
        "idea": 0.5,
        "content_creation": 12.0,
        "publish": 5.0,
        "amplify": 7.0,
        "engage": 3.0,
        "admin_mod": 3.5,
        "coordinate": 2.0,
        "organize": 2.0,
        "expert_consult": 3.0,
        "referral_convert": 2.0,
        "weekly_activity": 1.0,
    }
    assert PLATFORM_MULTIPLIERS == {
        "youtube": 1.6,
        "telegram": 1.5,
        "gem_space": 1.4,
        "dzen": 1.3,
        "tenchat": 1.3,
        "vk": 1.2,
        "ok": 1.1,
        "default": 1.0,
    }


@pytest.mark.parametrize(
    ("reach", "expected_reach_mult", "expected_points"),
    [
        (49_999, 1.0, 18.0),
        (50_000, 1.3, 23.4),
        (100_000, 1.5, 27.0),
    ],
)
def test_content_creation_reach_boundaries(
    reach: int,
    expected_reach_mult: float,
    expected_points: float,
) -> None:
    result = calculate_points(
        PointsCalculationInput.model_validate(
            {
                "event_type": "content_creation",
                "platform": "telegram",
                "reach": reach,
            }
        )
    )

    assert result.base_points == 12.0
    assert result.platform_mult == 1.5
    assert result.reach_mult == expected_reach_mult
    assert result.amp_mult == 1.0
    assert result.final_points == expected_points


def test_amplify_extra_reach_uses_full_50k_steps_only() -> None:
    result = calculate_points(
        PointsCalculationInput.model_validate(
            {
                "event_type": "amplify",
                "platform": "vk",
                "extra_reach": 149_999,
            }
        )
    )

    assert result.reach_mult == 1.0
    assert result.amp_mult == 1.1
    assert result.final_points == 9.24


def test_default_platform_and_input_aliases_are_supported() -> None:
    result = calculate_points(
        PointsCalculationInput.model_validate(
            {
                "action": "idea",
            }
        )
    )

    assert result.event_type == "idea"
    assert result.platform == "default"
    assert result.final_points == 0.5


def test_public_helpers_normalize_tokens_and_reject_negative_reach() -> None:
    result = calculate_points(
        event_type=ContributionAction.CONTENT_CREATION,
        platform="YouTube",
        reach=100_000,
    )

    assert result.platform == "youtube"
    assert result.final_points == 28.8

    with pytest.raises(ValueError, match="reach"):
        calculate_reach_multiplier("content_creation", -1)


@pytest.mark.parametrize(
    "payload",
    [
        {"event_type": "unknown", "platform": "telegram"},
        {"event_type": "publish", "platform": "unknown"},
        {"event_type": "content_creation", "reach": -1},
        {"event_type": "amplify", "extra_reach": -1},
    ],
)
def test_invalid_input_is_rejected_with_validation_error(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        PointsCalculationInput.model_validate(payload)
