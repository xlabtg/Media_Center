from __future__ import annotations

import asyncio

import pytest

from libs.shared import (
    ChromaSettings,
    InMemoryAuditSink,
    InMemoryTenantVectorStore,
    TenantContext,
    TenantIsolationError,
    VectorRecord,
    build_tenant_vector_collection_name,
    chroma_port_from_env,
)


def _context(tenant_id: str = "tenant-a") -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        subject="member-1",
        roles=("member_full",),
        correlation_id="corr-vector-1",
    )


def test_chroma_settings_read_host_port_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CHROMA_HOST", raising=False)

    with pytest.raises(ValueError, match="CHROMA_HOST"):
        ChromaSettings.from_env()

    monkeypatch.setenv("CHROMA_HOST", "localhost")
    monkeypatch.setenv("CHROMA_PORT", "8001")

    settings = ChromaSettings.from_env()

    assert settings.host == "localhost"
    assert settings.port == 8001
    assert settings.collection_prefix == "nmc"
    assert settings.environment == "dev"


def test_chroma_port_from_env_requires_valid_tcp_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CHROMA_PORT", "not-a-port")

    with pytest.raises(ValueError, match="CHROMA_PORT"):
        chroma_port_from_env()

    monkeypatch.setenv("CHROMA_PORT", "70000")

    with pytest.raises(ValueError, match="CHROMA_PORT"):
        chroma_port_from_env()


def test_collection_names_are_tenant_and_domain_scoped() -> None:
    context = _context("00000000-0000-4000-8000-000000000001")

    collection_name = build_tenant_vector_collection_name(
        "memory",
        context=context,
    )

    assert collection_name == ("nmc_dev_00000000-0000-4000-8000-000000000001_memory")


def test_in_memory_vector_store_upserts_and_queries_with_tenant_filter() -> None:
    asyncio.run(_run_vector_store_scenario())


async def _run_vector_store_scenario() -> None:
    store = InMemoryTenantVectorStore()
    tenant_a = _context("tenant-a")
    tenant_b = _context("tenant-b")

    await store.upsert(
        "memory",
        [
            VectorRecord(
                id="doc-a",
                embedding=(0.0, 0.0, 1.0),
                document="Совет утвердил регламент выплат",
                metadata={"source": "minutes"},
            )
        ],
        context=tenant_a,
    )
    await store.upsert(
        "memory",
        [
            VectorRecord(
                id="doc-b",
                embedding=(1.0, 0.0, 0.0),
                document="План публикаций другого tenant",
                metadata={"source": "calendar"},
            )
        ],
        context=tenant_b,
    )

    tenant_a_results = await store.query(
        "memory",
        (0.0, 0.0, 0.9),
        context=tenant_a,
        limit=3,
    )
    tenant_b_results = await store.query(
        "memory",
        (0.0, 0.0, 0.9),
        context=tenant_b,
        limit=3,
    )

    assert [result.id for result in tenant_a_results] == ["doc-a"]
    assert tenant_a_results[0].metadata["tenant_id"] == "tenant-a"
    assert tenant_a_results[0].metadata["domain"] == "memory"
    assert [result.id for result in tenant_b_results] == ["doc-b"]


def test_vector_store_rejects_cross_tenant_metadata_override() -> None:
    asyncio.run(_run_cross_tenant_vector_scenario())


async def _run_cross_tenant_vector_scenario() -> None:
    store = InMemoryTenantVectorStore()
    audit_sink = InMemoryAuditSink()

    with pytest.raises(TenantIsolationError) as exc_info:
        await store.upsert(
            "memory",
            [
                VectorRecord(
                    id="doc-cross",
                    embedding=(0.0, 1.0),
                    metadata={"tenant_id": "tenant-b"},
                )
            ],
            context=_context("tenant-a"),
            audit_sink=audit_sink,
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.error_code == "tenant_isolation_violation"
    assert len(audit_sink.events) == 1
    assert audit_sink.events[0].tenant_id == "tenant-a"
    assert audit_sink.events[0].requested_tenant_hash is not None
    assert audit_sink.events[0].resource_type == "vector_metadata"
