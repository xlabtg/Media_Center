from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from libs.shared.tenant import TenantContext

from .connector import (
    AuditBatchError,
    AuditRecordCommand,
    AuditRecordReceipt,
    GrpcBlockchainAuditConnector,
)


@dataclass(frozen=True, slots=True)
class AuditBatchWriter:
    connector: GrpcBlockchainAuditConnector
    max_batch_size: int = 100

    def __post_init__(self) -> None:
        if self.max_batch_size <= 0:
            raise ValueError("max_batch_size должен быть положительным")

    async def record_batch(
        self,
        commands: Iterable[AuditRecordCommand],
        *,
        context: TenantContext | None = None,
    ) -> tuple[AuditRecordReceipt, ...]:
        batch = tuple(commands)
        if not batch:
            raise AuditBatchError("batch должен содержать хотя бы одну audit-запись")
        if len(batch) > self.max_batch_size:
            raise AuditBatchError(
                f"batch не должен превышать {self.max_batch_size} audit-записей"
            )

        return await self.connector.record_audit_hashes(batch, context=context)
