from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import isfinite
from typing import Annotated

from pydantic import AliasChoices, ConfigDict, Field

from libs.shared.models import SharedBaseModel, SubjectId

COUNCIL_CAP_KV = 0.10
KV_RAW_DECIMALS = 6
KV_CAPPED_DECIMALS = 5
PAYOUT_SHARE_DECIMALS = 10

NonNegativeFiniteFloat = Annotated[
    float,
    Field(ge=0, allow_inf_nan=False),
]


class MemberPointsInput(SharedBaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        str_strip_whitespace=True,
    )

    member_id: SubjectId
    total_points: NonNegativeFiniteFloat = Field(
        validation_alias=AliasChoices("total_points", "points", "points_member")
    )


class WeightCalculationInput(SharedBaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        str_strip_whitespace=True,
    )

    avg_points_council: NonNegativeFiniteFloat = Field(
        validation_alias=AliasChoices(
            "avg_points_council",
            "average_points_council",
            "council_average",
        )
    )
    members: tuple[MemberPointsInput, ...] = Field(default_factory=tuple)
    cap_kv: float = Field(
        default=COUNCIL_CAP_KV,
        ge=0,
        le=1,
        allow_inf_nan=False,
        validation_alias=AliasChoices("cap_kv", "council_cap_kv", "kv_cap"),
    )


class MemberWeightOutput(SharedBaseModel):
    member_id: SubjectId
    total_points: NonNegativeFiniteFloat
    avg_points_council: NonNegativeFiniteFloat
    kv_raw: NonNegativeFiniteFloat
    kv_capped: NonNegativeFiniteFloat
    payout_share: NonNegativeFiniteFloat


class WeightCalculationOutput(SharedBaseModel):
    avg_points_council: NonNegativeFiniteFloat
    cap_kv: NonNegativeFiniteFloat
    total_kv_capped: NonNegativeFiniteFloat
    members: tuple[MemberWeightOutput, ...]

    @property
    def total_payout_share(self) -> float:
        return round(
            sum(member.payout_share for member in self.members),
            PAYOUT_SHARE_DECIMALS,
        )


ContributionWeightInput = WeightCalculationInput
ContributionWeightOutput = WeightCalculationOutput


def _validate_non_negative_finite(value: float, field_name: str) -> None:
    if not isfinite(value) or value < 0:
        raise ValueError(f"{field_name} должен быть конечным неотрицательным числом")


def calculate_member_kv(
    total_points: float,
    avg_points_council: float,
    cap_kv: float = COUNCIL_CAP_KV,
) -> tuple[float, float]:
    _validate_non_negative_finite(total_points, "total_points")
    _validate_non_negative_finite(avg_points_council, "avg_points_council")
    _validate_non_negative_finite(cap_kv, "cap_kv")
    if cap_kv > 1:
        raise ValueError("cap_kv должен быть не больше 1")
    if avg_points_council == 0 or total_points == 0 or cap_kv == 0:
        return 0.0, 0.0

    kv_raw = round(total_points / avg_points_council, KV_RAW_DECIMALS)
    kv_capped = round(min(kv_raw, cap_kv), KV_CAPPED_DECIMALS)
    return kv_raw, kv_capped


def calculate_payout_shares(kv_capped_values: Sequence[float]) -> tuple[float, ...]:
    for value in kv_capped_values:
        _validate_non_negative_finite(value, "kv_capped")

    if not kv_capped_values:
        return ()

    rounded_values = tuple(
        round(value, KV_CAPPED_DECIMALS) for value in kv_capped_values
    )
    total_kv_capped = round(sum(rounded_values), KV_CAPPED_DECIMALS)
    if total_kv_capped == 0:
        return tuple(0.0 for _ in rounded_values)

    shares = [
        round(value / total_kv_capped, PAYOUT_SHARE_DECIMALS) if value > 0 else 0.0
        for value in rounded_values
    ]
    residual = round(1.0 - sum(shares), PAYOUT_SHARE_DECIMALS)
    if residual != 0:
        positive_indexes = [
            index for index, value in enumerate(rounded_values) if value > 0
        ]
        if positive_indexes:
            last_positive_index = positive_indexes[-1]
            shares[last_positive_index] = round(
                shares[last_positive_index] + residual,
                PAYOUT_SHARE_DECIMALS,
            )

    return tuple(shares)


def calculate_weights(
    payload: WeightCalculationInput | Mapping[str, object] | None = None,
    **kwargs: object,
) -> WeightCalculationOutput:
    if payload is not None and kwargs:
        raise ValueError("payload и keyword-аргументы нельзя передавать одновременно")
    if payload is None and not kwargs:
        raise ValueError("нужны входные данные для расчёта Кв")

    raw_payload: WeightCalculationInput | Mapping[str, object]
    raw_payload = kwargs if payload is None else payload
    request = (
        raw_payload
        if isinstance(raw_payload, WeightCalculationInput)
        else WeightCalculationInput.model_validate(raw_payload)
    )

    member_weights: list[tuple[MemberPointsInput, float, float]] = []
    for member in request.members:
        kv_raw, kv_capped = calculate_member_kv(
            member.total_points,
            request.avg_points_council,
            request.cap_kv,
        )
        member_weights.append((member, kv_raw, kv_capped))

    payout_shares = calculate_payout_shares(
        [kv_capped for _, _, kv_capped in member_weights]
    )
    members = tuple(
        MemberWeightOutput(
            member_id=member.member_id,
            total_points=member.total_points,
            avg_points_council=request.avg_points_council,
            kv_raw=kv_raw,
            kv_capped=kv_capped,
            payout_share=payout_share,
        )
        for (member, kv_raw, kv_capped), payout_share in zip(
            member_weights,
            payout_shares,
            strict=True,
        )
    )

    return WeightCalculationOutput(
        avg_points_council=request.avg_points_council,
        cap_kv=request.cap_kv,
        total_kv_capped=round(
            sum(kv_capped for _, _, kv_capped in member_weights),
            KV_CAPPED_DECIMALS,
        ),
        members=members,
    )
