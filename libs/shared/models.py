from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator

if TYPE_CHECKING:
    from libs.shared.tenant import TenantContext

type JSONValue = (
    None | bool | int | float | str | list["JSONValue"] | dict[str, "JSONValue"]
)

TENANT_ID_PATTERN_TEXT = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$"
TOKEN_PATTERN_TEXT = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$"
EVENT_TYPE_PATTERN_TEXT = r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$"
AUDIT_HASH_PATTERN_TEXT = r"^[0-9a-f]{64}$"

TenantId = Annotated[
    str,
    Field(
        min_length=1,
        max_length=64,
        pattern=TENANT_ID_PATTERN_TEXT,
    ),
]
CorrelationId = Annotated[
    str,
    Field(
        min_length=1,
        max_length=128,
        pattern=TOKEN_PATTERN_TEXT,
    ),
]
SubjectId = Annotated[
    str,
    Field(
        min_length=1,
        max_length=128,
        pattern=TOKEN_PATTERN_TEXT,
    ),
]
RoleName = Annotated[
    str,
    Field(
        min_length=1,
        max_length=128,
        pattern=TOKEN_PATTERN_TEXT,
    ),
]
EventType = Annotated[
    str,
    Field(
        min_length=3,
        max_length=128,
        pattern=EVENT_TYPE_PATTERN_TEXT,
    ),
]
AuditHash = Annotated[
    str,
    Field(
        min_length=64,
        max_length=64,
        pattern=AUDIT_HASH_PATTERN_TEXT,
    ),
]
IdempotencyKey = Annotated[
    str,
    Field(
        min_length=1,
        max_length=128,
        pattern=TOKEN_PATTERN_TEXT,
    ),
]


class SharedBaseModel(BaseModel):
    """Base Pydantic v2 model for stable cross-service contracts."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


class TenantScopedModel(SharedBaseModel):
    """Minimal model for payloads that must be scoped to one tenant."""

    tenant_id: TenantId
    correlation_id: CorrelationId | None = None

    def tenant_filter(self) -> dict[str, str]:
        return {"tenant_id": self.tenant_id}


class RequestContextModel(TenantScopedModel):
    """Serializable representation of the verified request tenant context."""

    subject: SubjectId | None = None
    roles: tuple[RoleName, ...] = Field(default_factory=tuple)

    @field_validator("roles", mode="before")
    @classmethod
    def _normalize_roles(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            return (value,)
        if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
            roles = tuple(role for role in value if isinstance(role, str))
            if len(roles) == len(value):
                return roles

        raise ValueError("roles должен быть строкой или списком строк")

    @classmethod
    def from_tenant_context(cls, context: TenantContext) -> Self:
        return cls(
            tenant_id=context.tenant_id,
            subject=context.subject,
            roles=context.roles,
            correlation_id=context.correlation_id,
        )

    @classmethod
    def from_current_tenant_context(cls) -> Self:
        from libs.shared.tenant import require_tenant_context

        return cls.from_tenant_context(require_tenant_context())

    def to_tenant_context(self) -> TenantContext:
        from libs.shared.tenant import TenantContext

        return TenantContext(
            tenant_id=self.tenant_id,
            subject=self.subject,
            roles=self.roles,
            correlation_id=self.correlation_id,
        )


class AuditHashReference(TenantScopedModel):
    """Reference to a canonical audit hash owned by a tenant."""

    event_id: IdempotencyKey
    event_type: EventType
    audit_hash: AuditHash


class PaginationParams(SharedBaseModel):
    limit: int = Field(default=50, ge=1, le=500)
    offset: int = Field(default=0, ge=0)


class PageMeta(SharedBaseModel):
    limit: int = Field(ge=1, le=500)
    offset: int = Field(ge=0)
    total: int = Field(ge=0)


class PagedResponse[ModelT](SharedBaseModel):
    items: tuple[ModelT, ...]
    meta: PageMeta
