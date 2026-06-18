from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from enum import StrEnum
from typing import Annotated, Final
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import AliasChoices, ConfigDict, Field, field_validator

from libs.shared.models import IdempotencyKey, SharedBaseModel, SubjectId, TenantId

DEFAULT_L3_MIN_CONTRIBUTION_WEIGHT: Final = 10.0
REFERRAL_URL_MAX_LENGTH: Final = 2_048
ROUTE_ID_LENGTH: Final = 32
WEIGHT_SCALE: Final = 1_000

NonNegativeFiniteFloat = Annotated[
    float,
    Field(ge=0, allow_inf_nan=False),
]


class ReferralLevel(StrEnum):
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"


REFERRAL_REWARD_RATES: Final[dict[ReferralLevel, float]] = {
    ReferralLevel.L1: 0.20,
    ReferralLevel.L2: 0.10,
    ReferralLevel.L3: 0.05,
}


class LinkRotatorError(ValueError):
    """Base error for CGLR referral link routing failures."""


class LinkRotationError(LinkRotatorError):
    """Raised when the rotator cannot select a policy-compliant link."""


class ReferralLinkTarget(SharedBaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        str_strip_whitespace=True,
    )

    owner_id: SubjectId = Field(
        validation_alias=AliasChoices("owner_id", "member_id", "partner_id")
    )
    url: str = Field(min_length=1, max_length=REFERRAL_URL_MAX_LENGTH)
    contribution_weight: NonNegativeFiniteFloat = Field(
        default=0,
        validation_alias=AliasChoices(
            "contribution_weight",
            "weight",
            "points",
        ),
    )

    @field_validator("url")
    @classmethod
    def _validate_url(cls, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"} or parts.netloc == "":
            raise ValueError("url должен быть абсолютной HTTP(S)-ссылкой")
        return value


class LinkRouteRequest(SharedBaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        str_strip_whitespace=True,
    )

    tenant_id: TenantId
    content_id: IdempotencyKey
    admin_link: ReferralLinkTarget = Field(
        validation_alias=AliasChoices("admin_link", "admin_cta", "l1")
    )
    author_link: ReferralLinkTarget = Field(
        validation_alias=AliasChoices("author_link", "author", "l2")
    )
    l3_candidates: tuple[ReferralLinkTarget, ...] = Field(
        default_factory=tuple,
        validation_alias=AliasChoices("l3_candidates", "partners", "l3"),
    )
    rotation_seed: str | None = Field(default=None, min_length=1, max_length=128)
    l3_min_contribution_weight: NonNegativeFiniteFloat = Field(
        default=DEFAULT_L3_MIN_CONTRIBUTION_WEIGHT
    )


class ReferralLink(SharedBaseModel):
    tenant_id: TenantId
    content_id: IdempotencyKey
    route_id: IdempotencyKey
    level: ReferralLevel
    owner_id: SubjectId
    url: str = Field(min_length=1, max_length=REFERRAL_URL_MAX_LENGTH)
    reward_share: NonNegativeFiniteFloat = Field(le=1)
    contribution_weight: NonNegativeFiniteFloat = 0
    rotation_seed: str | None = None


class LinkRouteResult(SharedBaseModel):
    tenant_id: TenantId
    content_id: IdempotencyKey
    l3_min_contribution_weight: NonNegativeFiniteFloat
    links: tuple[ReferralLink, ...]

    @property
    def reward_distribution(self) -> tuple[dict[str, str | float], ...]:
        return tuple(
            {
                "level": link.level.value,
                "owner_id": link.owner_id,
                "reward_share": link.reward_share,
            }
            for link in self.links
        )


class ReferralClickStats(SharedBaseModel):
    tenant_id: TenantId
    content_id: IdempotencyKey
    route_id: IdempotencyKey
    level: ReferralLevel
    owner_id: SubjectId
    reward_share: NonNegativeFiniteFloat = Field(le=1)
    click_count: int = Field(ge=0)


@dataclass(slots=True)
class InMemoryReferralClickTracker:
    _click_counts: dict[tuple[str, str], int] = dataclass_field(default_factory=dict)
    _links_by_route: dict[tuple[str, str], ReferralLink] = dataclass_field(
        default_factory=dict
    )

    def record_click(self, link: ReferralLink) -> ReferralClickStats:
        key = (link.tenant_id, link.route_id)
        self._links_by_route[key] = link
        self._click_counts[key] = self._click_counts.get(key, 0) + 1
        return self._stats_for_link(link)

    def stats_for_tenant(self, tenant_id: str) -> tuple[ReferralClickStats, ...]:
        stats = [
            self._stats_for_link(link)
            for key, link in self._links_by_route.items()
            if key[0] == tenant_id
        ]
        return tuple(sorted(stats, key=lambda item: item.route_id))

    def total_clicks_by_level(self, tenant_id: str) -> dict[ReferralLevel, int]:
        totals = {level: 0 for level in ReferralLevel}
        for key, click_count in self._click_counts.items():
            if key[0] != tenant_id:
                continue
            link = self._links_by_route[key]
            totals[link.level] += click_count
        return totals

    def _stats_for_link(self, link: ReferralLink) -> ReferralClickStats:
        return ReferralClickStats(
            tenant_id=link.tenant_id,
            content_id=link.content_id,
            route_id=link.route_id,
            level=link.level,
            owner_id=link.owner_id,
            reward_share=link.reward_share,
            click_count=self._click_counts.get((link.tenant_id, link.route_id), 0),
        )


def generate_referral_links(
    payload: LinkRouteRequest | Mapping[str, object] | None = None,
    **kwargs: object,
) -> LinkRouteResult:
    if payload is not None and kwargs:
        raise ValueError("payload и keyword-аргументы нельзя передавать одновременно")
    if payload is None and not kwargs:
        raise ValueError("нужны входные данные для генерации реферальных ссылок")

    raw_payload: LinkRouteRequest | Mapping[str, object]
    raw_payload = kwargs if payload is None else payload
    request = (
        raw_payload
        if isinstance(raw_payload, LinkRouteRequest)
        else LinkRouteRequest.model_validate(raw_payload)
    )
    rotation_seed = _resolve_rotation_seed(request)
    l3_target = select_l3_partner(
        request.l3_candidates,
        seed=rotation_seed,
        min_contribution_weight=request.l3_min_contribution_weight,
    )

    links = (
        _build_referral_link(request, ReferralLevel.L1, request.admin_link, None),
        _build_referral_link(request, ReferralLevel.L2, request.author_link, None),
        _build_referral_link(request, ReferralLevel.L3, l3_target, rotation_seed),
    )
    return LinkRouteResult(
        tenant_id=request.tenant_id,
        content_id=request.content_id,
        l3_min_contribution_weight=request.l3_min_contribution_weight,
        links=links,
    )


def select_l3_partner(
    candidates: Sequence[ReferralLinkTarget],
    *,
    seed: str,
    min_contribution_weight: float = DEFAULT_L3_MIN_CONTRIBUTION_WEIGHT,
) -> ReferralLinkTarget:
    eligible_candidates = tuple(
        sorted(
            (
                candidate
                for candidate in candidates
                if candidate.contribution_weight >= min_contribution_weight
            ),
            key=lambda candidate: (candidate.owner_id, candidate.url),
        )
    )
    if not eligible_candidates:
        raise LinkRotationError(
            f"нет L3-кандидатов с contribution_weight >= {min_contribution_weight:g}"
        )

    scaled_weights = tuple(
        _scale_weight(candidate.contribution_weight)
        for candidate in eligible_candidates
    )
    total_weight = sum(scaled_weights)
    offset = _seed_offset(seed, total_weight)
    cumulative_weight = 0
    for candidate, scaled_weight in zip(
        eligible_candidates,
        scaled_weights,
        strict=True,
    ):
        cumulative_weight += scaled_weight
        if offset < cumulative_weight:
            return candidate

    return eligible_candidates[-1]


def _build_referral_link(
    request: LinkRouteRequest,
    level: ReferralLevel,
    target: ReferralLinkTarget,
    rotation_seed: str | None,
) -> ReferralLink:
    route_id = _route_id(
        tenant_id=request.tenant_id,
        content_id=request.content_id,
        level=level,
        target=target,
    )
    url = _append_tracking_params(
        target.url,
        {
            "nmc_route_id": route_id,
            "nmc_tenant_id": request.tenant_id,
            "nmc_content_id": request.content_id,
            "nmc_level": level.value,
            "nmc_owner_id": target.owner_id,
        },
    )
    return ReferralLink(
        tenant_id=request.tenant_id,
        content_id=request.content_id,
        route_id=route_id,
        level=level,
        owner_id=target.owner_id,
        url=url,
        reward_share=REFERRAL_REWARD_RATES[level],
        contribution_weight=target.contribution_weight,
        rotation_seed=rotation_seed,
    )


def _route_id(
    *,
    tenant_id: str,
    content_id: str,
    level: ReferralLevel,
    target: ReferralLinkTarget,
) -> str:
    payload = {
        "content_id": content_id,
        "level": level.value,
        "owner_id": target.owner_id,
        "tenant_id": tenant_id,
        "url": target.url,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()[:ROUTE_ID_LENGTH]


def _resolve_rotation_seed(request: LinkRouteRequest) -> str:
    if request.rotation_seed is not None:
        return request.rotation_seed
    return f"{request.tenant_id}:{request.content_id}:L3"


def _append_tracking_params(url: str, params: Mapping[str, str]) -> str:
    parts = urlsplit(url)
    existing_query = [
        item
        for item in parse_qsl(parts.query, keep_blank_values=True)
        if item[0] not in params
    ]
    query = urlencode([*existing_query, *params.items()])
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


def _scale_weight(weight: float) -> int:
    scaled_weight = round(weight * WEIGHT_SCALE)
    if scaled_weight <= 0:
        raise LinkRotationError("contribution_weight L3-кандидата должен быть больше 0")
    return scaled_weight


def _seed_offset(seed: str, total_weight: int) -> int:
    if total_weight <= 0:
        raise LinkRotationError("суммарный вес L3-кандидатов должен быть больше 0")
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    return int.from_bytes(digest[:16], "big") % total_weight


__all__ = [
    "DEFAULT_L3_MIN_CONTRIBUTION_WEIGHT",
    "InMemoryReferralClickTracker",
    "LinkRouteRequest",
    "LinkRouteResult",
    "LinkRotationError",
    "LinkRotatorError",
    "REFERRAL_REWARD_RATES",
    "ReferralClickStats",
    "ReferralLevel",
    "ReferralLink",
    "ReferralLinkTarget",
    "generate_referral_links",
    "select_l3_partner",
]
