from __future__ import annotations

import asyncio
import hashlib
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from importlib import import_module
from typing import Any, Protocol, cast
from urllib.parse import urlparse

from libs.shared.tenant import (
    AuditSink,
    TenantContext,
    TenantIsolationError,
    assert_requested_tenant,
    require_tenant_context,
)

S3_ENDPOINT_URL_ENV = "S3_ENDPOINT_URL"
S3_ACCESS_KEY_ENV = "S3_ACCESS_KEY"
S3_SECRET_KEY_ENV = "S3_SECRET_KEY"
S3_BUCKET_ENV = "S3_BUCKET"
S3_REGION_ENV = "S3_REGION"
DEFAULT_S3_REGION = "ru-central-1"
DEFAULT_S3_TENANT_PREFIX = "tenants"
DEFAULT_PRESIGNED_EXPIRES_SECONDS = 900

type ObjectMetadata = dict[str, str]

_BUCKET_PATTERN = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
_SAFE_SEGMENT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_METADATA_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class TenantObjectStorage(Protocol):
    async def put_object(
        self,
        domain: str,
        object_id: str,
        body: bytes | bytearray | memoryview,
        *,
        content_type: str = "application/octet-stream",
        metadata: Mapping[str, str] | None = None,
        context: TenantContext | None = None,
        audit_sink: AuditSink | None = None,
    ) -> TenantObjectReference:
        """Persist one tenant-scoped object."""

    async def get_object(
        self,
        domain: str,
        object_id: str,
        *,
        context: TenantContext | None = None,
        audit_sink: AuditSink | None = None,
    ) -> TenantObject | None:
        """Read one tenant-scoped object or return None when it is absent."""

    async def list_objects(
        self,
        domain: str,
        *,
        prefix: str = "",
        context: TenantContext | None = None,
    ) -> list[TenantObjectReference]:
        """List objects under one tenant/domain prefix."""

    async def delete_object(
        self,
        domain: str,
        object_id: str,
        *,
        context: TenantContext | None = None,
    ) -> bool:
        """Delete one tenant-scoped object."""

    async def create_presigned_get_url(
        self,
        domain: str,
        object_id: str,
        *,
        context: TenantContext | None = None,
        expires_in_seconds: int = DEFAULT_PRESIGNED_EXPIRES_SECONDS,
    ) -> str:
        """Create a time-limited GET URL for one tenant-scoped object."""

    async def create_presigned_put_url(
        self,
        domain: str,
        object_id: str,
        *,
        content_hash: str,
        content_type: str = "application/octet-stream",
        metadata: Mapping[str, str] | None = None,
        context: TenantContext | None = None,
        audit_sink: AuditSink | None = None,
        expires_in_seconds: int = DEFAULT_PRESIGNED_EXPIRES_SECONDS,
    ) -> str:
        """Create a time-limited PUT URL for one tenant-scoped object."""


@dataclass(frozen=True, slots=True)
class S3Settings:
    endpoint_url: str
    access_key: str
    secret_key: str
    bucket: str
    region: str = DEFAULT_S3_REGION
    tenant_prefix: str = DEFAULT_S3_TENANT_PREFIX

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "endpoint_url",
            validate_s3_endpoint_url(self.endpoint_url),
        )
        object.__setattr__(
            self,
            "access_key",
            _required_string(self.access_key, "S3_ACCESS_KEY"),
        )
        object.__setattr__(
            self,
            "secret_key",
            _required_string(self.secret_key, "S3_SECRET_KEY"),
        )
        object.__setattr__(self, "bucket", validate_s3_bucket_name(self.bucket))
        object.__setattr__(self, "region", _safe_segment(self.region, "region"))
        object.__setattr__(
            self,
            "tenant_prefix",
            _safe_segment(self.tenant_prefix, "tenant_prefix"),
        )

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        endpoint_url_env: str = S3_ENDPOINT_URL_ENV,
        access_key_env: str = S3_ACCESS_KEY_ENV,
        secret_key_env: str = S3_SECRET_KEY_ENV,
        bucket_env: str = S3_BUCKET_ENV,
        region_env: str = S3_REGION_ENV,
        tenant_prefix: str = DEFAULT_S3_TENANT_PREFIX,
    ) -> S3Settings:
        source = os.environ if environ is None else environ
        return cls(
            endpoint_url=s3_endpoint_url_from_env(source, env_var=endpoint_url_env),
            access_key=_env_value(source, access_key_env),
            secret_key=_env_value(source, secret_key_env),
            bucket=_env_value(source, bucket_env),
            region=source.get(region_env, DEFAULT_S3_REGION),
            tenant_prefix=tenant_prefix,
        )


@dataclass(frozen=True, slots=True)
class TenantObjectReference:
    bucket: str
    key: str
    domain: str
    object_id: str
    size: int | None = None
    content_type: str | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)
    etag: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "bucket", validate_s3_bucket_name(self.bucket))
        object.__setattr__(self, "key", _normalize_key(self.key, "key"))
        object.__setattr__(self, "domain", _safe_segment(self.domain, "domain"))
        object.__setattr__(
            self,
            "object_id",
            _normalize_object_id(self.object_id),
        )
        if self.size is not None and self.size < 0:
            raise ValueError("size должен быть неотрицательным")
        if self.content_type is not None and self.content_type.strip() == "":
            raise ValueError("content_type должен быть непустой строкой")
        object.__setattr__(
            self,
            "metadata",
            _normalize_metadata(self.metadata),
        )


@dataclass(frozen=True, slots=True)
class TenantObject:
    reference: TenantObjectReference
    body: bytes

    def __post_init__(self) -> None:
        object.__setattr__(self, "body", bytes(self.body))


@dataclass(slots=True)
class _StoredObject:
    body: bytes
    content_type: str
    metadata: ObjectMetadata
    etag: str


class InMemoryTenantObjectStorage:
    """Deterministic tenant object storage for unit tests and local wiring."""

    def __init__(
        self,
        *,
        bucket: str,
        tenant_prefix: str = DEFAULT_S3_TENANT_PREFIX,
    ) -> None:
        self._bucket = validate_s3_bucket_name(bucket)
        self._tenant_prefix = _safe_segment(tenant_prefix, "tenant_prefix")
        self._objects: dict[str, _StoredObject] = {}

    async def put_object(
        self,
        domain: str,
        object_id: str,
        body: bytes | bytearray | memoryview,
        *,
        content_type: str = "application/octet-stream",
        metadata: Mapping[str, str] | None = None,
        context: TenantContext | None = None,
        audit_sink: AuditSink | None = None,
    ) -> TenantObjectReference:
        resolved_context = _resolve_context(context)
        normalized_domain = _safe_segment(domain, "domain")
        normalized_object_id = _normalize_object_id(object_id)
        normalized_body = bytes(body)
        normalized_content_type = _content_type(content_type)
        key = build_tenant_object_key(
            normalized_domain,
            normalized_object_id,
            context=resolved_context,
            tenant_prefix=self._tenant_prefix,
        )
        object_metadata = _tenant_metadata_for_object(
            metadata,
            body=normalized_body,
            domain=normalized_domain,
            context=resolved_context,
            audit_sink=audit_sink,
        )
        etag = hashlib.md5(normalized_body, usedforsecurity=False).hexdigest()
        self._objects[key] = _StoredObject(
            body=normalized_body,
            content_type=normalized_content_type,
            metadata=object_metadata,
            etag=etag,
        )

        return self._reference_from_stored(
            key,
            normalized_domain,
            normalized_object_id,
            self._objects[key],
            context=resolved_context,
            audit_sink=audit_sink,
        )

    async def get_object(
        self,
        domain: str,
        object_id: str,
        *,
        context: TenantContext | None = None,
        audit_sink: AuditSink | None = None,
    ) -> TenantObject | None:
        resolved_context = _resolve_context(context)
        normalized_domain = _safe_segment(domain, "domain")
        normalized_object_id = _normalize_object_id(object_id)
        key = build_tenant_object_key(
            normalized_domain,
            normalized_object_id,
            context=resolved_context,
            tenant_prefix=self._tenant_prefix,
        )
        stored = self._objects.get(key)
        if stored is None:
            return None

        return TenantObject(
            reference=self._reference_from_stored(
                key,
                normalized_domain,
                normalized_object_id,
                stored,
                context=resolved_context,
                audit_sink=audit_sink,
            ),
            body=stored.body,
        )

    async def list_objects(
        self,
        domain: str,
        *,
        prefix: str = "",
        context: TenantContext | None = None,
    ) -> list[TenantObjectReference]:
        resolved_context = _resolve_context(context)
        normalized_domain = _safe_segment(domain, "domain")
        object_prefix = _normalize_optional_object_prefix(prefix)
        key_prefix = (
            build_tenant_object_prefix(
                normalized_domain,
                context=resolved_context,
                tenant_prefix=self._tenant_prefix,
            )
            + object_prefix
        )
        references: list[TenantObjectReference] = []
        for key in sorted(self._objects):
            if not key.startswith(key_prefix):
                continue
            stored = self._objects[key]
            object_id = key.removeprefix(
                build_tenant_object_prefix(
                    normalized_domain,
                    context=resolved_context,
                    tenant_prefix=self._tenant_prefix,
                )
            )
            references.append(
                self._reference_from_stored(
                    key,
                    normalized_domain,
                    object_id,
                    stored,
                    context=resolved_context,
                    audit_sink=None,
                )
            )

        return references

    async def delete_object(
        self,
        domain: str,
        object_id: str,
        *,
        context: TenantContext | None = None,
    ) -> bool:
        key = build_tenant_object_key(
            domain,
            object_id,
            context=context,
            tenant_prefix=self._tenant_prefix,
        )
        return self._objects.pop(key, None) is not None

    async def create_presigned_get_url(
        self,
        domain: str,
        object_id: str,
        *,
        context: TenantContext | None = None,
        expires_in_seconds: int = DEFAULT_PRESIGNED_EXPIRES_SECONDS,
    ) -> str:
        expires = _positive_expiration(expires_in_seconds)
        key = build_tenant_object_key(
            domain,
            object_id,
            context=context,
            tenant_prefix=self._tenant_prefix,
        )
        return (
            f"memory://{self._bucket}/{key}?operation=get_object&expires_in={expires}"
        )

    async def create_presigned_put_url(
        self,
        domain: str,
        object_id: str,
        *,
        content_hash: str,
        content_type: str = "application/octet-stream",
        metadata: Mapping[str, str] | None = None,
        context: TenantContext | None = None,
        audit_sink: AuditSink | None = None,
        expires_in_seconds: int = DEFAULT_PRESIGNED_EXPIRES_SECONDS,
    ) -> str:
        resolved_context = _resolve_context(context)
        normalized_domain = _safe_segment(domain, "domain")
        normalized_object_id = _normalize_object_id(object_id)
        expires = _positive_expiration(expires_in_seconds)
        _content_type(content_type)
        _tenant_metadata_for_presigned_put(
            metadata,
            content_hash=content_hash,
            domain=normalized_domain,
            context=resolved_context,
            audit_sink=audit_sink,
        )
        key = build_tenant_object_key(
            normalized_domain,
            normalized_object_id,
            context=resolved_context,
            tenant_prefix=self._tenant_prefix,
        )
        return (
            f"memory://{self._bucket}/{key}?operation=put_object&expires_in={expires}"
        )

    def _reference_from_stored(
        self,
        key: str,
        domain: str,
        object_id: str,
        stored: _StoredObject,
        *,
        context: TenantContext,
        audit_sink: AuditSink | None,
    ) -> TenantObjectReference:
        _assert_metadata_tenant(
            stored.metadata,
            context=context,
            audit_sink=audit_sink,
            resource_type="object_result",
        )
        return TenantObjectReference(
            bucket=self._bucket,
            key=key,
            domain=domain,
            object_id=object_id,
            size=len(stored.body),
            content_type=stored.content_type,
            metadata=stored.metadata,
            etag=stored.etag,
        )


@dataclass(frozen=True, slots=True)
class S3TenantObjectStorage:
    """boto3-backed implementation of the shared tenant object contract."""

    client: Any
    settings: S3Settings

    @classmethod
    def from_settings(cls, settings: S3Settings) -> S3TenantObjectStorage:
        boto3 = cast(Any, import_module("boto3"))
        client = boto3.client(
            "s3",
            endpoint_url=settings.endpoint_url,
            aws_access_key_id=settings.access_key,
            aws_secret_access_key=settings.secret_key,
            region_name=settings.region,
        )

        return cls(client=client, settings=settings)

    async def put_object(
        self,
        domain: str,
        object_id: str,
        body: bytes | bytearray | memoryview,
        *,
        content_type: str = "application/octet-stream",
        metadata: Mapping[str, str] | None = None,
        context: TenantContext | None = None,
        audit_sink: AuditSink | None = None,
    ) -> TenantObjectReference:
        resolved_context = _resolve_context(context)
        normalized_domain = _safe_segment(domain, "domain")
        normalized_object_id = _normalize_object_id(object_id)
        normalized_body = bytes(body)
        normalized_content_type = _content_type(content_type)
        key = build_tenant_object_key(
            normalized_domain,
            normalized_object_id,
            context=resolved_context,
            tenant_prefix=self.settings.tenant_prefix,
        )
        object_metadata = _tenant_metadata_for_object(
            metadata,
            body=normalized_body,
            domain=normalized_domain,
            context=resolved_context,
            audit_sink=audit_sink,
        )
        raw_response = await asyncio.to_thread(
            self.client.put_object,
            Bucket=self.settings.bucket,
            Key=key,
            Body=normalized_body,
            ContentType=normalized_content_type,
            Metadata=object_metadata,
        )

        return TenantObjectReference(
            bucket=self.settings.bucket,
            key=key,
            domain=normalized_domain,
            object_id=normalized_object_id,
            size=len(normalized_body),
            content_type=normalized_content_type,
            metadata=object_metadata,
            etag=_etag_from_response(raw_response),
        )

    async def get_object(
        self,
        domain: str,
        object_id: str,
        *,
        context: TenantContext | None = None,
        audit_sink: AuditSink | None = None,
    ) -> TenantObject | None:
        resolved_context = _resolve_context(context)
        normalized_domain = _safe_segment(domain, "domain")
        normalized_object_id = _normalize_object_id(object_id)
        key = build_tenant_object_key(
            normalized_domain,
            normalized_object_id,
            context=resolved_context,
            tenant_prefix=self.settings.tenant_prefix,
        )
        try:
            raw_response = await asyncio.to_thread(
                self.client.get_object,
                Bucket=self.settings.bucket,
                Key=key,
            )
        except Exception as error:
            if _is_s3_not_found(error):
                return None
            raise

        body_stream = raw_response["Body"]
        body = bytes(await asyncio.to_thread(body_stream.read))
        metadata = _normalize_metadata(
            cast(Mapping[str, str], raw_response.get("Metadata") or {})
        )
        _assert_metadata_tenant(
            metadata,
            context=resolved_context,
            audit_sink=audit_sink,
            resource_type="object_result",
        )

        return TenantObject(
            reference=TenantObjectReference(
                bucket=self.settings.bucket,
                key=key,
                domain=normalized_domain,
                object_id=normalized_object_id,
                size=_size_from_response(raw_response, default=len(body)),
                content_type=_optional_response_string(raw_response, "ContentType"),
                metadata=metadata,
                etag=_etag_from_response(raw_response),
            ),
            body=body,
        )

    async def list_objects(
        self,
        domain: str,
        *,
        prefix: str = "",
        context: TenantContext | None = None,
    ) -> list[TenantObjectReference]:
        resolved_context = _resolve_context(context)
        normalized_domain = _safe_segment(domain, "domain")
        object_prefix = _normalize_optional_object_prefix(prefix)
        tenant_domain_prefix = build_tenant_object_prefix(
            normalized_domain,
            context=resolved_context,
            tenant_prefix=self.settings.tenant_prefix,
        )
        key_prefix = tenant_domain_prefix + object_prefix

        def collect_pages() -> list[Mapping[str, object]]:
            paginator = self.client.get_paginator("list_objects_v2")
            return list(
                paginator.paginate(
                    Bucket=self.settings.bucket,
                    Prefix=key_prefix,
                )
            )

        pages = await asyncio.to_thread(collect_pages)
        references: list[TenantObjectReference] = []
        for page in pages:
            contents = page.get("Contents", [])
            if not isinstance(contents, Sequence) or isinstance(contents, str | bytes):
                continue
            for item in contents:
                if not isinstance(item, Mapping):
                    continue
                key = item.get("Key")
                if not isinstance(key, str) or not key.startswith(tenant_domain_prefix):
                    continue
                size = item.get("Size")
                references.append(
                    TenantObjectReference(
                        bucket=self.settings.bucket,
                        key=key,
                        domain=normalized_domain,
                        object_id=key.removeprefix(tenant_domain_prefix),
                        size=int(size) if isinstance(size, int) else None,
                    )
                )

        return references

    async def delete_object(
        self,
        domain: str,
        object_id: str,
        *,
        context: TenantContext | None = None,
    ) -> bool:
        key = build_tenant_object_key(
            domain,
            object_id,
            context=context,
            tenant_prefix=self.settings.tenant_prefix,
        )
        await asyncio.to_thread(
            self.client.delete_object,
            Bucket=self.settings.bucket,
            Key=key,
        )

        return True

    async def create_presigned_get_url(
        self,
        domain: str,
        object_id: str,
        *,
        context: TenantContext | None = None,
        expires_in_seconds: int = DEFAULT_PRESIGNED_EXPIRES_SECONDS,
    ) -> str:
        expires = _positive_expiration(expires_in_seconds)
        key = build_tenant_object_key(
            domain,
            object_id,
            context=context,
            tenant_prefix=self.settings.tenant_prefix,
        )
        url = await asyncio.to_thread(
            self.client.generate_presigned_url,
            "get_object",
            Params={"Bucket": self.settings.bucket, "Key": key},
            ExpiresIn=expires,
            HttpMethod="GET",
        )

        return str(url)

    async def create_presigned_put_url(
        self,
        domain: str,
        object_id: str,
        *,
        content_hash: str,
        content_type: str = "application/octet-stream",
        metadata: Mapping[str, str] | None = None,
        context: TenantContext | None = None,
        audit_sink: AuditSink | None = None,
        expires_in_seconds: int = DEFAULT_PRESIGNED_EXPIRES_SECONDS,
    ) -> str:
        resolved_context = _resolve_context(context)
        normalized_domain = _safe_segment(domain, "domain")
        normalized_object_id = _normalize_object_id(object_id)
        normalized_content_type = _content_type(content_type)
        expires = _positive_expiration(expires_in_seconds)
        object_metadata = _tenant_metadata_for_presigned_put(
            metadata,
            content_hash=content_hash,
            domain=normalized_domain,
            context=resolved_context,
            audit_sink=audit_sink,
        )
        key = build_tenant_object_key(
            normalized_domain,
            normalized_object_id,
            context=resolved_context,
            tenant_prefix=self.settings.tenant_prefix,
        )
        url = await asyncio.to_thread(
            self.client.generate_presigned_url,
            "put_object",
            Params={
                "Bucket": self.settings.bucket,
                "Key": key,
                "ContentType": normalized_content_type,
                "Metadata": object_metadata,
            },
            ExpiresIn=expires,
            HttpMethod="PUT",
        )

        return str(url)


def s3_endpoint_url_from_env(
    environ: Mapping[str, str] | None = None,
    *,
    env_var: str = S3_ENDPOINT_URL_ENV,
) -> str:
    source = os.environ if environ is None else environ
    endpoint_url = source.get(env_var)
    if endpoint_url is None or endpoint_url.strip() == "":
        raise ValueError(f"{env_var} должен быть задан")

    try:
        return validate_s3_endpoint_url(endpoint_url)
    except ValueError as error:
        message = f"{env_var} должен использовать http:// или https://"
        raise ValueError(message) from error


def validate_s3_endpoint_url(endpoint_url: str) -> str:
    normalized_url = endpoint_url.strip().rstrip("/")
    if normalized_url == "":
        raise ValueError("S3_ENDPOINT_URL должен быть непустой строкой")

    parsed_url = urlparse(normalized_url)
    if parsed_url.scheme not in {"http", "https"} or parsed_url.netloc == "":
        raise ValueError("S3_ENDPOINT_URL должен использовать http:// или https://")

    return normalized_url


def validate_s3_bucket_name(bucket: str) -> str:
    normalized = bucket.strip()
    if not _BUCKET_PATTERN.fullmatch(normalized):
        raise ValueError("S3_BUCKET должен быть DNS-compatible bucket name")
    if ".." in normalized or ".-" in normalized or "-." in normalized:
        raise ValueError("S3_BUCKET не должен содержать небезопасные сочетания")

    return normalized


def build_tenant_object_key(
    domain: str,
    object_id: str,
    *,
    context: TenantContext | None = None,
    tenant_prefix: str = DEFAULT_S3_TENANT_PREFIX,
) -> str:
    return build_tenant_object_prefix(
        domain,
        context=context,
        tenant_prefix=tenant_prefix,
    ) + _normalize_object_id(object_id)


def build_tenant_object_prefix(
    domain: str | None = None,
    *,
    context: TenantContext | None = None,
    tenant_prefix: str = DEFAULT_S3_TENANT_PREFIX,
) -> str:
    resolved_context = _resolve_context(context)
    normalized_prefix = _safe_segment(tenant_prefix, "tenant_prefix")
    tenant_segment = _tenant_path_segment(resolved_context.tenant_id)
    if domain is None:
        return f"{normalized_prefix}/{tenant_segment}/"

    return f"{normalized_prefix}/{tenant_segment}/{_safe_segment(domain, 'domain')}/"


def build_tenant_s3_prefix_policy(
    bucket: str,
    *,
    context: TenantContext | None = None,
    tenant_prefix: str = DEFAULT_S3_TENANT_PREFIX,
) -> dict[str, object]:
    bucket_name = validate_s3_bucket_name(bucket)
    tenant_object_prefix = build_tenant_object_prefix(
        context=context,
        tenant_prefix=tenant_prefix,
    )

    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "ListTenantPrefix",
                "Effect": "Allow",
                "Action": ["s3:ListBucket"],
                "Resource": [f"arn:aws:s3:::{bucket_name}"],
                "Condition": {
                    "StringLike": {"s3:prefix": [f"{tenant_object_prefix}*"]}
                },
            },
            {
                "Sid": "TenantObjectAccess",
                "Effect": "Allow",
                "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
                "Resource": [f"arn:aws:s3:::{bucket_name}/{tenant_object_prefix}*"],
            },
        ],
    }


def _tenant_metadata_for_object(
    metadata: Mapping[str, str] | None,
    *,
    body: bytes,
    domain: str,
    context: TenantContext,
    audit_sink: AuditSink | None,
) -> ObjectMetadata:
    normalized_metadata = _normalize_metadata(metadata or {})
    requested_tenant_id = normalized_metadata.get("tenant_id")
    if requested_tenant_id is not None:
        assert_requested_tenant(
            requested_tenant_id,
            context=context,
            audit_sink=audit_sink,
            resource_type="object_metadata",
        )

    normalized_metadata["tenant_id"] = context.tenant_id
    normalized_metadata["domain"] = domain
    normalized_metadata["content_hash"] = hashlib.sha256(body).hexdigest()
    if context.correlation_id is not None:
        normalized_metadata["correlation_id"] = context.correlation_id

    return normalized_metadata


def _tenant_metadata_for_presigned_put(
    metadata: Mapping[str, str] | None,
    *,
    content_hash: str,
    domain: str,
    context: TenantContext,
    audit_sink: AuditSink | None,
) -> ObjectMetadata:
    normalized_metadata = _normalize_metadata(metadata or {})
    requested_tenant_id = normalized_metadata.get("tenant_id")
    if requested_tenant_id is not None:
        assert_requested_tenant(
            requested_tenant_id,
            context=context,
            audit_sink=audit_sink,
            resource_type="object_metadata",
        )

    normalized_metadata["tenant_id"] = context.tenant_id
    normalized_metadata["domain"] = domain
    normalized_metadata["content_hash"] = _sha256_hex(content_hash)
    if context.correlation_id is not None:
        normalized_metadata["correlation_id"] = context.correlation_id

    return normalized_metadata


def _assert_metadata_tenant(
    metadata: Mapping[str, str],
    *,
    context: TenantContext,
    audit_sink: AuditSink | None,
    resource_type: str,
) -> None:
    requested_tenant_id = metadata.get("tenant_id")
    if requested_tenant_id is None:
        raise TenantIsolationError(
            "S3 object metadata не содержит tenant_id",
            correlation_id=context.correlation_id,
        )
    assert_requested_tenant(
        requested_tenant_id,
        context=context,
        audit_sink=audit_sink,
        resource_type=resource_type,
    )


def _normalize_metadata(metadata: Mapping[str, str]) -> ObjectMetadata:
    normalized: ObjectMetadata = {}
    for key, value in metadata.items():
        if not isinstance(key, str) or not _METADATA_KEY_PATTERN.fullmatch(key):
            raise ValueError("metadata key должен быть безопасной строкой")
        if not isinstance(value, str):
            raise ValueError("metadata value должен быть строкой")
        if any(ord(character) < 32 for character in value):
            raise ValueError("metadata value не должен содержать control characters")
        normalized[key.lower()] = value

    return normalized


def _safe_segment(value: str, label: str) -> str:
    normalized = value.strip()
    if not _SAFE_SEGMENT_PATTERN.fullmatch(normalized):
        raise ValueError(f"{label} должен быть безопасным path segment")

    return normalized


def _tenant_path_segment(tenant_id: str) -> str:
    normalized = tenant_id.strip()
    if normalized == "" or "/" in normalized or "\\" in normalized:
        raise TenantIsolationError("tenant_id не должен содержать path separators")
    if any(character.isspace() or ord(character) < 32 for character in normalized):
        raise TenantIsolationError("tenant_id не должен содержать пробелы")
    if ".." in normalized:
        raise TenantIsolationError("tenant_id не должен содержать '..'")

    return normalized


def _normalize_object_id(object_id: str) -> str:
    normalized = object_id.strip()
    if normalized == "":
        raise ValueError("object_id должен быть непустой строкой")
    if normalized.startswith("/") or "\\" in normalized:
        raise ValueError("object_id не должен начинаться с '/' или содержать '\\'")
    if "//" in normalized:
        raise ValueError("object_id не должен содержать пустые path segments")
    if any(character.isspace() or ord(character) < 32 for character in normalized):
        raise ValueError("object_id не должен содержать пробелы")
    if any(part in {".", ".."} for part in normalized.split("/")):
        raise ValueError("object_id не должен содержать '.' или '..'")

    return normalized


def _normalize_optional_object_prefix(prefix: str) -> str:
    normalized = prefix.strip()
    if normalized == "":
        return ""
    normalized_object_id = _normalize_object_id(normalized)
    return (
        normalized_object_id if normalized_object_id.endswith("/") else f"{normalized}/"
    )


def _normalize_key(value: str, label: str) -> str:
    normalized = value.strip()
    if normalized == "" or normalized.startswith("/") or "\\" in normalized:
        raise ValueError(f"{label} должен быть безопасным S3 key")
    if "//" in normalized:
        raise ValueError(f"{label} не должен содержать пустые path segments")

    return normalized


def _content_type(content_type: str) -> str:
    normalized = content_type.strip()
    if normalized == "" or any(character.isspace() for character in normalized):
        raise ValueError("content_type должен быть media type без пробелов")

    return normalized


def _positive_expiration(expires_in_seconds: int) -> int:
    if expires_in_seconds <= 0:
        raise ValueError("expires_in_seconds должен быть положительным")

    return expires_in_seconds


def _sha256_hex(value: str) -> str:
    normalized = value.strip().lower()
    if not re.fullmatch(r"[a-f0-9]{64}", normalized):
        raise ValueError("content_hash должен быть SHA256 hex digest")

    return normalized


def _env_value(environ: Mapping[str, str], env_var: str) -> str:
    raw_value = environ.get(env_var)
    if raw_value is None or raw_value.strip() == "":
        raise ValueError(f"{env_var} должен быть задан")

    return raw_value


def _required_string(value: str, label: str) -> str:
    normalized = value.strip()
    if normalized == "":
        raise ValueError(f"{label} должен быть задан")

    return normalized


def _resolve_context(context: TenantContext | None) -> TenantContext:
    if context is not None:
        return context

    return require_tenant_context()


def _etag_from_response(raw_response: object) -> str | None:
    if not isinstance(raw_response, Mapping):
        return None

    etag = raw_response.get("ETag")
    if not isinstance(etag, str):
        return None

    return etag.strip('"')


def _size_from_response(raw_response: Mapping[str, object], *, default: int) -> int:
    content_length = raw_response.get("ContentLength")
    if isinstance(content_length, int) and content_length >= 0:
        return content_length

    return default


def _optional_response_string(
    raw_response: Mapping[str, object],
    key: str,
) -> str | None:
    value = raw_response.get(key)
    if isinstance(value, str) and value.strip() != "":
        return value

    return None


def _is_s3_not_found(error: Exception) -> bool:
    response = getattr(error, "response", None)
    if not isinstance(response, Mapping):
        return False

    error_payload = response.get("Error")
    response_metadata = response.get("ResponseMetadata")
    status_code = None
    if isinstance(response_metadata, Mapping):
        status_code = response_metadata.get("HTTPStatusCode")
    if status_code == 404:
        return True
    if not isinstance(error_payload, Mapping):
        return False

    code = error_payload.get("Code")
    return code in {"NoSuchKey", "NoSuchBucket", "404", "NotFound"}
