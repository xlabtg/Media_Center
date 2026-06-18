from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated

from pydantic import AliasChoices, ConfigDict, Field, field_validator

from libs.shared.models import SharedBaseModel

BASE_POINTS: dict[str, float] = {
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

PLATFORM_MULTIPLIERS: dict[str, float] = {
    "youtube": 1.6,
    "telegram": 1.5,
    "gem_space": 1.4,
    "dzen": 1.3,
    "tenchat": 1.3,
    "vk": 1.2,
    "ok": 1.1,
    "default": 1.0,
}

CONTENT_REACH_THRESHOLD = 50_000
CONTENT_HIGH_REACH_THRESHOLD = 100_000
AMPLIFICATION_REACH_STEP = 50_000
AMPLIFICATION_STEP_MULTIPLIER = 0.05

NonNegativeInt = Annotated[int, Field(ge=0)]


class ContributionEventType(StrEnum):
    IDEA = "idea"
    CONTENT_CREATION = "content_creation"
    PUBLISH = "publish"
    AMPLIFY = "amplify"
    ENGAGE = "engage"
    ADMIN_MOD = "admin_mod"
    COORDINATE = "coordinate"
    ORGANIZE = "organize"
    EXPERT_CONSULT = "expert_consult"
    REFERRAL_CONVERT = "referral_convert"
    WEEKLY_ACTIVITY = "weekly_activity"


class Platform(StrEnum):
    YOUTUBE = "youtube"
    TELEGRAM = "telegram"
    GEM_SPACE = "gem_space"
    DZEN = "dzen"
    TENCHAT = "tenchat"
    VK = "vk"
    OK = "ok"
    DEFAULT = "default"


ContributionAction = ContributionEventType
ContributionPlatform = Platform


class PointsCalculationInput(SharedBaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        str_strip_whitespace=True,
    )

    event_type: ContributionEventType = Field(
        validation_alias=AliasChoices("event_type", "action", "contribution_type")
    )
    platform: Platform = Platform.DEFAULT
    reach: NonNegativeInt = 0
    extra_reach: NonNegativeInt = 0

    @field_validator("event_type", "platform", mode="before")
    @classmethod
    def _normalize_token(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value


class PointsCalculationOutput(SharedBaseModel):
    event_type: ContributionEventType
    platform: Platform
    base_points: float
    platform_mult: float
    reach_mult: float
    amp_mult: float
    final_points: float

    @property
    def platform_multiplier(self) -> float:
        return self.platform_mult

    @property
    def reach_multiplier(self) -> float:
        return self.reach_mult

    @property
    def amplification_multiplier(self) -> float:
        return self.amp_mult


ContributionPointsInput = PointsCalculationInput
ContributionPointsOutput = PointsCalculationOutput


def _normalize_enum_value(value: StrEnum | str) -> str:
    return value.value if isinstance(value, StrEnum) else value.strip().lower()


def get_base_points(event_type: ContributionEventType | str) -> float:
    event = ContributionEventType(_normalize_enum_value(event_type))
    return BASE_POINTS[event.value]


def get_platform_multiplier(platform: Platform | str | None) -> float:
    normalized_platform = (
        Platform.DEFAULT
        if platform is None
        else Platform(_normalize_enum_value(platform))
    )
    return PLATFORM_MULTIPLIERS[normalized_platform.value]


def calculate_reach_multiplier(
    event_type: ContributionEventType | str,
    reach: int,
) -> float:
    if reach < 0:
        raise ValueError("reach должен быть неотрицательным")

    event = ContributionEventType(_normalize_enum_value(event_type))
    if event != ContributionEventType.CONTENT_CREATION:
        return 1.0
    if reach >= CONTENT_HIGH_REACH_THRESHOLD:
        return 1.5
    if reach >= CONTENT_REACH_THRESHOLD:
        return 1.3
    return 1.0


def calculate_amplification_multiplier(
    event_type: ContributionEventType | str,
    extra_reach: int,
) -> float:
    if extra_reach < 0:
        raise ValueError("extra_reach должен быть неотрицательным")

    event = ContributionEventType(_normalize_enum_value(event_type))
    if event != ContributionEventType.AMPLIFY:
        return 1.0

    full_steps = extra_reach // AMPLIFICATION_REACH_STEP
    return 1.0 + full_steps * AMPLIFICATION_STEP_MULTIPLIER


def calculate_points(
    payload: PointsCalculationInput | Mapping[str, object] | None = None,
    **kwargs: object,
) -> PointsCalculationOutput:
    if payload is not None and kwargs:
        raise ValueError("payload и keyword-аргументы нельзя передавать одновременно")
    if payload is None and not kwargs:
        raise ValueError("нужны входные данные для расчёта баллов")

    raw_payload: PointsCalculationInput | Mapping[str, object]
    raw_payload = kwargs if payload is None else payload
    request = (
        raw_payload
        if isinstance(raw_payload, PointsCalculationInput)
        else PointsCalculationInput.model_validate(raw_payload)
    )

    base_points = get_base_points(request.event_type)
    platform_mult = get_platform_multiplier(request.platform)
    reach_mult = calculate_reach_multiplier(request.event_type, request.reach)
    amp_mult = calculate_amplification_multiplier(
        request.event_type,
        request.extra_reach,
    )
    final_points = round(base_points * platform_mult * reach_mult * amp_mult, 2)

    return PointsCalculationOutput(
        event_type=request.event_type,
        platform=request.platform,
        base_points=base_points,
        platform_mult=platform_mult,
        reach_mult=reach_mult,
        amp_mult=amp_mult,
        final_points=final_points,
    )
