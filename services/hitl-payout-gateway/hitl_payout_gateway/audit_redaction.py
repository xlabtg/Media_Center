from __future__ import annotations

from collections.abc import Mapping

from libs.shared.models import JSONValue

_SENSITIVE_METADATA_KEYS = frozenset(
    {
        "access_token",
        "amount",
        "amount_mcv",
        "amount_minor",
        "amount_rub",
        "api_key",
        "bank_account",
        "card",
        "card_number",
        "email",
        "pan",
        "password",
        "payout_amount",
        "phone",
        "raw_content",
        "recipient",
        "recipient_id",
        "recipient_token",
        "refresh_token",
        "secret",
        "source_content",
        "token",
        "transcript",
        "voice",
    }
)


def audit_safe_metadata(metadata: Mapping[str, JSONValue]) -> dict[str, JSONValue]:
    safe: dict[str, JSONValue] = {}
    redacted_count = 0

    for key, value in metadata.items():
        normalized_key = key.strip().lower()
        if normalized_key == "payment" and isinstance(value, dict):
            safe[key] = _payment_metadata_summary(value)
            continue
        if _is_sensitive_metadata_key(normalized_key):
            redacted_count += 1
            continue

        safe[key] = _audit_safe_metadata_value(value)

    if redacted_count > 0:
        safe["redacted_metadata_fields"] = redacted_count

    return safe


def _audit_safe_metadata_value(value: JSONValue) -> JSONValue:
    if isinstance(value, dict):
        return audit_safe_metadata(value)
    if isinstance(value, list):
        return [_audit_safe_metadata_value(item) for item in value]

    return value


def _payment_metadata_summary(payment: Mapping[str, JSONValue]) -> dict[str, JSONValue]:
    summary: dict[str, JSONValue] = {"present": True}
    currency = _string_value(payment.get("currency"))
    rails = _string_value(payment.get("rails"))
    if currency is not None:
        summary["currency"] = currency
    if rails is not None:
        summary["rails"] = rails

    return summary


def _is_sensitive_metadata_key(normalized_key: str) -> bool:
    if normalized_key in _SENSITIVE_METADATA_KEYS:
        return True

    return normalized_key.endswith(("_token", "_secret", "_password", "_email"))


def _string_value(value: JSONValue | None) -> str | None:
    if not isinstance(value, str):
        return None

    normalized = value.strip()
    if normalized == "":
        return None

    return normalized
