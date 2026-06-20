from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, cast
from urllib.parse import quote

import httpx
from pydantic import Field, SecretStr, ValidationError, field_validator

from hitl_payout_gateway.execution_manager import (
    PayoutConnectorError,
    PayoutPaymentCommand,
    PayoutPaymentResult,
    PayoutPaymentStatusCommand,
    PayoutPaymentStatusResult,
)
from hitl_payout_gateway.queue_manager import PayoutPaymentStatus
from libs.shared.models import JSONValue, SharedBaseModel

PaymentRails = Literal["sbp", "bank_card", "bank_account"]

DEFAULT_RF_STATUS_MAPPING: dict[str, PayoutPaymentStatus] = {
    "accepted": PayoutPaymentStatus.ACCEPTED,
    "created": PayoutPaymentStatus.ACCEPTED,
    "queued": PayoutPaymentStatus.ACCEPTED,
    "pending": PayoutPaymentStatus.PROCESSING,
    "processing": PayoutPaymentStatus.PROCESSING,
    "in_progress": PayoutPaymentStatus.PROCESSING,
    "done": PayoutPaymentStatus.SUCCEEDED,
    "paid": PayoutPaymentStatus.SUCCEEDED,
    "success": PayoutPaymentStatus.SUCCEEDED,
    "succeeded": PayoutPaymentStatus.SUCCEEDED,
    "declined": PayoutPaymentStatus.FAILED,
    "failed": PayoutPaymentStatus.FAILED,
    "rejected": PayoutPaymentStatus.FAILED,
    "returned": PayoutPaymentStatus.RETURNED,
    "return": PayoutPaymentStatus.RETURNED,
    "refunded": PayoutPaymentStatus.REFUNDED,
    "refund": PayoutPaymentStatus.REFUNDED,
}
DEFAULT_RETRYABLE_ERROR_CODES = (
    "bank_unavailable",
    "gateway_timeout",
    "payment_unavailable",
    "rate_limited",
    "temporarily_unavailable",
)


class RFPayoutGatewayConfig(SharedBaseModel):
    provider: str = Field(
        default="rf_payment_gateway",
        pattern=r"^[a-z][a-z0-9_-]{0,63}$",
    )
    base_url: str = Field(min_length=1, max_length=2048)
    merchant_id: str = Field(min_length=1, max_length=128)
    api_key: SecretStr
    execute_path: str = Field(default="/payouts", min_length=1, max_length=256)
    status_path_template: str = Field(
        default="/payouts/{payment_id}",
        min_length=1,
        max_length=256,
    )
    auth_header: str = Field(default="Authorization", min_length=1, max_length=128)
    auth_scheme: str = Field(default="Bearer", min_length=1, max_length=64)
    idempotency_header: str = Field(
        default="Idempotency-Key",
        min_length=1,
        max_length=128,
    )
    timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    status_mapping: dict[str, PayoutPaymentStatus] = Field(
        default_factory=lambda: dict(DEFAULT_RF_STATUS_MAPPING),
        min_length=1,
    )
    retryable_error_codes: tuple[str, ...] = DEFAULT_RETRYABLE_ERROR_CODES

    @field_validator("base_url")
    @classmethod
    def _normalize_base_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        if normalized == "":
            raise ValueError("base_url должен быть непустой строкой")
        if not normalized.startswith(("http://", "https://")):
            raise ValueError("base_url должен использовать http:// или https://")

        return normalized

    @field_validator("execute_path", "status_path_template")
    @classmethod
    def _normalize_path(cls, value: str) -> str:
        normalized = value.strip()
        if normalized == "":
            raise ValueError("path должен быть непустой строкой")
        if not normalized.startswith("/"):
            normalized = f"/{normalized}"

        return normalized

    @field_validator("retryable_error_codes", mode="before")
    @classmethod
    def _normalize_retryable_codes(cls, value: object) -> object:
        if isinstance(value, str):
            return (value.strip().lower(),)
        if isinstance(value, list | tuple):
            return tuple(
                item.strip().lower()
                for item in value
                if isinstance(item, str) and item.strip() != ""
            )

        return value

    @field_validator("status_mapping", mode="before")
    @classmethod
    def _normalize_status_mapping(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value

        return {
            str(raw_status).strip().lower(): status
            for raw_status, status in value.items()
            if str(raw_status).strip() != ""
        }


class RFPayoutPaymentDetails(SharedBaseModel):
    amount_minor: int = Field(gt=0)
    currency: Literal["RUB"] = "RUB"
    recipient_token: str = Field(min_length=1, max_length=256)
    rails: PaymentRails = "sbp"
    purpose: str | None = Field(default=None, min_length=1, max_length=256)


@dataclass(slots=True)
class RFPayoutGatewayConnector:
    config: RFPayoutGatewayConfig
    client: httpx.AsyncClient | None = None
    connector_name: str = "rf_payment_gateway"

    def __post_init__(self) -> None:
        if self.connector_name == "rf_payment_gateway":
            self.connector_name = self.config.provider

    async def execute_payout(
        self,
        command: PayoutPaymentCommand,
    ) -> PayoutPaymentResult:
        details = _payment_details_from(command.metadata)
        response = await self._request(
            method="POST",
            path=self.config.execute_path,
            headers=_execution_headers(config=self.config, command=command),
            json_payload=_execution_payload(
                config=self.config,
                command=command,
                details=details,
            ),
        )
        payload = response_json_object(response)
        _raise_for_response_error(
            response=response,
            payload=payload,
            config=self.config,
            connector_name=self.connector_name,
        )
        payment_id = _string_field(
            payload,
            "payment_id",
            "payout_id",
            "transaction_id",
            "id",
        )
        if payment_id is None:
            raise PayoutConnectorError(
                "РФ-шлюз вернул ответ без payment_id",
                connector_name=self.connector_name,
                error_code="invalid_response",
                retryable=True,
            )

        return PayoutPaymentResult(
            execution_ref=f"{self.config.provider}:{payment_id}",
            connector_name=self.connector_name,
            executed_at=_datetime_field(
                payload,
                "accepted_at",
                "created_at",
                "executed_at",
                fallback=command.requested_at,
            ),
            gateway_payment_id=payment_id,
            gateway_status=_status_from_payload(
                payload,
                config=self.config,
                connector_name=self.connector_name,
            ),
        )

    async def sync_payout_status(
        self,
        command: PayoutPaymentStatusCommand,
    ) -> PayoutPaymentStatusResult:
        response = await self._request(
            method="GET",
            path=self.config.status_path_template.format(
                payment_id=quote(command.payment_gateway_id, safe="")
            ),
            headers=_status_headers(config=self.config, command=command),
            json_payload=None,
        )
        payload = response_json_object(response)
        _raise_for_response_error(
            response=response,
            payload=payload,
            config=self.config,
            connector_name=self.connector_name,
        )
        payment_id = _string_field(
            payload,
            "payment_id",
            "payout_id",
            "transaction_id",
            "id",
        )
        status = _status_from_payload(
            payload,
            config=self.config,
            connector_name=self.connector_name,
        )

        return PayoutPaymentStatusResult(
            payment_gateway_id=payment_id or command.payment_gateway_id,
            status=status,
            connector_name=self.connector_name,
            synced_at=_datetime_field(
                payload,
                "synced_at",
                "updated_at",
                "created_at",
                fallback=command.requested_at,
            ),
            error_code=_string_field(payload, "error_code", "reason_code"),
            retryable=status
            in {
                PayoutPaymentStatus.ACCEPTED,
                PayoutPaymentStatus.PROCESSING,
            },
            refund_id=_string_field(payload, "refund_id", "return_id"),
        )

    async def _request(
        self,
        *,
        method: str,
        path: str,
        headers: Mapping[str, str],
        json_payload: Mapping[str, JSONValue] | None,
    ) -> httpx.Response:
        url = f"{self.config.base_url}{path}"
        try:
            if self.client is not None:
                return await self.client.request(
                    method,
                    url,
                    headers=dict(headers),
                    json=json_payload,
                )

            async with httpx.AsyncClient(
                timeout=self.config.timeout_seconds,
            ) as client:
                return await client.request(
                    method,
                    url,
                    headers=dict(headers),
                    json=json_payload,
                )
        except httpx.TimeoutException as error:
            raise PayoutConnectorError(
                "РФ-шлюз не ответил до таймаута",
                connector_name=self.connector_name,
                error_code="gateway_timeout",
                retryable=True,
            ) from error
        except httpx.HTTPError as error:
            raise PayoutConnectorError(
                "Не удалось выполнить запрос к РФ-шлюзу",
                connector_name=self.connector_name,
                error_code="gateway_http_error",
                retryable=True,
            ) from error


def response_json_object(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        return {}

    if not isinstance(payload, dict):
        return {}

    return cast(dict[str, Any], payload)


def _payment_details_from(metadata: Mapping[str, JSONValue]) -> RFPayoutPaymentDetails:
    payment_metadata = metadata.get("payment")
    if not isinstance(payment_metadata, dict):
        raise PayoutConnectorError(
            "Для РФ-шлюза нужно передать metadata.payment",
            connector_name="rf_payment_gateway",
            error_code="payment_metadata_missing",
            retryable=False,
        )

    try:
        return RFPayoutPaymentDetails.model_validate(payment_metadata)
    except ValidationError as error:
        raise PayoutConnectorError(
            "metadata.payment содержит некорректные параметры выплаты",
            connector_name="rf_payment_gateway",
            error_code="payment_metadata_invalid",
            retryable=False,
        ) from error


def _execution_payload(
    *,
    config: RFPayoutGatewayConfig,
    command: PayoutPaymentCommand,
    details: RFPayoutPaymentDetails,
) -> dict[str, JSONValue]:
    payload: dict[str, JSONValue] = {
        "merchant_id": config.merchant_id,
        "payout_id": command.payout_id,
        "execution_id": command.execution_id,
        "amount_minor": details.amount_minor,
        "currency": details.currency,
        "recipient_token": details.recipient_token,
        "rails": details.rails,
        "member_ref_hash": command.member_hash,
        "distribution_hash": command.distribution_hash,
        "correlation_id": command.correlation_id,
    }
    if details.purpose is not None:
        payload["purpose"] = details.purpose

    return payload


def _execution_headers(
    *,
    config: RFPayoutGatewayConfig,
    command: PayoutPaymentCommand,
) -> dict[str, str]:
    return {
        config.auth_header: _auth_header_value(config),
        config.idempotency_header: command.execution_id,
        "X-Correlation-Id": command.correlation_id,
    }


def _status_headers(
    *,
    config: RFPayoutGatewayConfig,
    command: PayoutPaymentStatusCommand,
) -> dict[str, str]:
    return {
        config.auth_header: _auth_header_value(config),
        "X-Correlation-Id": command.correlation_id,
    }


def _auth_header_value(config: RFPayoutGatewayConfig) -> str:
    return f"{config.auth_scheme} {config.api_key.get_secret_value()}"


def _raise_for_response_error(
    *,
    response: httpx.Response,
    payload: Mapping[str, Any],
    config: RFPayoutGatewayConfig,
    connector_name: str,
) -> None:
    if response.status_code < 400:
        return

    error_code = _string_field(payload, "error_code", "code", "error")
    normalized_error_code = (
        error_code.strip().lower()
        if error_code is not None
        else f"http_{response.status_code}"
    )
    raise PayoutConnectorError(
        "РФ-шлюз вернул ошибку исполнения выплаты",
        connector_name=connector_name,
        error_code=normalized_error_code,
        retryable=(
            _is_retryable_status(response.status_code)
            or normalized_error_code in config.retryable_error_codes
        ),
    )


def _status_from_payload(
    payload: Mapping[str, Any],
    *,
    config: RFPayoutGatewayConfig,
    connector_name: str,
) -> PayoutPaymentStatus:
    raw_status = _string_field(payload, "status", "payment_status", "state")
    if raw_status is None:
        raise PayoutConnectorError(
            "РФ-шлюз вернул ответ без статуса платежа",
            connector_name=connector_name,
            error_code="invalid_response",
            retryable=True,
        )

    normalized_status = raw_status.strip().lower()
    status = config.status_mapping.get(normalized_status)
    if status is None:
        raise PayoutConnectorError(
            "РФ-шлюз вернул неизвестный статус платежа",
            connector_name=connector_name,
            error_code="unknown_payment_status",
            retryable=True,
        )

    return status


def _string_field(payload: Mapping[str, Any], *names: str) -> str | None:
    for name in names:
        value = payload.get(name)
        if isinstance(value, bool) or value is None:
            continue
        if isinstance(value, int | float | str):
            normalized = str(value).strip()
            if normalized != "":
                return normalized

    return None


def _datetime_field(
    payload: Mapping[str, Any],
    *names: str,
    fallback: datetime,
) -> datetime:
    raw_value = _string_field(payload, *names)
    if raw_value is None:
        return _normalize_datetime(fallback)

    try:
        return _normalize_datetime(raw_value)
    except ValueError:
        return _normalize_datetime(fallback)


def _normalize_datetime(value: datetime | str) -> datetime:
    if isinstance(value, str):
        normalized = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        normalized = value

    if normalized.tzinfo is None:
        normalized = normalized.replace(tzinfo=UTC)

    return normalized.astimezone(UTC)


def _is_retryable_status(status_code: int) -> bool:
    return status_code in {408, 425, 429, 500, 502, 503, 504}
