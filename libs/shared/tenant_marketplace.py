from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from threading import Lock
from typing import Literal, overload
from uuid import uuid4

from libs.shared.tenant import TENANT_ID_PATTERN, TenantContext
from libs.shared.tenant_resources import (
    InMemoryTenantResourceManager,
    TenantResourcePlan,
)

Clock = Callable[[], datetime]

TENANT_MARKETPLACE_REQUIRED_CHECKLIST: tuple[str, ...] = (
    "profile",
    "contacts",
    "data_policy",
    "council_review",
)

_MODERATOR_ROLES = frozenset({"council", "presidium", "board"})
_SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$")
_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{1,63}$")


class TenantMarketplaceProfileStatus(StrEnum):
    PUBLISHED = "published"
    HIDDEN = "hidden"
    SUSPENDED = "suspended"


class TenantMarketplaceApplicationStatus(StrEnum):
    SUBMITTED = "submitted"
    NEEDS_CHANGES = "needs_changes"
    REJECTED = "rejected"
    PROVISIONED = "provisioned"


class TenantMarketplaceDecision(StrEnum):
    APPROVE = "approve"
    REQUEST_CHANGES = "request_changes"
    REJECT = "reject"


@dataclass(frozen=True, slots=True)
class TenantMarketplaceProfile:
    tenant_id: str
    slug: str
    name: str
    region: str
    cooperative_type: str
    description: str
    member_count_range: str
    capabilities: Sequence[str]
    status: TenantMarketplaceProfileStatus = TenantMarketplaceProfileStatus.PUBLISHED
    published_at: datetime | None = None
    resource_plan_name: str | None = None
    contact_ref: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "tenant_id", _validated_tenant_id(self.tenant_id))
        object.__setattr__(self, "slug", _validated_slug(self.slug))
        object.__setattr__(self, "name", _required_text(self.name, "name"))
        object.__setattr__(self, "region", _validated_token(self.region, "region"))
        object.__setattr__(
            self,
            "cooperative_type",
            _validated_token(self.cooperative_type, "cooperative_type"),
        )
        object.__setattr__(
            self,
            "description",
            _required_text(self.description, "description"),
        )
        object.__setattr__(
            self,
            "member_count_range",
            _required_text(self.member_count_range, "member_count_range"),
        )
        object.__setattr__(
            self,
            "capabilities",
            _validated_capabilities(self.capabilities),
        )
        if self.contact_ref is not None:
            object.__setattr__(
                self,
                "contact_ref",
                _validated_secret_ref(self.contact_ref, "contact_ref"),
            )
        if self.resource_plan_name is not None:
            object.__setattr__(
                self,
                "resource_plan_name",
                _required_text(self.resource_plan_name, "resource_plan_name"),
            )
        if self.published_at is not None:
            object.__setattr__(
                self,
                "published_at",
                _normalized_datetime(self.published_at),
            )

    def as_public_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "tenant_id": self.tenant_id,
            "slug": self.slug,
            "name": self.name,
            "region": self.region,
            "cooperative_type": self.cooperative_type,
            "description": self.description,
            "member_count_range": self.member_count_range,
            "capabilities": list(self.capabilities),
            "status": self.status.value,
        }
        if self.published_at is not None:
            payload["published_at"] = _isoformat_z(self.published_at)
        if self.resource_plan_name is not None:
            payload["resource_plan_name"] = self.resource_plan_name

        return payload


@dataclass(frozen=True, slots=True)
class TenantMarketplaceSubmission:
    slug: str
    name: str
    region: str
    cooperative_type: str
    description: str
    expected_members: int
    capabilities: Sequence[str]
    contact_ref: str
    requested_plan: TenantResourcePlan
    checklist: Mapping[str, bool] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "slug", _validated_slug(self.slug))
        object.__setattr__(self, "name", _required_text(self.name, "name"))
        object.__setattr__(self, "region", _validated_token(self.region, "region"))
        object.__setattr__(
            self,
            "cooperative_type",
            _validated_token(self.cooperative_type, "cooperative_type"),
        )
        object.__setattr__(
            self,
            "description",
            _required_text(self.description, "description"),
        )
        if self.expected_members <= 0:
            raise ValueError("expected_members должен быть положительным")
        object.__setattr__(
            self,
            "capabilities",
            _validated_capabilities(self.capabilities),
        )
        object.__setattr__(
            self,
            "contact_ref",
            _validated_secret_ref(self.contact_ref, "contact_ref"),
        )
        object.__setattr__(
            self,
            "checklist",
            _normalized_checklist(self.checklist),
        )

    @property
    def ready_for_moderation(self) -> bool:
        return all(
            self.checklist.get(step, False)
            for step in TENANT_MARKETPLACE_REQUIRED_CHECKLIST
        )

    @property
    def blockers(self) -> tuple[str, ...]:
        return tuple(
            step
            for step in TENANT_MARKETPLACE_REQUIRED_CHECKLIST
            if not self.checklist.get(step, False)
        )


@dataclass(frozen=True, slots=True)
class TenantMarketplaceModerationRecord:
    reviewer_subject: str
    decision: TenantMarketplaceDecision
    decided_at: datetime
    comment: str
    status_after: TenantMarketplaceApplicationStatus

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "reviewer_subject",
            _required_text(self.reviewer_subject, "reviewer_subject"),
        )
        object.__setattr__(
            self,
            "decided_at",
            _normalized_datetime(self.decided_at),
        )
        object.__setattr__(self, "comment", self.comment.strip())


@dataclass(frozen=True, slots=True)
class TenantMarketplaceApplication:
    application_id: str
    submission: TenantMarketplaceSubmission
    applicant_subject: str
    submitted_at: datetime
    status: TenantMarketplaceApplicationStatus = (
        TenantMarketplaceApplicationStatus.SUBMITTED
    )
    moderation_history: tuple[TenantMarketplaceModerationRecord, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "application_id",
            _validated_application_id(self.application_id),
        )
        object.__setattr__(
            self,
            "applicant_subject",
            _required_text(self.applicant_subject, "applicant_subject"),
        )
        object.__setattr__(
            self,
            "submitted_at",
            _normalized_datetime(self.submitted_at),
        )

    @property
    def ready_for_moderation(self) -> bool:
        return (
            self.status is TenantMarketplaceApplicationStatus.SUBMITTED
            and self.submission.ready_for_moderation
        )

    @property
    def blockers(self) -> tuple[str, ...]:
        if self.status is not TenantMarketplaceApplicationStatus.SUBMITTED:
            return ("status",)
        return self.submission.blockers


@dataclass(frozen=True, slots=True)
class TenantMarketplaceProvisioningResult:
    tenant_id: str
    application: TenantMarketplaceApplication
    profile: TenantMarketplaceProfile
    resource_plan: TenantResourcePlan

    def __post_init__(self) -> None:
        object.__setattr__(self, "tenant_id", _validated_tenant_id(self.tenant_id))


class InMemoryTenantMarketplace:
    """Tenant catalog and self-service onboarding workflow for local wiring."""

    def __init__(
        self,
        *,
        resource_manager: InMemoryTenantResourceManager | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._resource_manager = resource_manager
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = Lock()
        self._profiles: dict[str, TenantMarketplaceProfile] = {}
        self._profiles_by_slug: dict[str, str] = {}
        self._applications: dict[str, TenantMarketplaceApplication] = {}
        self._applications_by_slug: dict[str, str] = {}

    def publish_profile(
        self,
        profile: TenantMarketplaceProfile,
    ) -> TenantMarketplaceProfile:
        with self._lock:
            self._ensure_slug_available_for_profile(profile.slug, profile.tenant_id)
            self._profiles[profile.tenant_id] = profile
            self._profiles_by_slug[profile.slug] = profile.tenant_id
            return profile

    def list_catalog(
        self,
        *,
        region: str | None = None,
        cooperative_type: str | None = None,
    ) -> tuple[TenantMarketplaceProfile, ...]:
        normalized_region = (
            _validated_token(region, "region") if region is not None else None
        )
        normalized_type = (
            _validated_token(cooperative_type, "cooperative_type")
            if cooperative_type is not None
            else None
        )
        with self._lock:
            profiles = [
                profile
                for profile in self._profiles.values()
                if profile.status is TenantMarketplaceProfileStatus.PUBLISHED
                and (normalized_region is None or profile.region == normalized_region)
                and (
                    normalized_type is None
                    or profile.cooperative_type == normalized_type
                )
            ]

        return tuple(
            sorted(profiles, key=lambda profile: (profile.region, profile.slug))
        )

    def submit_application(
        self,
        submission: TenantMarketplaceSubmission,
        *,
        applicant: TenantContext,
        submitted_at: datetime | None = None,
        application_id: str | None = None,
    ) -> TenantMarketplaceApplication:
        applicant_subject = _required_text(
            applicant.subject or "",
            "applicant.subject",
        )
        normalized_application_id = (
            _validated_application_id(application_id)
            if application_id is not None
            else f"app-{submission.slug}-{uuid4().hex[:8]}"
        )
        application = TenantMarketplaceApplication(
            application_id=normalized_application_id,
            submission=submission,
            applicant_subject=applicant_subject,
            submitted_at=submitted_at or self._clock(),
        )

        with self._lock:
            self._ensure_slug_available_for_application(submission.slug)
            if normalized_application_id in self._applications:
                raise ValueError("application_id уже существует")
            self._applications[normalized_application_id] = application
            self._applications_by_slug[submission.slug] = normalized_application_id
            return application

    @overload
    def moderate_application(
        self,
        application_id: str,
        *,
        decision: Literal[TenantMarketplaceDecision.APPROVE],
        reviewer: TenantContext,
        decided_at: datetime | None = None,
        comment: str = "",
        tenant_id: str,
    ) -> TenantMarketplaceProvisioningResult: ...

    @overload
    def moderate_application(
        self,
        application_id: str,
        *,
        decision: Literal[
            TenantMarketplaceDecision.REQUEST_CHANGES,
            TenantMarketplaceDecision.REJECT,
        ],
        reviewer: TenantContext,
        decided_at: datetime | None = None,
        comment: str = "",
        tenant_id: str | None = None,
    ) -> TenantMarketplaceApplication: ...

    def moderate_application(
        self,
        application_id: str,
        *,
        decision: TenantMarketplaceDecision,
        reviewer: TenantContext,
        decided_at: datetime | None = None,
        comment: str = "",
        tenant_id: str | None = None,
    ) -> TenantMarketplaceApplication | TenantMarketplaceProvisioningResult:
        reviewer_subject = _moderator_subject(reviewer)
        normalized_application_id = _validated_application_id(application_id)
        normalized_decided_at = _normalized_datetime(decided_at or self._clock())
        normalized_comment = comment.strip()

        with self._lock:
            application = self._application_or_raise(normalized_application_id)
            if decision is TenantMarketplaceDecision.APPROVE:
                return self._approve_application(
                    application=application,
                    tenant_id=tenant_id,
                    reviewer_subject=reviewer_subject,
                    decided_at=normalized_decided_at,
                    comment=normalized_comment,
                )
            if decision is TenantMarketplaceDecision.REQUEST_CHANGES:
                return self._store_moderation_result(
                    application,
                    decision=decision,
                    reviewer_subject=reviewer_subject,
                    decided_at=normalized_decided_at,
                    comment=normalized_comment,
                    status=TenantMarketplaceApplicationStatus.NEEDS_CHANGES,
                )
            if decision is TenantMarketplaceDecision.REJECT:
                updated = self._store_moderation_result(
                    application,
                    decision=decision,
                    reviewer_subject=reviewer_subject,
                    decided_at=normalized_decided_at,
                    comment=normalized_comment,
                    status=TenantMarketplaceApplicationStatus.REJECTED,
                )
                self._applications_by_slug.pop(application.submission.slug, None)
                return updated

        raise ValueError("неподдерживаемое решение модерации")

    def get_application(self, application_id: str) -> TenantMarketplaceApplication:
        normalized_application_id = _validated_application_id(application_id)
        with self._lock:
            return self._application_or_raise(normalized_application_id)

    def list_applications(
        self,
        *,
        status: TenantMarketplaceApplicationStatus | None = None,
    ) -> tuple[TenantMarketplaceApplication, ...]:
        with self._lock:
            applications = [
                application
                for application in self._applications.values()
                if status is None or application.status is status
            ]

        return tuple(
            sorted(
                applications,
                key=lambda application: (
                    application.submitted_at,
                    application.application_id,
                ),
            ),
        )

    def _approve_application(
        self,
        *,
        application: TenantMarketplaceApplication,
        tenant_id: str | None,
        reviewer_subject: str,
        decided_at: datetime,
        comment: str,
    ) -> TenantMarketplaceProvisioningResult:
        if tenant_id is None:
            raise ValueError("tenant_id обязателен для одобрения заявки")
        if not application.ready_for_moderation:
            raise ValueError(
                "заявка не готова к модерации: " + ", ".join(application.blockers),
            )

        normalized_tenant_id = _validated_tenant_id(tenant_id)
        self._ensure_slug_available_for_profile(
            application.submission.slug,
            normalized_tenant_id,
        )
        if normalized_tenant_id in self._profiles:
            raise ValueError("tenant_id уже существует")

        profile = TenantMarketplaceProfile(
            tenant_id=normalized_tenant_id,
            slug=application.submission.slug,
            name=application.submission.name,
            region=application.submission.region,
            cooperative_type=application.submission.cooperative_type,
            description=application.submission.description,
            member_count_range=_member_count_range(
                application.submission.expected_members,
            ),
            capabilities=application.submission.capabilities,
            status=TenantMarketplaceProfileStatus.PUBLISHED,
            published_at=decided_at,
            resource_plan_name=application.submission.requested_plan.name,
            contact_ref=application.submission.contact_ref,
        )
        updated_application = self._store_moderation_result(
            application,
            decision=TenantMarketplaceDecision.APPROVE,
            reviewer_subject=reviewer_subject,
            decided_at=decided_at,
            comment=comment,
            status=TenantMarketplaceApplicationStatus.PROVISIONED,
        )
        self._profiles[profile.tenant_id] = profile
        self._profiles_by_slug[profile.slug] = profile.tenant_id

        if self._resource_manager is not None:
            self._resource_manager.configure_tenant(
                normalized_tenant_id,
                application.submission.requested_plan,
            )

        return TenantMarketplaceProvisioningResult(
            tenant_id=normalized_tenant_id,
            application=updated_application,
            profile=profile,
            resource_plan=application.submission.requested_plan,
        )

    def _store_moderation_result(
        self,
        application: TenantMarketplaceApplication,
        *,
        decision: TenantMarketplaceDecision,
        reviewer_subject: str,
        decided_at: datetime,
        comment: str,
        status: TenantMarketplaceApplicationStatus,
    ) -> TenantMarketplaceApplication:
        moderation_record = TenantMarketplaceModerationRecord(
            reviewer_subject=reviewer_subject,
            decision=decision,
            decided_at=decided_at,
            comment=comment,
            status_after=status,
        )
        updated = replace(
            application,
            status=status,
            moderation_history=application.moderation_history + (moderation_record,),
        )
        self._applications[application.application_id] = updated
        return updated

    def _application_or_raise(
        self,
        application_id: str,
    ) -> TenantMarketplaceApplication:
        application = self._applications.get(application_id)
        if application is None:
            raise ValueError("заявка не найдена")
        return application

    def _ensure_slug_available_for_profile(self, slug: str, tenant_id: str) -> None:
        tenant_for_slug = self._profiles_by_slug.get(slug)
        if tenant_for_slug is not None and tenant_for_slug != tenant_id:
            raise ValueError("slug уже занят")

    def _ensure_slug_available_for_application(self, slug: str) -> None:
        if slug in self._profiles_by_slug:
            raise ValueError("slug уже занят")
        existing_application_id = self._applications_by_slug.get(slug)
        if existing_application_id is None:
            return

        existing = self._applications.get(existing_application_id)
        if existing is not None and existing.status is not (
            TenantMarketplaceApplicationStatus.REJECTED
        ):
            raise ValueError("slug уже занят")


def _moderator_subject(context: TenantContext) -> str:
    if not _MODERATOR_ROLES.intersection(context.roles):
        raise ValueError("модерация доступна только ролям council, presidium или board")
    return _required_text(context.subject or "", "reviewer.subject")


def _validated_tenant_id(tenant_id: str) -> str:
    normalized = tenant_id.strip()
    if TENANT_ID_PATTERN.fullmatch(normalized) is None:
        raise ValueError("tenant_id имеет недопустимый формат")
    return normalized


def _validated_slug(slug: str) -> str:
    normalized = slug.strip().lower()
    if _SLUG_PATTERN.fullmatch(normalized) is None:
        raise ValueError("slug имеет недопустимый формат")
    return normalized


def _validated_application_id(application_id: str) -> str:
    normalized = application_id.strip()
    if _TOKEN_PATTERN.fullmatch(normalized) is None:
        raise ValueError("application_id имеет недопустимый формат")
    return normalized


def _validated_token(value: str, label: str) -> str:
    normalized = value.strip()
    if _TOKEN_PATTERN.fullmatch(normalized) is None:
        raise ValueError(f"{label} имеет недопустимый формат")
    return normalized


def _required_text(value: str, label: str) -> str:
    normalized = value.strip()
    if normalized == "":
        raise ValueError(f"{label} должен быть непустой строкой")
    return normalized


def _validated_capabilities(capabilities: Sequence[str]) -> tuple[str, ...]:
    normalized = tuple(
        _validated_token(capability, "capability") for capability in capabilities
    )
    if not normalized:
        raise ValueError("capabilities должен содержать минимум один элемент")
    return normalized


def _validated_secret_ref(value: str, label: str) -> str:
    normalized = value.strip()
    if not normalized.startswith("vault://"):
        raise ValueError(f"{label} должен ссылаться на секретный контур")
    return normalized


def _normalized_checklist(checklist: Mapping[str, bool]) -> dict[str, bool]:
    normalized = {
        _validated_token(step, "checklist.step"): bool(done)
        for step, done in checklist.items()
    }
    for step in TENANT_MARKETPLACE_REQUIRED_CHECKLIST:
        normalized.setdefault(step, False)
    return normalized


def _normalized_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _isoformat_z(value: datetime) -> str:
    return _normalized_datetime(value).isoformat().replace("+00:00", "Z")


def _member_count_range(expected_members: int) -> str:
    if expected_members <= 10:
        return "1-10"
    if expected_members <= 25:
        return "15-25"
    if expected_members <= 50:
        return "26-50"
    if expected_members <= 100:
        return "51-100"
    return "100+"
