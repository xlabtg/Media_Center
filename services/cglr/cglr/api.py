from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Annotated, Protocol, cast
from uuid import uuid4

from contribution_ledger import (
    ContributionEventType,
    Platform,
    calculate_points,
    record_contribution_event,
)
from fastapi import APIRouter, Depends, FastAPI, Header, Path, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import AliasChoices, ConfigDict, Field, field_validator

from libs.shared.errors import (
    IDEMPOTENCY_CONFLICT_CODE,
    VALIDATION_ERROR_CODE,
    SharedError,
    error_response_body,
)
from libs.shared.events import (
    EventPublisher,
    InMemoryEventBus,
)
from libs.shared.models import (
    IdempotencyKey,
    JSONValue,
    SharedBaseModel,
    SubjectId,
    TenantId,
)
from libs.shared.server import (
    BaseAppConfig,
    create_service_runtime_app,
)
from libs.shared.service_template import ServiceTemplateConfig
from libs.shared.tenant import (
    InMemoryAuditSink,
    TenantContext,
    TenantCoreError,
    TenantScopedRepository,
    require_tenant_context,
)

from .link_rotator import (
    DEFAULT_L3_MIN_CONTRIBUTION_WEIGHT,
    LinkRotationError,
    LinkRotatorError,
    LinkRouteRequest,
    ReferralLevel,
    ReferralLink,
    ReferralLinkTarget,
    generate_referral_links,
)
from .template_engine import (
    CONTEXT_KEY_PATTERN,
    TemplateEngineError,
    TemplateRenderRequest,
    TemplateValidationRules,
    render_template,
)

CGLR_SERVICE_NAME = "cglr"
CGLR_EVENT_SOURCE = "cglr"
CGLR_EVENT_SCHEMA_VERSION = "1.0"
CONTENT_GENERATED_EVENT = "content.generated"
CONTRIBUTION_SOURCE_TYPE = "cglr_generation"

_IDEMPOTENCY_HEADER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$"
_PLATFORM_TARGET_PATTERN = r"^[a-z][a-z0-9_-]{0,63}$"

IdempotencyHeader = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=1,
        max_length=128,
        pattern=_IDEMPOTENCY_HEADER_PATTERN,
    ),
]
ContentIdPath = Annotated[
    str,
    Path(min_length=1, max_length=128, pattern=_IDEMPOTENCY_HEADER_PATTERN),
]
PlatformTarget = Annotated[
    str,
    Field(min_length=1, max_length=64, pattern=_PLATFORM_TARGET_PATTERN),
]
NonNegativeFiniteFloat = Annotated[
    float,
    Field(ge=0, allow_inf_nan=False),
]


class LinkRoutingInput(SharedBaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        str_strip_whitespace=True,
    )

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


class ContributionLogSettings(SharedBaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    member_id: SubjectId | None = None
    event_type: ContributionEventType = ContributionEventType.CONTENT_CREATION
    platform: Platform = Platform.DEFAULT
    reach: int = Field(default=0, ge=0)
    extra_reach: int = Field(default=0, ge=0)
    metadata: dict[str, JSONValue] = Field(default_factory=dict)
    occurred_at: datetime | None = None

    @field_validator("event_type", "platform", mode="before")
    @classmethod
    def _normalize_tokens(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("occurred_at")
    @classmethod
    def _normalize_optional_datetime(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return _normalize_datetime(value)


class GenerateContentRequest(SharedBaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    template_id: IdempotencyKey
    template_body: str = Field(min_length=1, max_length=50_000)
    context: dict[str, JSONValue] = Field(default_factory=dict)
    validation: TemplateValidationRules = Field(default_factory=TemplateValidationRules)
    platform_targets: tuple[PlatformTarget, ...] = Field(default_factory=tuple)
    link_routing: LinkRoutingInput
    contribution: ContributionLogSettings = Field(
        default_factory=ContributionLogSettings
    )

    @field_validator("context")
    @classmethod
    def _validate_context_keys(
        cls,
        value: dict[str, JSONValue],
    ) -> dict[str, JSONValue]:
        invalid_keys = [
            key
            for key in value
            if CONTEXT_KEY_PATTERN.fullmatch(key) is None
            or _is_private_context_key(key)
        ]
        if invalid_keys:
            raise ValueError(
                "context содержит небезопасные ключи: "
                + ", ".join(sorted(invalid_keys))
            )
        return value

    def template_payload(self) -> TemplateRenderRequest:
        return TemplateRenderRequest(
            template_body=self.template_body,
            context=self.context,
            validation=self.validation,
        )


class RewardDistributionItem(SharedBaseModel):
    level: ReferralLevel
    owner_id: SubjectId
    reward_share: NonNegativeFiniteFloat = Field(le=1)


class LoggedContributionResponse(SharedBaseModel):
    contribution_id: IdempotencyKey
    tenant_id: TenantId
    member_id: SubjectId
    event_type: ContributionEventType
    source_type: str
    source_ref: IdempotencyKey
    points_awarded: NonNegativeFiniteFloat
    audit_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    idempotency_key: IdempotencyKey
    occurred_at: datetime
    created_at: datetime


class GeneratedContentResponse(SharedBaseModel):
    content_id: IdempotencyKey
    tenant_id: TenantId
    template_id: IdempotencyKey
    content: str
    content_with_links: str
    content_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    links: tuple[ReferralLink, ...]
    reward_distribution: tuple[RewardDistributionItem, ...]
    contribution: LoggedContributionResponse
    created_at: datetime


@dataclass(frozen=True, slots=True)
class GeneratedContentRecord:
    content_id: str
    tenant_id: str
    template_id: str
    content: str
    content_with_links: str
    content_hash: str
    links: tuple[ReferralLink, ...]
    reward_distribution: tuple[RewardDistributionItem, ...]
    contribution: LoggedContributionResponse
    idempotency_key: str
    request_hash: str
    created_at: datetime


@dataclass(slots=True)
class InMemoryGeneratedContentRepository:
    _records: list[GeneratedContentRecord] = field(default_factory=list)
    _tenant_guard: TenantScopedRepository[GeneratedContentRecord] = field(
        default_factory=lambda: TenantScopedRepository("generated_content")
    )

    def get_existing_generation(
        self,
        *,
        context: TenantContext,
        idempotency_key: str,
        request_hash: str,
    ) -> GeneratedContentRecord | None:
        for record in self._records:
            if (
                record.tenant_id == context.tenant_id
                and record.idempotency_key == idempotency_key
            ):
                if record.request_hash != request_hash:
                    raise SharedError(
                        status_code=409,
                        error_code=IDEMPOTENCY_CONFLICT_CODE,
                        message="Idempotency-Key уже использован с другим payload",
                        correlation_id=context.correlation_id,
                    )
                return record

        return None

    def add_generation(
        self,
        record: GeneratedContentRecord,
    ) -> GeneratedContentRecord:
        self._records.append(record)
        return record

    def get_content(
        self,
        *,
        context: TenantContext,
        content_id: str,
        audit_sink: InMemoryAuditSink | None = None,
    ) -> GeneratedContentRecord | None:
        return self._tenant_guard.get_owned(
            self._records,
            content_id,
            context,
            id_field="content_id",
            audit_sink=audit_sink,
        )


class ContributionLogger(Protocol):
    async def log_generation(
        self,
        *,
        context: TenantContext,
        payload: GenerateContentRequest,
        content_id: str,
        content_hash: str,
        occurred_at: datetime,
    ) -> LoggedContributionResponse:
        """Create a Contribution Ledger-compatible record for generated content."""


@dataclass(slots=True)
class InMemoryContributionLogger:
    publisher: EventPublisher
    _records: list[LoggedContributionResponse] = field(default_factory=list)

    @property
    def records(self) -> tuple[LoggedContributionResponse, ...]:
        return tuple(self._records)

    async def log_generation(
        self,
        *,
        context: TenantContext,
        payload: GenerateContentRequest,
        content_id: str,
        content_hash: str,
        occurred_at: datetime,
    ) -> LoggedContributionResponse:
        member_id = payload.contribution.member_id or context.subject
        if member_id is None:
            raise SharedError(
                status_code=400,
                error_code=VALIDATION_ERROR_CODE,
                message="member_id должен быть задан в contribution или JWT subject",
                correlation_id=context.correlation_id,
            )

        calculated = calculate_points(
            event_type=payload.contribution.event_type,
            platform=payload.contribution.platform,
            reach=payload.contribution.reach,
            extra_reach=payload.contribution.extra_reach,
        )
        created_at = datetime.now(UTC)
        contribution_id = f"contribution-{uuid4()}"
        idempotency_key = f"cglr:{content_id}"
        event_result = await record_contribution_event(
            publisher=self.publisher,
            contribution_id=contribution_id,
            tenant_id=context.tenant_id,
            member_id=member_id,
            contribution_type=payload.contribution.event_type,
            points_awarded=calculated.final_points,
            metadata=_contribution_metadata(
                payload=payload,
                content_id=content_id,
                content_hash=content_hash,
            ),
            occurred_at=occurred_at,
            correlation_id=_correlation_id(context),
            causation_id=content_id,
        )
        record = LoggedContributionResponse(
            contribution_id=contribution_id,
            tenant_id=context.tenant_id,
            member_id=member_id,
            event_type=ContributionEventType(event_result.contribution_type),
            source_type=CONTRIBUTION_SOURCE_TYPE,
            source_ref=content_id,
            points_awarded=event_result.points_awarded,
            audit_hash=event_result.audit_hash,
            idempotency_key=idempotency_key,
            occurred_at=occurred_at,
            created_at=created_at,
        )
        self._records.append(record)
        return record


@dataclass(slots=True)
class CGLRAPIState:
    repository: InMemoryGeneratedContentRepository
    publisher: InMemoryEventBus
    contribution_logger: ContributionLogger
    audit_sink: InMemoryAuditSink


router = APIRouter(tags=["CGLR"])


def create_cglr_app(
    config: BaseAppConfig | ServiceTemplateConfig,
    *,
    repository: InMemoryGeneratedContentRepository | None = None,
    publisher: InMemoryEventBus | None = None,
    contribution_logger: ContributionLogger | None = None,
    audit_sink: InMemoryAuditSink | None = None,
) -> FastAPI:
    resolved_audit_sink = audit_sink or InMemoryAuditSink()
    resolved_publisher = publisher or InMemoryEventBus()
    app = create_service_runtime_app(
        config,
        title="Media Center Content Generator & Link Router",
        audit_sink=resolved_audit_sink,
    )
    app.state.cglr_api = CGLRAPIState(
        repository=repository or InMemoryGeneratedContentRepository(),
        publisher=resolved_publisher,
        contribution_logger=contribution_logger
        or InMemoryContributionLogger(resolved_publisher),
        audit_sink=resolved_audit_sink,
    )
    app.add_exception_handler(TenantCoreError, _tenant_core_error_handler)
    app.add_exception_handler(SharedError, _shared_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(TemplateEngineError, _domain_error_handler)
    app.add_exception_handler(LinkRotatorError, _domain_error_handler)
    app.include_router(router)
    return app


@router.post(
    "/generate",
    response_model=GeneratedContentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Сгенерировать контент и зарегистрировать вклад",
)
async def generate_content(
    payload: GenerateContentRequest,
    idempotency_key: IdempotencyHeader,
    state: Annotated[CGLRAPIState, Depends(_api_state)],
    context: Annotated[TenantContext, Depends(_tenant_context)],
) -> GeneratedContentResponse:
    request_hash = _hash_json_payload(
        {
            "tenant_id": context.tenant_id,
            "idempotency_key": idempotency_key,
            "payload": cast(
                dict[str, JSONValue],
                payload.model_dump(mode="json", exclude_none=True),
            ),
        }
    )
    existing_record = state.repository.get_existing_generation(
        context=context,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
    )
    if existing_record is not None:
        return _generated_content_response(existing_record)

    content_id = f"content-{uuid4()}"
    rendered = render_template(payload.template_payload())
    route_result = generate_referral_links(
        LinkRouteRequest(
            tenant_id=context.tenant_id,
            content_id=content_id,
            admin_link=payload.link_routing.admin_link,
            author_link=payload.link_routing.author_link,
            l3_candidates=payload.link_routing.l3_candidates,
            rotation_seed=payload.link_routing.rotation_seed,
            l3_min_contribution_weight=(
                payload.link_routing.l3_min_contribution_weight
            ),
        )
    )
    reward_distribution = _reward_distribution(route_result.links)
    content_with_links = _content_with_links(rendered.content, route_result.links)
    content_hash = _content_hash(
        tenant_id=context.tenant_id,
        template_id=payload.template_id,
        content=rendered.content,
        links=route_result.links,
    )
    created_at = datetime.now(UTC)
    await _publish_content_generated(
        publisher=state.publisher,
        context=context,
        payload=payload,
        content_id=content_id,
        content_hash=content_hash,
        idempotency_key=idempotency_key,
        occurred_at=created_at,
    )
    contribution = await state.contribution_logger.log_generation(
        context=context,
        payload=payload,
        content_id=content_id,
        content_hash=content_hash,
        occurred_at=payload.contribution.occurred_at or created_at,
    )
    record = state.repository.add_generation(
        GeneratedContentRecord(
            content_id=content_id,
            tenant_id=context.tenant_id,
            template_id=payload.template_id,
            content=rendered.content,
            content_with_links=content_with_links,
            content_hash=content_hash,
            links=route_result.links,
            reward_distribution=reward_distribution,
            contribution=contribution,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            created_at=created_at,
        )
    )
    return _generated_content_response(record)


@router.get(
    "/content/{content_id}",
    response_model=GeneratedContentResponse,
    summary="Получить готовый сгенерированный контент",
)
def get_content(
    content_id: ContentIdPath,
    state: Annotated[CGLRAPIState, Depends(_api_state)],
    context: Annotated[TenantContext, Depends(_tenant_context)],
) -> GeneratedContentResponse:
    record = state.repository.get_content(
        context=context,
        content_id=content_id,
        audit_sink=state.audit_sink,
    )
    if record is None:
        raise SharedError(
            status_code=404,
            error_code="content_not_found",
            message="Сгенерированный контент не найден",
            correlation_id=context.correlation_id,
        )

    return _generated_content_response(record)


def _api_state(request: Request) -> CGLRAPIState:
    return cast(CGLRAPIState, request.app.state.cglr_api)


def _tenant_context() -> TenantContext:
    return require_tenant_context()


def _generated_content_response(
    record: GeneratedContentRecord,
) -> GeneratedContentResponse:
    return GeneratedContentResponse(
        content_id=record.content_id,
        tenant_id=record.tenant_id,
        template_id=record.template_id,
        content=record.content,
        content_with_links=record.content_with_links,
        content_hash=record.content_hash,
        links=record.links,
        reward_distribution=record.reward_distribution,
        contribution=record.contribution,
        created_at=record.created_at,
    )


def _reward_distribution(
    links: tuple[ReferralLink, ...],
) -> tuple[RewardDistributionItem, ...]:
    return tuple(
        RewardDistributionItem(
            level=link.level,
            owner_id=link.owner_id,
            reward_share=link.reward_share,
        )
        for link in links
    )


def _content_with_links(content: str, links: tuple[ReferralLink, ...]) -> str:
    if not links:
        return content

    link_lines = [f"{link.level.value}: {link.url}" for link in links]
    return content.rstrip() + "\n\nРеферальные ссылки:\n" + "\n".join(link_lines)


def _content_hash(
    *,
    tenant_id: str,
    template_id: str,
    content: str,
    links: tuple[ReferralLink, ...],
) -> str:
    return _hash_json_payload(
        {
            "tenant_id": tenant_id,
            "template_id": template_id,
            "content": content,
            "links": [
                cast(dict[str, JSONValue], link.model_dump(mode="json"))
                for link in links
            ],
        }
    )


async def _publish_content_generated(
    *,
    publisher: EventPublisher,
    context: TenantContext,
    payload: GenerateContentRequest,
    content_id: str,
    content_hash: str,
    idempotency_key: str,
    occurred_at: datetime,
) -> None:
    from libs.shared.events import EventEnvelope

    await publisher.publish(
        EventEnvelope(
            event_id=f"evt-content-{uuid4()}",
            type=CONTENT_GENERATED_EVENT,
            schema_version=CGLR_EVENT_SCHEMA_VERSION,
            tenant_id=context.tenant_id,
            source=CGLR_EVENT_SOURCE,
            correlation_id=_correlation_id(context),
            occurred_at=occurred_at,
            causation_id=idempotency_key,
            payload={
                "content_id": content_id,
                "template_id": payload.template_id,
                "content_hash": content_hash,
                "platform_targets": list(payload.platform_targets),
            },
        )
    )


def _contribution_metadata(
    *,
    payload: GenerateContentRequest,
    content_id: str,
    content_hash: str,
) -> dict[str, JSONValue]:
    return {
        "source_type": CONTRIBUTION_SOURCE_TYPE,
        "content_id": content_id,
        "template_id": payload.template_id,
        "content_hash": content_hash,
        "platform_targets": list(payload.platform_targets),
        "metadata": payload.contribution.metadata,
    }


def _correlation_id(context: TenantContext) -> str:
    return context.correlation_id or f"corr-{uuid4()}"


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _is_private_context_key(name: str) -> bool:
    return name.startswith("_") or "__" in name


def _hash_json_payload(payload: Mapping[str, JSONValue]) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


async def _tenant_core_error_handler(
    _request: Request,
    exc: Exception,
) -> JSONResponse:
    error = cast(TenantCoreError, exc)
    return JSONResponse(
        status_code=error.status_code,
        content=error.to_response_body(),
    )


async def _shared_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    error = cast(SharedError, exc)
    return JSONResponse(
        status_code=error.status_code,
        content=error.to_response_body(),
    )


async def _validation_error_handler(
    _request: Request,
    exc: Exception,
) -> JSONResponse:
    validation_error = cast(RequestValidationError, exc)
    return JSONResponse(
        status_code=400,
        content=jsonable_encoder(
            error_response_body(
                code=VALIDATION_ERROR_CODE,
                message="Запрос не прошёл валидацию",
                details={"errors": jsonable_encoder(validation_error.errors())},
            )
        ),
    )


async def _domain_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    error = cast(TemplateEngineError | LinkRotationError, exc)
    return JSONResponse(
        status_code=400,
        content=error_response_body(
            code=VALIDATION_ERROR_CODE,
            message="Запрос не прошёл доменную валидацию",
            details={"reason": str(error)},
        ),
    )
