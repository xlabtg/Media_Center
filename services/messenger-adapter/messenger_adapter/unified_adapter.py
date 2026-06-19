from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Self

from pydantic import Field, field_validator

from libs.shared.models import (
    AuditHash,
    CorrelationId,
    IdempotencyKey,
    JSONValue,
    SharedBaseModel,
    TenantId,
)
from messenger_adapter.base_adapter import (
    BasePlatformAdapter,
    PlatformName,
    PlatformPublicationError,
    PublicationReceipt,
    PublicationRequest,
    TargetId,
)
from messenger_adapter.platform_registry import (
    PlatformNotRegisteredError,
    PlatformRegistry,
    PlatformRegistryEntry,
    PlatformStatus,
)


class PublicationBatchRequest(SharedBaseModel):
    tenant_id: TenantId
    publication_id: IdempotencyKey
    content: str = Field(min_length=1, max_length=100_000)
    correlation_id: CorrelationId
    platforms: tuple[PlatformName, ...] | None = None
    target_ids: dict[str, TargetId] = Field(default_factory=dict)
    metadata: dict[str, JSONValue] = Field(default_factory=dict)

    @field_validator("platforms", mode="before")
    @classmethod
    def _normalize_platforms(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str):
            return (_normalize_platform(value),)
        if isinstance(value, Sequence) and not isinstance(
            value,
            str | bytes | bytearray,
        ):
            platforms: list[str] = []
            seen: set[str] = set()
            for item in value:
                if not isinstance(item, str):
                    return value
                platform = _normalize_platform(item)
                if platform not in seen:
                    platforms.append(platform)
                    seen.add(platform)
            return tuple(platforms)
        return value

    @field_validator("target_ids", mode="before")
    @classmethod
    def _normalize_target_ids(cls, value: object) -> object:
        if value is None:
            return {}
        if not isinstance(value, dict):
            return value

        normalized: dict[str, object] = {}
        for platform, target_id in value.items():
            if not isinstance(platform, str):
                return value
            normalized[_normalize_platform(platform)] = target_id
        return normalized


class PublicationBatchFailure(SharedBaseModel):
    platform: PlatformName
    target_id: TargetId | None = None
    error_code: str = Field(min_length=1, max_length=128)
    retryable: bool
    attempt_count: int = Field(ge=0)
    audit_hash: AuditHash | None = None


class PublicationBatchResult(SharedBaseModel):
    tenant_id: TenantId
    publication_id: IdempotencyKey
    correlation_id: CorrelationId
    receipts: tuple[PublicationReceipt, ...] = Field(default_factory=tuple)
    failed: tuple[PublicationBatchFailure, ...] = Field(default_factory=tuple)

    @property
    def succeeded_platforms(self) -> tuple[str, ...]:
        return tuple(receipt.platform for receipt in self.receipts)

    @property
    def failed_platforms(self) -> tuple[str, ...]:
        return tuple(failure.platform for failure in self.failed)


class UnifiedMessengerAdapterError(ValueError):
    """Raised when a unified publication request cannot be prepared."""


@dataclass(slots=True)
class UnifiedMessengerAdapter:
    platform_adapters: Mapping[str, BasePlatformAdapter]
    platform_registry: PlatformRegistry | None = None
    _platform_adapters: dict[str, BasePlatformAdapter] = field(
        default_factory=dict,
        init=False,
    )

    def __post_init__(self) -> None:
        self._platform_adapters = {
            _normalize_platform(platform): adapter
            for platform, adapter in self.platform_adapters.items()
        }
        if not self._platform_adapters:
            raise UnifiedMessengerAdapterError(
                "Нужно зарегистрировать хотя бы один площадочный адаптер"
            )

    async def publish(
        self,
        request: PublicationBatchRequest,
        *,
        stop_on_error: bool = False,
    ) -> PublicationBatchResult:
        receipts: list[PublicationReceipt] = []
        failures: list[PublicationBatchFailure] = []

        for platform in self._publication_platforms(request):
            target_id: str | None = None
            try:
                adapter = self._require_adapter(platform)
                registry_entry = self._registry_entry(
                    tenant_id=request.tenant_id,
                    platform=platform,
                )
                target_id = self._target_id_for(
                    request=request,
                    platform=platform,
                    registry_entry=registry_entry,
                )
                receipt = await adapter.publish(
                    PublicationRequest(
                        tenant_id=request.tenant_id,
                        platform=platform,
                        publication_id=request.publication_id,
                        target_id=target_id,
                        content=request.content,
                        correlation_id=request.correlation_id,
                        metadata=request.metadata,
                    )
                )
            except Exception as error:
                publication_error = _publication_error_from(
                    error,
                    platform=platform,
                )
                failure = PublicationBatchFailure(
                    platform=platform,
                    target_id=target_id,
                    error_code=publication_error.error_code,
                    retryable=publication_error.retryable,
                    attempt_count=publication_error.attempt_count,
                    audit_hash=publication_error.audit_hash,
                )
                failures.append(failure)
                if stop_on_error:
                    raise publication_error from error
                continue

            receipts.append(receipt)

        if not receipts and not failures:
            raise UnifiedMessengerAdapterError(
                "Нет площадок для публикации в unified request"
            )

        return PublicationBatchResult(
            tenant_id=request.tenant_id,
            publication_id=request.publication_id,
            correlation_id=request.correlation_id,
            receipts=tuple(receipts),
            failed=tuple(failures),
        )

    def with_adapter(self, platform: str, adapter: BasePlatformAdapter) -> Self:
        self._platform_adapters[_normalize_platform(platform)] = adapter
        return self

    def _publication_platforms(
        self,
        request: PublicationBatchRequest,
    ) -> tuple[str, ...]:
        if request.platforms is not None:
            return request.platforms

        if self.platform_registry is None:
            return tuple(self._platform_adapters)

        return tuple(
            entry.platform
            for entry in self.platform_registry.list_platforms(
                tenant_id=request.tenant_id,
            )
            if entry.status == PlatformStatus.ACTIVE
        )

    def _require_adapter(self, platform: str) -> BasePlatformAdapter:
        normalized_platform = _normalize_platform(platform)
        adapter = self._platform_adapters.get(normalized_platform)
        if adapter is None:
            raise PlatformPublicationError(
                "Для площадки не зарегистрирован publisher adapter",
                platform=normalized_platform,
                error_code="platform_adapter_not_registered",
                retryable=False,
            )

        return adapter

    def _registry_entry(
        self,
        *,
        tenant_id: str,
        platform: str,
    ) -> PlatformRegistryEntry | None:
        if self.platform_registry is None:
            return None

        try:
            return self.platform_registry.require_platform(
                tenant_id=tenant_id,
                platform=platform,
            )
        except PlatformNotRegisteredError as error:
            raise PlatformPublicationError(
                "Площадка не зарегистрирована в реестре tenant",
                platform=platform,
                error_code="platform_not_registered",
                retryable=False,
            ) from error

    def _target_id_for(
        self,
        *,
        request: PublicationBatchRequest,
        platform: str,
        registry_entry: PlatformRegistryEntry | None,
    ) -> str:
        normalized_platform = _normalize_platform(platform)
        target_id = request.target_ids.get(normalized_platform)
        if target_id is None and registry_entry is not None:
            target_id = _default_target_id(registry_entry.parameters)
        if target_id is None:
            raise PlatformPublicationError(
                "Для площадки не указан target_id",
                platform=normalized_platform,
                error_code="target_id_missing",
                retryable=False,
            )

        return target_id


def _default_target_id(parameters: Mapping[str, JSONValue]) -> str | None:
    value = parameters.get("default_target_id")
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float | str):
        normalized = str(value).strip()
        if normalized != "":
            return normalized
    return None


def _publication_error_from(
    error: Exception,
    *,
    platform: str,
) -> PlatformPublicationError:
    if isinstance(error, PlatformPublicationError):
        return error

    return PlatformPublicationError(
        "Сбой unified публикации на площадку",
        platform=platform,
        error_code="publication_failed",
        retryable=True,
    )


def _normalize_platform(platform: str) -> str:
    normalized = platform.strip().lower()
    if normalized == "":
        raise UnifiedMessengerAdapterError("platform не может быть пустой")
    return normalized


__all__ = [
    "PublicationBatchFailure",
    "PublicationBatchRequest",
    "PublicationBatchResult",
    "UnifiedMessengerAdapter",
    "UnifiedMessengerAdapterError",
]
