from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from blockchain_auditor.access_controller import (
        AUDIT_BATCH_RECORD_POLICY,
        AUDIT_READ_POLICY,
        AUDIT_RECORD_POLICY,
        BLOCKCHAIN_AUDITOR_RESOURCE_TYPE,
        BlockchainAuditAccessController,
    )
    from blockchain_auditor.api import (
        AuditEventVerificationRequest,
        AuditEventVerificationResponse,
        AuditHashVerificationResponse,
        AuditRecordBatchRequest,
        AuditRecordBatchResponse,
        AuditRecordRequestItem,
        BlockchainAuditorAPIState,
        create_blockchain_auditor_app,
    )
    from blockchain_auditor.batch_writer import AuditBatchWriter
    from blockchain_auditor.connector import (
        AuditBatchError,
        AuditMetadataPolicyError,
        AuditRecord,
        AuditRecordCommand,
        AuditRecordConflictError,
        AuditRecordReceipt,
        BlockchainAuditError,
        GrpcBlockchainAuditConnector,
        GrpcBlockchainAuditTransport,
        InMemoryGrpcBlockchainAuditTransport,
        validate_audit_metadata,
    )
    from blockchain_auditor.hash_generator import (
        HashGenerationResult,
        generate_event_hash,
        generate_event_hash_from_payload,
    )
    from blockchain_auditor.settings import (
        BLOCKCHAIN_AUDITOR_SERVICE_NAME,
        DEFAULT_BLOCKCHAIN_AUDITOR_URL,
        BlockchainAuditorSettings,
        build_blockchain_auditor_settings,
    )

_EXPORTS: dict[str, str] = {
    "AUDIT_BATCH_RECORD_POLICY": "blockchain_auditor.access_controller",
    "AUDIT_READ_POLICY": "blockchain_auditor.access_controller",
    "AUDIT_RECORD_POLICY": "blockchain_auditor.access_controller",
    "BLOCKCHAIN_AUDITOR_RESOURCE_TYPE": "blockchain_auditor.access_controller",
    "BlockchainAuditAccessController": "blockchain_auditor.access_controller",
    "AuditEventVerificationRequest": "blockchain_auditor.api",
    "AuditEventVerificationResponse": "blockchain_auditor.api",
    "AuditHashVerificationResponse": "blockchain_auditor.api",
    "AuditRecordBatchRequest": "blockchain_auditor.api",
    "AuditRecordBatchResponse": "blockchain_auditor.api",
    "AuditRecordRequestItem": "blockchain_auditor.api",
    "BlockchainAuditorAPIState": "blockchain_auditor.api",
    "create_blockchain_auditor_app": "blockchain_auditor.api",
    "AuditBatchWriter": "blockchain_auditor.batch_writer",
    "AuditBatchError": "blockchain_auditor.connector",
    "AuditMetadataPolicyError": "blockchain_auditor.connector",
    "AuditRecord": "blockchain_auditor.connector",
    "AuditRecordCommand": "blockchain_auditor.connector",
    "AuditRecordConflictError": "blockchain_auditor.connector",
    "AuditRecordReceipt": "blockchain_auditor.connector",
    "BlockchainAuditError": "blockchain_auditor.connector",
    "GrpcBlockchainAuditConnector": "blockchain_auditor.connector",
    "GrpcBlockchainAuditTransport": "blockchain_auditor.connector",
    "InMemoryGrpcBlockchainAuditTransport": "blockchain_auditor.connector",
    "validate_audit_metadata": "blockchain_auditor.connector",
    "HashGenerationResult": "blockchain_auditor.hash_generator",
    "generate_event_hash": "blockchain_auditor.hash_generator",
    "generate_event_hash_from_payload": "blockchain_auditor.hash_generator",
    "BLOCKCHAIN_AUDITOR_SERVICE_NAME": "blockchain_auditor.settings",
    "DEFAULT_BLOCKCHAIN_AUDITOR_URL": "blockchain_auditor.settings",
    "BlockchainAuditorSettings": "blockchain_auditor.settings",
    "build_blockchain_auditor_settings": "blockchain_auditor.settings",
}

__all__ = [
    "AUDIT_BATCH_RECORD_POLICY",
    "BLOCKCHAIN_AUDITOR_SERVICE_NAME",
    "BLOCKCHAIN_AUDITOR_RESOURCE_TYPE",
    "DEFAULT_BLOCKCHAIN_AUDITOR_URL",
    "AUDIT_READ_POLICY",
    "AUDIT_RECORD_POLICY",
    "AuditRecordBatchRequest",
    "AuditRecordBatchResponse",
    "AuditRecordRequestItem",
    "AuditBatchError",
    "AuditEventVerificationRequest",
    "AuditEventVerificationResponse",
    "AuditHashVerificationResponse",
    "AuditBatchWriter",
    "AuditMetadataPolicyError",
    "AuditRecord",
    "AuditRecordCommand",
    "AuditRecordConflictError",
    "AuditRecordReceipt",
    "BlockchainAuditError",
    "BlockchainAuditAccessController",
    "BlockchainAuditorAPIState",
    "BlockchainAuditorSettings",
    "GrpcBlockchainAuditConnector",
    "GrpcBlockchainAuditTransport",
    "HashGenerationResult",
    "InMemoryGrpcBlockchainAuditTransport",
    "build_blockchain_auditor_settings",
    "create_blockchain_auditor_app",
    "generate_event_hash",
    "generate_event_hash_from_payload",
    "validate_audit_metadata",
]


def __getattr__(name: str) -> Any:
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *__all__})
