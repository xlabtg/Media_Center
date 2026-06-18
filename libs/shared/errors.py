from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, cast

from pydantic import Field

from libs.shared.models import CorrelationId, SharedBaseModel

VALIDATION_ERROR_CODE = "validation_error"
UNAUTHORIZED_CODE = "unauthorized"
FORBIDDEN_CODE = "forbidden"
TENANT_ISOLATION_CODE = "tenant_isolation_violation"
IDEMPOTENCY_CONFLICT_CODE = "idempotency_conflict"
POLICY_GATE_REQUIRED_CODE = "policy_gate_required"
RATE_LIMITED_CODE = "rate_limited"
SERVICE_NOT_FOUND_CODE = "service_not_found"

ErrorCode = Annotated[
    str,
    Field(
        min_length=2,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_]*$",
    ),
]


class ErrorBody(SharedBaseModel):
    code: ErrorCode
    message: str = Field(min_length=1)
    details: dict[str, object] = Field(default_factory=dict)
    correlation_id: CorrelationId | None = None


class ErrorEnvelope(SharedBaseModel):
    error: ErrorBody

    @classmethod
    def from_error(
        cls,
        *,
        code: str,
        message: str,
        details: Mapping[str, object] | None = None,
        correlation_id: str | None = None,
    ) -> ErrorEnvelope:
        return cls(
            error=ErrorBody(
                code=code,
                message=message,
                details=dict(details or {}),
                correlation_id=correlation_id,
            )
        )

    def to_response_body(self) -> dict[str, object]:
        return cast(dict[str, object], self.model_dump(mode="json"))


class SharedError(Exception):
    """Base exception for errors that render to the shared error envelope."""

    def __init__(
        self,
        *,
        status_code: int,
        error_code: str,
        message: str,
        details: Mapping[str, object] | None = None,
        correlation_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.message = message
        self.details = dict(details or {})
        self.correlation_id = correlation_id

    def to_envelope(self) -> ErrorEnvelope:
        return ErrorEnvelope.from_error(
            code=self.error_code,
            message=self.message,
            details=self.details,
            correlation_id=self.correlation_id,
        )

    def to_response_body(self) -> dict[str, object]:
        return self.to_envelope().to_response_body()


def error_response_body(
    *,
    code: str,
    message: str,
    details: Mapping[str, object] | None = None,
    correlation_id: str | None = None,
) -> dict[str, object]:
    return ErrorEnvelope.from_error(
        code=code,
        message=message,
        details=details,
        correlation_id=correlation_id,
    ).to_response_body()
