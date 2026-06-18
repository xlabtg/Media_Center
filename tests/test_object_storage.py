from __future__ import annotations

import asyncio
import hashlib

import pytest

from libs.shared import (
    InMemoryAuditSink,
    InMemoryTenantObjectStorage,
    S3Settings,
    TenantContext,
    TenantIsolationError,
    build_tenant_object_key,
    build_tenant_s3_prefix_policy,
    s3_endpoint_url_from_env,
)


def _context(tenant_id: str = "tenant-a") -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        subject="member-1",
        roles=("member_full",),
        correlation_id="corr-storage-1",
    )


def test_s3_settings_read_endpoint_credentials_and_bucket_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("S3_ENDPOINT_URL", raising=False)

    with pytest.raises(ValueError, match="S3_ENDPOINT_URL"):
        s3_endpoint_url_from_env()

    monkeypatch.setenv("S3_ENDPOINT_URL", "ftp://localhost:9000")
    with pytest.raises(ValueError, match="http://"):
        s3_endpoint_url_from_env()

    monkeypatch.setenv("S3_ENDPOINT_URL", "http://localhost:9000")
    monkeypatch.setenv("S3_ACCESS_KEY", "nmc_minio")
    monkeypatch.setenv("S3_SECRET_KEY", "nmc_minio_dev_password")
    monkeypatch.setenv("S3_BUCKET", "nmc-dev")

    settings = S3Settings.from_env()

    assert settings.endpoint_url == "http://localhost:9000"
    assert settings.access_key == "nmc_minio"
    assert settings.secret_key == "nmc_minio_dev_password"
    assert settings.bucket == "nmc-dev"
    assert settings.region == "ru-central-1"


def test_object_keys_are_tenant_and_domain_scoped() -> None:
    key = build_tenant_object_key(
        "content",
        "post-001.bin",
        context=_context("tenant-a"),
    )

    assert key == "tenants/tenant-a/content/post-001.bin"

    with pytest.raises(ValueError, match="domain"):
        build_tenant_object_key("raw/content", "post-001.bin", context=_context())

    with pytest.raises(ValueError, match="object_id"):
        build_tenant_object_key("content", "../secrets.env", context=_context())


def test_in_memory_object_storage_round_trips_bytes_with_tenant_metadata() -> None:
    asyncio.run(_run_object_storage_scenario())


async def _run_object_storage_scenario() -> None:
    storage = InMemoryTenantObjectStorage(bucket="nmc-dev")
    tenant_a = _context("tenant-a")
    tenant_b = _context("tenant-b")
    body = b"published media payload"

    reference = await storage.put_object(
        "content",
        "post-001.bin",
        body,
        content_type="application/octet-stream",
        metadata={"source": "fixture"},
        context=tenant_a,
    )
    stored = await storage.get_object("content", "post-001.bin", context=tenant_a)
    other_tenant_stored = await storage.get_object(
        "content",
        "post-001.bin",
        context=tenant_b,
    )
    tenant_a_objects = await storage.list_objects("content", context=tenant_a)
    tenant_b_objects = await storage.list_objects("content", context=tenant_b)

    assert reference.bucket == "nmc-dev"
    assert reference.key == "tenants/tenant-a/content/post-001.bin"
    assert reference.size == len(body)
    assert stored is not None
    assert stored.body == body
    assert stored.reference.metadata["tenant_id"] == "tenant-a"
    assert stored.reference.metadata["domain"] == "content"
    assert stored.reference.metadata["correlation_id"] == "corr-storage-1"
    assert stored.reference.metadata["content_hash"] == hashlib.sha256(body).hexdigest()
    assert stored.reference.metadata["source"] == "fixture"
    assert other_tenant_stored is None
    assert [item.object_id for item in tenant_a_objects] == ["post-001.bin"]
    assert tenant_b_objects == []


def test_object_storage_rejects_cross_tenant_metadata_override() -> None:
    asyncio.run(_run_cross_tenant_object_storage_scenario())


async def _run_cross_tenant_object_storage_scenario() -> None:
    storage = InMemoryTenantObjectStorage(bucket="nmc-dev")
    audit_sink = InMemoryAuditSink()

    with pytest.raises(TenantIsolationError) as exc_info:
        await storage.put_object(
            "content",
            "post-cross.bin",
            b"payload",
            metadata={"tenant_id": "tenant-b"},
            context=_context("tenant-a"),
            audit_sink=audit_sink,
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.error_code == "tenant_isolation_violation"
    assert len(audit_sink.events) == 1
    assert audit_sink.events[0].tenant_id == "tenant-a"
    assert audit_sink.events[0].requested_tenant_hash is not None
    assert audit_sink.events[0].resource_type == "object_metadata"


def test_presigned_urls_and_policy_are_limited_to_tenant_prefix() -> None:
    asyncio.run(_run_presigned_and_policy_scenario())


async def _run_presigned_and_policy_scenario() -> None:
    context = _context("tenant-a")
    storage = InMemoryTenantObjectStorage(bucket="nmc-dev")

    url = await storage.create_presigned_get_url(
        "content",
        "post-001.bin",
        context=context,
        expires_in_seconds=900,
    )
    upload_url = await storage.create_presigned_put_url(
        "content",
        "post-001.bin",
        content_hash=hashlib.sha256(b"published media payload").hexdigest(),
        context=context,
        expires_in_seconds=900,
    )
    policy = build_tenant_s3_prefix_policy("nmc-dev", context=context)

    assert "tenants/tenant-a/content/post-001.bin" in url
    assert "expires_in=900" in url
    assert "tenants/tenant-a/content/post-001.bin" in upload_url
    assert "operation=put_object" in upload_url
    assert policy == {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "ListTenantPrefix",
                "Effect": "Allow",
                "Action": ["s3:ListBucket"],
                "Resource": ["arn:aws:s3:::nmc-dev"],
                "Condition": {"StringLike": {"s3:prefix": ["tenants/tenant-a/*"]}},
            },
            {
                "Sid": "TenantObjectAccess",
                "Effect": "Allow",
                "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
                "Resource": ["arn:aws:s3:::nmc-dev/tenants/tenant-a/*"],
            },
        ],
    }
