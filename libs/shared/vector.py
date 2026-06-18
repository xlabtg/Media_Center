from __future__ import annotations

import asyncio
import hashlib
import math
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from importlib import import_module
from numbers import Real
from typing import Any, Protocol, cast

from libs.shared.tenant import (
    AuditSink,
    TenantContext,
    TenantIsolationError,
    assert_requested_tenant,
    require_tenant_context,
)

CHROMA_HOST_ENV = "CHROMA_HOST"
CHROMA_PORT_ENV = "CHROMA_PORT"
CHROMA_SSL_ENV = "CHROMA_SSL"
DEFAULT_CHROMA_COLLECTION_PREFIX = "nmc"
DEFAULT_CHROMA_ENVIRONMENT = "dev"
DEFAULT_CHROMA_PORT = 8001

type MetadataScalar = bool | int | float | str
type VectorMetadata = dict[str, MetadataScalar]

_COLLECTION_UNSAFE_PATTERN = re.compile(r"[^A-Za-z0-9_-]+")


class TenantVectorStore(Protocol):
    async def upsert(
        self,
        domain: str,
        records: Sequence[VectorRecord],
        *,
        context: TenantContext | None = None,
        audit_sink: AuditSink | None = None,
    ) -> None:
        """Persist tenant-scoped vectors in the selected domain collection."""

    async def query(
        self,
        domain: str,
        query_embedding: Sequence[float],
        *,
        context: TenantContext | None = None,
        limit: int = 10,
        metadata_filter: Mapping[str, MetadataScalar] | None = None,
        audit_sink: AuditSink | None = None,
    ) -> list[VectorSearchResult]:
        """Return nearest tenant-scoped vector records for the query embedding."""


@dataclass(frozen=True, slots=True)
class ChromaSettings:
    host: str
    port: int = DEFAULT_CHROMA_PORT
    ssl: bool = False
    collection_prefix: str = DEFAULT_CHROMA_COLLECTION_PREFIX
    environment: str = DEFAULT_CHROMA_ENVIRONMENT

    def __post_init__(self) -> None:
        object.__setattr__(self, "host", validate_chroma_host(self.host))
        object.__setattr__(self, "port", validate_chroma_port(self.port))
        object.__setattr__(
            self,
            "collection_prefix",
            _collection_segment(self.collection_prefix, "collection_prefix"),
        )
        object.__setattr__(
            self,
            "environment",
            _collection_segment(self.environment, "environment"),
        )

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        host_env: str = CHROMA_HOST_ENV,
        port_env: str = CHROMA_PORT_ENV,
        ssl_env: str = CHROMA_SSL_ENV,
        collection_prefix: str = DEFAULT_CHROMA_COLLECTION_PREFIX,
        environment: str = DEFAULT_CHROMA_ENVIRONMENT,
    ) -> ChromaSettings:
        return cls(
            host=chroma_host_from_env(environ, env_var=host_env),
            port=chroma_port_from_env(environ, env_var=port_env),
            ssl=chroma_ssl_from_env(environ, env_var=ssl_env),
            collection_prefix=collection_prefix,
            environment=environment,
        )


@dataclass(frozen=True, slots=True)
class VectorRecord:
    id: str
    embedding: Sequence[float]
    document: str | None = None
    metadata: Mapping[str, MetadataScalar] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _normalize_vector_id(self.id))
        object.__setattr__(
            self,
            "embedding",
            _normalize_embedding(self.embedding, "embedding"),
        )
        if self.document is not None and self.document.strip() == "":
            raise ValueError("document должен быть непустой строкой")
        object.__setattr__(
            self,
            "metadata",
            _normalize_metadata(self.metadata, "metadata"),
        )


@dataclass(frozen=True, slots=True)
class VectorSearchResult:
    id: str
    distance: float
    metadata: Mapping[str, MetadataScalar]
    document: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _normalize_vector_id(self.id))
        if not math.isfinite(self.distance) or self.distance < 0:
            raise ValueError("distance должен быть неотрицательным числом")
        object.__setattr__(
            self,
            "metadata",
            _normalize_metadata(self.metadata, "metadata"),
        )


class InMemoryTenantVectorStore:
    """Deterministic tenant vector store for unit tests and local service wiring."""

    def __init__(
        self,
        *,
        collection_prefix: str = DEFAULT_CHROMA_COLLECTION_PREFIX,
        environment: str = DEFAULT_CHROMA_ENVIRONMENT,
    ) -> None:
        self._collection_prefix = _collection_segment(
            collection_prefix,
            "collection_prefix",
        )
        self._environment = _collection_segment(environment, "environment")
        self._collections: dict[str, dict[str, VectorRecord]] = {}

    async def upsert(
        self,
        domain: str,
        records: Sequence[VectorRecord],
        *,
        context: TenantContext | None = None,
        audit_sink: AuditSink | None = None,
    ) -> None:
        if not records:
            raise ValueError("records должен содержать хотя бы одну запись")

        resolved_context = _resolve_context(context)
        normalized_domain = _normalize_domain(domain)
        collection_name = build_tenant_vector_collection_name(
            normalized_domain,
            context=resolved_context,
            collection_prefix=self._collection_prefix,
            environment=self._environment,
        )
        collection = self._collections.setdefault(collection_name, {})

        for record in records:
            enriched_metadata = _tenant_metadata_for_record(
                record.metadata,
                domain=normalized_domain,
                context=resolved_context,
                audit_sink=audit_sink,
            )
            collection[record.id] = VectorRecord(
                id=record.id,
                embedding=record.embedding,
                document=record.document,
                metadata=enriched_metadata,
            )

    async def query(
        self,
        domain: str,
        query_embedding: Sequence[float],
        *,
        context: TenantContext | None = None,
        limit: int = 10,
        metadata_filter: Mapping[str, MetadataScalar] | None = None,
        audit_sink: AuditSink | None = None,
    ) -> list[VectorSearchResult]:
        if limit <= 0:
            raise ValueError("limit должен быть положительным")

        resolved_context = _resolve_context(context)
        normalized_domain = _normalize_domain(domain)
        normalized_embedding = _normalize_embedding(
            query_embedding,
            "query_embedding",
        )
        collection_name = build_tenant_vector_collection_name(
            normalized_domain,
            context=resolved_context,
            collection_prefix=self._collection_prefix,
            environment=self._environment,
        )
        metadata_where = _tenant_metadata_filter(
            metadata_filter,
            context=resolved_context,
            audit_sink=audit_sink,
        )

        results: list[VectorSearchResult] = []
        for record in self._collections.get(collection_name, {}).values():
            if not _metadata_matches(record.metadata, metadata_where):
                continue
            distance = _squared_l2_distance(normalized_embedding, record.embedding)
            results.append(
                VectorSearchResult(
                    id=record.id,
                    distance=distance,
                    document=record.document,
                    metadata=record.metadata,
                )
            )

        return sorted(results, key=lambda result: (result.distance, result.id))[:limit]


@dataclass(slots=True)
class ChromaTenantVectorStore:
    """ChromaDB-backed implementation of the shared tenant vector contract."""

    client: Any
    settings: ChromaSettings

    @classmethod
    def from_settings(cls, settings: ChromaSettings) -> ChromaTenantVectorStore:
        chromadb = cast(Any, import_module("chromadb"))
        client = chromadb.HttpClient(
            host=settings.host,
            port=settings.port,
            ssl=settings.ssl,
        )

        return cls(client=client, settings=settings)

    async def upsert(
        self,
        domain: str,
        records: Sequence[VectorRecord],
        *,
        context: TenantContext | None = None,
        audit_sink: AuditSink | None = None,
    ) -> None:
        if not records:
            raise ValueError("records должен содержать хотя бы одну запись")

        resolved_context = _resolve_context(context)
        normalized_domain = _normalize_domain(domain)
        collection = await self._collection_for_tenant(
            normalized_domain,
            resolved_context,
        )
        ids: list[str] = []
        embeddings: list[list[float]] = []
        metadatas: list[VectorMetadata] = []
        documents: list[str | None] = []

        for record in records:
            ids.append(record.id)
            embeddings.append(list(record.embedding))
            metadatas.append(
                _tenant_metadata_for_record(
                    record.metadata,
                    domain=normalized_domain,
                    context=resolved_context,
                    audit_sink=audit_sink,
                )
            )
            documents.append(record.document)

        normalized_documents = [
            document if document is not None else "" for document in documents
        ]
        await asyncio.to_thread(
            collection.upsert,
            ids=ids,
            embeddings=embeddings,
            metadatas=metadatas,
            documents=normalized_documents,
        )

    async def query(
        self,
        domain: str,
        query_embedding: Sequence[float],
        *,
        context: TenantContext | None = None,
        limit: int = 10,
        metadata_filter: Mapping[str, MetadataScalar] | None = None,
        audit_sink: AuditSink | None = None,
    ) -> list[VectorSearchResult]:
        if limit <= 0:
            raise ValueError("limit должен быть положительным")

        resolved_context = _resolve_context(context)
        normalized_domain = _normalize_domain(domain)
        normalized_embedding = _normalize_embedding(
            query_embedding,
            "query_embedding",
        )
        collection = await self._collection_for_tenant(
            normalized_domain,
            resolved_context,
        )
        metadata_where = _tenant_metadata_filter(
            metadata_filter,
            context=resolved_context,
            audit_sink=audit_sink,
        )
        raw_result = await asyncio.to_thread(
            collection.query,
            query_embeddings=[list(normalized_embedding)],
            n_results=limit,
            where=metadata_where,
        )

        return _search_results_from_chroma(
            raw_result,
            context=resolved_context,
            audit_sink=audit_sink,
        )

    async def _collection_for_tenant(
        self,
        domain: str,
        context: TenantContext,
    ) -> Any:
        collection_name = build_tenant_vector_collection_name(
            domain,
            context=context,
            collection_prefix=self.settings.collection_prefix,
            environment=self.settings.environment,
        )

        return await asyncio.to_thread(
            self.client.get_or_create_collection,
            name=collection_name,
            metadata={
                "tenant_id": context.tenant_id,
                "domain": domain,
                "environment": self.settings.environment,
            },
        )


def chroma_host_from_env(
    environ: Mapping[str, str] | None = None,
    *,
    env_var: str = CHROMA_HOST_ENV,
) -> str:
    source = os.environ if environ is None else environ
    host = source.get(env_var)
    if host is None or host.strip() == "":
        raise ValueError(f"{env_var} должен быть задан")

    return validate_chroma_host(host)


def chroma_port_from_env(
    environ: Mapping[str, str] | None = None,
    *,
    env_var: str = CHROMA_PORT_ENV,
) -> int:
    source = os.environ if environ is None else environ
    raw_port = source.get(env_var)
    if raw_port is None or raw_port.strip() == "":
        raise ValueError(f"{env_var} должен быть задан")

    try:
        port = int(raw_port)
    except ValueError as error:
        raise ValueError(f"{env_var} должен быть TCP-портом") from error

    try:
        return validate_chroma_port(port)
    except ValueError as error:
        raise ValueError(f"{env_var} должен быть TCP-портом от 1 до 65535") from error


def chroma_ssl_from_env(
    environ: Mapping[str, str] | None = None,
    *,
    env_var: str = CHROMA_SSL_ENV,
    default: bool = False,
) -> bool:
    source = os.environ if environ is None else environ
    raw_value = source.get(env_var)
    if raw_value is None or raw_value.strip() == "":
        return default

    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False

    raise ValueError(f"{env_var} должен быть boolean")


def validate_chroma_host(host: str) -> str:
    normalized = host.strip()
    if normalized == "":
        raise ValueError("CHROMA_HOST должен быть непустой строкой")
    if "://" in normalized or any(character.isspace() for character in normalized):
        raise ValueError("CHROMA_HOST должен быть host без схемы и пробелов")

    return normalized


def validate_chroma_port(port: int) -> int:
    if isinstance(port, bool) or port < 1 or port > 65535:
        raise ValueError("CHROMA_PORT должен быть TCP-портом от 1 до 65535")

    return port


def build_tenant_vector_collection_name(
    domain: str,
    *,
    context: TenantContext | None = None,
    collection_prefix: str = DEFAULT_CHROMA_COLLECTION_PREFIX,
    environment: str = DEFAULT_CHROMA_ENVIRONMENT,
) -> str:
    resolved_context = _resolve_context(context)
    normalized_domain = _normalize_domain(domain)
    parts = (
        _collection_segment(collection_prefix, "collection_prefix"),
        _collection_segment(environment, "environment"),
        _collection_segment(resolved_context.tenant_id, "tenant_id"),
        _collection_segment(normalized_domain, "domain"),
    )

    return "_".join(parts)


def _tenant_metadata_for_record(
    metadata: Mapping[str, MetadataScalar],
    *,
    domain: str,
    context: TenantContext,
    audit_sink: AuditSink | None,
) -> VectorMetadata:
    normalized_metadata = _normalize_metadata(metadata, "metadata")
    requested_tenant_id = normalized_metadata.get("tenant_id")
    if requested_tenant_id is not None:
        if not isinstance(requested_tenant_id, str):
            raise TenantIsolationError(
                "tenant_id в vector metadata имеет недопустимый формат",
                correlation_id=context.correlation_id,
            )
        assert_requested_tenant(
            requested_tenant_id,
            context=context,
            audit_sink=audit_sink,
            resource_type="vector_metadata",
        )

    normalized_metadata["tenant_id"] = context.tenant_id
    normalized_metadata["domain"] = domain
    return normalized_metadata


def _tenant_metadata_filter(
    metadata_filter: Mapping[str, MetadataScalar] | None,
    *,
    context: TenantContext,
    audit_sink: AuditSink | None,
) -> VectorMetadata:
    normalized_filter = _normalize_metadata(metadata_filter or {}, "metadata_filter")
    requested_tenant_id = normalized_filter.get("tenant_id")
    if requested_tenant_id is not None:
        if not isinstance(requested_tenant_id, str):
            raise TenantIsolationError(
                "tenant_id в vector filter имеет недопустимый формат",
                correlation_id=context.correlation_id,
            )
        assert_requested_tenant(
            requested_tenant_id,
            context=context,
            audit_sink=audit_sink,
            resource_type="vector_filter",
        )

    normalized_filter["tenant_id"] = context.tenant_id
    return normalized_filter


def _search_results_from_chroma(
    raw_result: Any,
    *,
    context: TenantContext,
    audit_sink: AuditSink | None,
) -> list[VectorSearchResult]:
    ids = _first_chroma_row(raw_result, "ids")
    distances = _first_chroma_row(raw_result, "distances")
    documents = _first_chroma_row(raw_result, "documents")
    metadatas = _first_chroma_row(raw_result, "metadatas")
    results: list[VectorSearchResult] = []

    for index, raw_id in enumerate(ids):
        metadata = _metadata_at(metadatas, index)
        requested_tenant_id = metadata.get("tenant_id")
        if not isinstance(requested_tenant_id, str):
            raise TenantIsolationError(
                "ChromaDB вернула vector metadata без tenant_id",
                correlation_id=context.correlation_id,
            )
        assert_requested_tenant(
            requested_tenant_id,
            context=context,
            audit_sink=audit_sink,
            resource_type="vector_result",
        )

        results.append(
            VectorSearchResult(
                id=str(raw_id),
                distance=_distance_at(distances, index),
                document=_document_at(documents, index),
                metadata=metadata,
            )
        )

    return results


def _first_chroma_row(raw_result: Any, key: str) -> list[Any]:
    if not isinstance(raw_result, Mapping):
        return []

    value = raw_result.get(key)
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return []
    if len(value) == 0:
        return []

    first_row = value[0]
    if not isinstance(first_row, Sequence) or isinstance(first_row, str | bytes):
        return []

    return list(first_row)


def _metadata_at(rows: Sequence[Any], index: int) -> VectorMetadata:
    if index >= len(rows):
        return {}

    row = rows[index]
    if row is None:
        return {}
    if not isinstance(row, Mapping):
        raise ValueError("ChromaDB metadatas должен содержать JSON object")

    return _normalize_metadata(cast(Mapping[str, MetadataScalar], row), "metadata")


def _distance_at(rows: Sequence[Any], index: int) -> float:
    if index >= len(rows):
        return 0.0

    value = rows[index]
    if not isinstance(value, Real) or isinstance(value, bool):
        raise ValueError("ChromaDB distances должен содержать числа")

    return float(value)


def _document_at(rows: Sequence[Any], index: int) -> str | None:
    if index >= len(rows):
        return None

    value = rows[index]
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("ChromaDB documents должен содержать строки")

    return value


def _resolve_context(context: TenantContext | None) -> TenantContext:
    if context is not None:
        return context

    return require_tenant_context()


def _normalize_vector_id(value: str) -> str:
    normalized = value.strip()
    if normalized == "":
        raise ValueError("id должен быть непустой строкой")
    if any(character.isspace() for character in normalized):
        raise ValueError("id не должен содержать пробелы")

    return normalized


def _normalize_domain(domain: str) -> str:
    normalized = domain.strip()
    if normalized == "":
        raise ValueError("domain должен быть непустой строкой")
    if any(character.isspace() for character in normalized):
        raise ValueError("domain не должен содержать пробелы")

    return normalized


def _normalize_embedding(
    embedding: Sequence[float],
    label: str,
) -> tuple[float, ...]:
    if isinstance(embedding, str | bytes) or not embedding:
        raise ValueError(f"{label} должен быть непустым числовым вектором")

    values: list[float] = []
    for value in embedding:
        if not isinstance(value, Real) or isinstance(value, bool):
            raise ValueError(f"{label} должен содержать только числа")
        normalized_value = float(value)
        if not math.isfinite(normalized_value):
            raise ValueError(f"{label} должен содержать только конечные числа")
        values.append(normalized_value)

    return tuple(values)


def _normalize_metadata(
    metadata: Mapping[str, MetadataScalar],
    label: str,
) -> VectorMetadata:
    normalized_metadata: VectorMetadata = {}
    for key, value in metadata.items():
        normalized_key = _normalize_metadata_key(key, label)
        if not isinstance(value, bool | int | float | str):
            raise ValueError(f"{label}.{normalized_key} должен быть scalar value")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"{label}.{normalized_key} должен быть конечным числом")
        normalized_metadata[normalized_key] = value

    return normalized_metadata


def _normalize_metadata_key(key: str, label: str) -> str:
    normalized = key.strip()
    if normalized == "":
        raise ValueError(f"{label} не должен содержать пустой ключ")
    if any(character.isspace() for character in normalized):
        raise ValueError(f"{label}.{normalized} не должен содержать пробелы")

    return normalized


def _collection_segment(value: str, label: str) -> str:
    normalized = value.strip()
    if normalized == "":
        raise ValueError(f"{label} должен быть непустой строкой")
    if any(character.isspace() for character in normalized):
        raise ValueError(f"{label} не должен содержать пробелы")

    safe = _COLLECTION_UNSAFE_PATTERN.sub("_", normalized).strip("_")
    if safe == "":
        raise ValueError(f"{label} должен содержать символы для имени коллекции")
    if safe != normalized:
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]
        safe = f"{safe[:80]}_{digest}"

    return safe[:96]


def _metadata_matches(
    metadata: Mapping[str, MetadataScalar],
    expected: Mapping[str, MetadataScalar],
) -> bool:
    return all(metadata.get(key) == value for key, value in expected.items())


def _squared_l2_distance(
    left: Sequence[float],
    right: Sequence[float],
) -> float:
    if len(left) != len(right):
        raise ValueError("Размерность vector embedding не совпадает с query")

    return sum(
        (left_value - right_value) ** 2
        for left_value, right_value in zip(left, right, strict=True)
    )
