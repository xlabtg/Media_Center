from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Final

from libs.shared.tenant import (
    TenantContext,
    encode_hs256_jwt,
    tenant_headers_from_context,
)

DEFAULT_TEST_JWT_SECRET: Final = "local-dev-secret"
DEFAULT_TEST_NOW: Final = 1_800_000_000


@dataclass(frozen=True, slots=True)
class TenantTestIdentity:
    """Reusable tenant identity for unit, integration and e2e test data."""

    tenant_id: str
    subject: str
    roles: tuple[str, ...] = ("member_full",)
    correlation_id: str = "corr-test"

    def context(self) -> TenantContext:
        return TenantContext(
            tenant_id=self.tenant_id,
            subject=self.subject,
            roles=self.roles,
            correlation_id=self.correlation_id,
        )

    def headers(
        self,
        *,
        service_name: str | None = None,
        forwarded_prefix: str | None = None,
        original_path: str | None = None,
    ) -> dict[str, str]:
        return tenant_headers_from_context(
            self.context(),
            service_name=service_name,
            forwarded_prefix=forwarded_prefix,
            original_path=original_path,
        )

    def jwt(
        self,
        *,
        secret: str = DEFAULT_TEST_JWT_SECRET,
        issuer: str = "nmc",
        audience: str = "api-gateway",
        expires_at: int = DEFAULT_TEST_NOW + 3600,
    ) -> str:
        return encode_hs256_jwt(
            {
                "tenant_id": self.tenant_id,
                "sub": self.subject,
                "roles": list(self.roles),
                "iss": issuer,
                "aud": audience,
                "exp": expires_at,
            },
            secret,
        )


@dataclass(frozen=True, slots=True)
class TenantTestDataset:
    """Small deterministic dataset with explicit owner and foreign records."""

    owner: TenantTestIdentity
    foreign: TenantTestIdentity
    owner_records: tuple[dict[str, object], ...]
    foreign_records: tuple[dict[str, object], ...]

    @property
    def all_records(self) -> tuple[dict[str, object], ...]:
        return self.owner_records + self.foreign_records


def build_tenant_test_dataset(
    *,
    owner_tenant_id: str = "tenant-a",
    foreign_tenant_id: str = "tenant-b",
) -> TenantTestDataset:
    owner = TenantTestIdentity(
        tenant_id=owner_tenant_id,
        subject="member-owner",
        correlation_id="corr-owner",
    )
    foreign = TenantTestIdentity(
        tenant_id=foreign_tenant_id,
        subject="member-foreign",
        correlation_id="corr-foreign",
    )

    return TenantTestDataset(
        owner=owner,
        foreign=foreign,
        owner_records=(
            {
                "tenant_id": owner.tenant_id,
                "record_id": "contribution-owner-1",
                "kind": "contribution",
                "points": 10,
            },
            {
                "tenant_id": owner.tenant_id,
                "record_id": "wallet-owner-1",
                "kind": "wallet_operation",
                "points": 5,
            },
        ),
        foreign_records=(
            {
                "tenant_id": foreign.tenant_id,
                "record_id": "contribution-foreign-1",
                "kind": "contribution",
                "points": 20,
            },
        ),
    )


def assert_only_tenant_records(
    records: Iterable[Mapping[str, object]],
    tenant_id: str,
) -> None:
    leaked_records = [
        record for record in records if record.get("tenant_id") != tenant_id
    ]
    if leaked_records:
        leaked_ids = ", ".join(
            str(record.get("record_id", "<unknown>")) for record in leaked_records
        )
        raise AssertionError(
            f"cross-tenant test data leak for {tenant_id}: {leaked_ids}"
        )
