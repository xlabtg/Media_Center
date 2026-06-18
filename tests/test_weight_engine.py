from __future__ import annotations

import pytest
from contribution_ledger.weight_engine import (
    COUNCIL_CAP_KV,
    WeightCalculationInput,
    calculate_payout_shares,
    calculate_weights,
)
from pydantic import ValidationError


def test_weight_engine_applies_council_cap_and_normalizes_payout_shares() -> None:
    result = calculate_weights(
        WeightCalculationInput.model_validate(
            {
                "avg_points_council": 100,
                "members": [
                    {"member_id": "member-a", "points": 5},
                    {"member_id": "member-b", "points": 10},
                    {"member_id": "member-c", "points": 50},
                ],
            }
        )
    )

    assert COUNCIL_CAP_KV == 0.10
    assert result.total_kv_capped == 0.25
    assert [member.kv_raw for member in result.members] == [0.05, 0.1, 0.5]
    assert [member.kv_capped for member in result.members] == [0.05, 0.1, 0.1]
    assert [member.payout_share for member in result.members] == [0.2, 0.4, 0.4]
    assert result.total_payout_share == 1.0


def test_weight_engine_keeps_rounded_payout_share_sum_equal_to_one() -> None:
    result = calculate_weights(
        avg_points_council=1,
        members=[
            {"member_id": "member-a", "points": 1},
            {"member_id": "member-b", "points": 1},
            {"member_id": "member-c", "points": 1},
        ],
    )

    assert [member.kv_capped for member in result.members] == [0.1, 0.1, 0.1]
    assert [member.payout_share for member in result.members] == [
        0.3333333333,
        0.3333333333,
        0.3333333334,
    ]
    assert result.total_payout_share == 1.0


@pytest.mark.parametrize(
    "payload",
    [
        {"avg_points_council": 0, "members": [{"member_id": "member-a", "points": 10}]},
        {
            "avg_points_council": 100,
            "members": [
                {"member_id": "member-a", "points": 0},
                {"member_id": "member-b", "points": 0},
            ],
        },
    ],
)
def test_weight_engine_handles_degenerate_zero_denominators(
    payload: dict[str, object],
) -> None:
    result = calculate_weights(payload)

    assert all(member.kv_raw == 0 for member in result.members)
    assert all(member.kv_capped == 0 for member in result.members)
    assert all(member.payout_share == 0 for member in result.members)
    assert result.total_kv_capped == 0
    assert result.total_payout_share == 0


def test_calculate_payout_shares_rejects_negative_kv_values() -> None:
    with pytest.raises(ValueError, match="kv_capped"):
        calculate_payout_shares([0.1, -0.01])


def test_weight_engine_rejects_negative_points_and_average() -> None:
    with pytest.raises(ValidationError):
        WeightCalculationInput.model_validate(
            {
                "avg_points_council": -1,
                "members": [{"member_id": "member-a", "points": 1}],
            }
        )

    with pytest.raises(ValidationError):
        WeightCalculationInput.model_validate(
            {
                "avg_points_council": 1,
                "members": [{"member_id": "member-a", "points": -1}],
            }
        )
