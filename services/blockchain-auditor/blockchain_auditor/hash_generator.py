from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import cast

from pydantic import Field

from libs.shared.audit_logger import (
    AUDIT_HASH_ALGORITHM,
    AuditPayload,
    audit_hash_from_payload,
    build_audit_payload,
    canonical_audit_json,
)
from libs.shared.models import (
    AuditHash,
    JSONValue,
    SharedBaseModel,
)


class HashGenerationResult(SharedBaseModel):
    algorithm: str = Field(default=AUDIT_HASH_ALGORITHM, pattern="^sha256$")
    audit_hash: AuditHash
    canonical_payload: dict[str, JSONValue]
    canonical_json: str


def generate_event_hash(
    *,
    event_type: str,
    tenant_id: str,
    timestamp: datetime | str,
    points: int | float | None = None,
    metadata: Mapping[str, JSONValue] | None = None,
) -> HashGenerationResult:
    payload = build_audit_payload(
        event_type=event_type,
        tenant_id=tenant_id,
        points=points,
        metadata=metadata,
        timestamp=timestamp,
    )
    return generate_event_hash_from_payload(payload)


def generate_event_hash_from_payload(payload: AuditPayload) -> HashGenerationResult:
    return HashGenerationResult(
        algorithm=AUDIT_HASH_ALGORITHM,
        audit_hash=audit_hash_from_payload(payload),
        canonical_payload=cast(dict[str, JSONValue], payload.to_hash_payload()),
        canonical_json=canonical_audit_json(payload),
    )
