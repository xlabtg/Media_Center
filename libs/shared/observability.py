from __future__ import annotations

import json
import math
import re
import secrets
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from libs.shared.tenant import TENANT_ID_PATTERN, TenantContext

type JSONValue = (
    None | bool | int | float | str | list["JSONValue"] | dict[str, "JSONValue"]
)
HexIdGenerator = Callable[[int], str]

DEFAULT_METRICS_PATH = "/metrics"
OPERATION_COUNTER = "nmc_service_operations_total"
OPERATION_DURATION = "nmc_service_operation_duration_seconds"

_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_TRACE_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_SPAN_ID_PATTERN = re.compile(r"^[0-9a-f]{16}$")
_TRACE_FLAGS_PATTERN = re.compile(r"^[0-9a-f]{2}$")
_TRACEPARENT_PATTERN = re.compile(
    r"^(?P<version>[0-9a-f]{2})-"
    r"(?P<trace_id>[0-9a-f]{32})-"
    r"(?P<span_id>[0-9a-f]{16})-"
    r"(?P<trace_flags>[0-9a-f]{2})$"
)
_EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_BEARER_PATTERN = re.compile(r"\bbearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
_PRIVATE_KEY_PATTERN = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")

_LOG_LEVELS = frozenset({"debug", "info", "warning", "error", "critical"})
_FORBIDDEN_FIELD_NAMES = frozenset(
    {
        "access_token",
        "address",
        "amount",
        "amount_mcv",
        "api_key",
        "authorization",
        "card",
        "cookie",
        "email",
        "first_name",
        "full_name",
        "inn",
        "last_name",
        "pan",
        "passport",
        "password",
        "patronymic",
        "payout_amount",
        "pdn",
        "personal_data",
        "phone",
        "phone_number",
        "pii",
        "private_key",
        "raw_content",
        "raw_payload",
        "refresh_token",
        "secret",
        "set_cookie",
        "snils",
        "token",
    }
)
_FORBIDDEN_FIELD_FRAGMENTS = frozenset(
    {
        "authorization",
        "password",
        "private_key",
        "secret",
        "token",
    }
)


class ObservabilityPrivacyError(ValueError):
    """Raised when telemetry payloads try to include ПДн, secrets or amounts."""


@dataclass(frozen=True, slots=True)
class ObservabilityContext:
    tenant_id: str
    service_name: str
    operation: str
    correlation_id: str | None = None
    trace_id: str | None = None
    span_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "tenant_id",
            _normalize_tenant_id(self.tenant_id),
        )
        object.__setattr__(
            self,
            "service_name",
            _normalize_token(self.service_name, "service_name"),
        )
        object.__setattr__(
            self,
            "operation",
            _normalize_token(self.operation, "operation"),
        )
        if self.correlation_id is not None:
            object.__setattr__(
                self,
                "correlation_id",
                _normalize_token(self.correlation_id, "correlation_id"),
            )
        if self.trace_id is not None:
            object.__setattr__(self, "trace_id", _normalize_trace_id(self.trace_id))
        if self.span_id is not None:
            object.__setattr__(self, "span_id", _normalize_span_id(self.span_id))

    @classmethod
    def from_tenant_context(
        cls,
        context: TenantContext,
        *,
        service_name: str,
        operation: str,
        trace_id: str | None = None,
        span_id: str | None = None,
    ) -> ObservabilityContext:
        return cls(
            tenant_id=context.tenant_id,
            service_name=service_name,
            operation=operation,
            correlation_id=context.correlation_id,
            trace_id=trace_id,
            span_id=span_id,
        )


@dataclass(frozen=True, slots=True)
class TraceContext:
    trace_id: str
    span_id: str
    trace_flags: str = "01"

    def __post_init__(self) -> None:
        object.__setattr__(self, "trace_id", _normalize_trace_id(self.trace_id))
        object.__setattr__(self, "span_id", _normalize_span_id(self.span_id))
        object.__setattr__(
            self,
            "trace_flags",
            _normalize_trace_flags(self.trace_flags),
        )

    def traceparent(self) -> str:
        return f"00-{self.trace_id}-{self.span_id}-{self.trace_flags}"

    @classmethod
    def from_traceparent(cls, traceparent: str) -> TraceContext:
        match = _TRACEPARENT_PATTERN.fullmatch(traceparent.strip())
        if match is None:
            raise ValueError("traceparent должен соответствовать W3C формату")
        if match.group("version") == "ff":
            raise ValueError("traceparent version ff зарезервирован")

        return cls(
            trace_id=match.group("trace_id"),
            span_id=match.group("span_id"),
            trace_flags=match.group("trace_flags"),
        )

    @classmethod
    def from_headers(cls, headers: Mapping[str, str]) -> TraceContext | None:
        normalized_headers = {key.lower(): value for key, value in headers.items()}
        raw_traceparent = normalized_headers.get("traceparent")
        if raw_traceparent is None:
            return None

        return cls.from_traceparent(raw_traceparent)


@dataclass(frozen=True, slots=True)
class TenantSpan:
    name: str
    trace_id: str
    span_id: str
    parent_span_id: str | None
    trace_flags: str
    attributes: dict[str, str] = field(default_factory=dict)

    def propagation_headers(self) -> dict[str, str]:
        headers = {
            "traceparent": TraceContext(
                trace_id=self.trace_id,
                span_id=self.span_id,
                trace_flags=self.trace_flags,
            ).traceparent(),
            "x-tenant-id": self.attributes["tenant_id"],
        }
        correlation_id = self.attributes.get("correlation_id")
        if correlation_id is not None:
            headers["x-correlation-id"] = correlation_id

        return headers


class TenantTracer:
    """Small deterministic tracer facade for local tests and service wiring."""

    def __init__(self, *, id_generator: HexIdGenerator | None = None) -> None:
        self._id_generator = id_generator or _random_hex_id
        self._spans: list[TenantSpan] = []

    @property
    def spans(self) -> tuple[TenantSpan, ...]:
        return tuple(self._spans)

    def start_span(
        self,
        name: str,
        *,
        context: ObservabilityContext,
        incoming_headers: Mapping[str, str] | None = None,
    ) -> TenantSpan:
        parent = (
            TraceContext.from_headers(incoming_headers)
            if incoming_headers is not None
            else None
        )
        trace_id = parent.trace_id if parent is not None else self._new_trace_id()
        trace_flags = parent.trace_flags if parent is not None else "01"
        span = TenantSpan(
            name=_normalize_token(name, "span name"),
            trace_id=trace_id,
            span_id=self._new_span_id(),
            parent_span_id=parent.span_id if parent is not None else None,
            trace_flags=trace_flags,
            attributes=_trace_attributes(context),
        )
        self._spans.append(span)
        return span

    def _new_trace_id(self) -> str:
        return _normalize_trace_id(self._id_generator(32))

    def _new_span_id(self) -> str:
        return _normalize_span_id(self._id_generator(16))


@dataclass(slots=True)
class _HistogramState:
    buckets: tuple[float, ...]
    bucket_counts: dict[float, int] = field(default_factory=dict)
    count: int = 0
    total: float = 0.0

    def observe(self, value: float) -> None:
        self.count += 1
        self.total += value
        for bucket in self.buckets:
            if value <= bucket:
                self.bucket_counts[bucket] = self.bucket_counts.get(bucket, 0) + 1


@dataclass(frozen=True, slots=True)
class _MetricKey:
    labels: tuple[tuple[str, str], ...]


class TenantMetricRegistry:
    """In-memory Prometheus registry with mandatory tenant-aware labels."""

    def __init__(
        self,
        *,
        buckets: Sequence[float] = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0),
    ) -> None:
        normalized_buckets = tuple(
            sorted(_positive_float(bucket) for bucket in buckets)
        )
        if len(normalized_buckets) == 0:
            raise ValueError("buckets должен содержать хотя бы одно значение")

        self._buckets = normalized_buckets
        self._counters: dict[_MetricKey, int] = {}
        self._histograms: dict[_MetricKey, _HistogramState] = {}

    def record_operation(
        self,
        *,
        context: ObservabilityContext,
        status: str,
        duration_seconds: float,
    ) -> None:
        labels = _metric_labels(context=context, status=status)
        key = _MetricKey(labels=labels)
        self._counters[key] = self._counters.get(key, 0) + 1
        histogram = self._histograms.get(key)
        if histogram is None:
            histogram = _HistogramState(buckets=self._buckets)
            self._histograms[key] = histogram

        histogram.observe(_non_negative_float(duration_seconds))

    def export_prometheus(self) -> str:
        lines = [
            "# HELP nmc_service_operations_total Tenant-scoped service operations.",
            "# TYPE nmc_service_operations_total counter",
        ]
        for key, value in sorted(self._counters.items(), key=_sort_metric_item):
            lines.append(f"{OPERATION_COUNTER}{_format_labels(key.labels)} {value}")

        lines.extend(
            [
                (
                    "# HELP nmc_service_operation_duration_seconds "
                    "Tenant-scoped service operation duration."
                ),
                "# TYPE nmc_service_operation_duration_seconds histogram",
            ]
        )
        for key, histogram in sorted(self._histograms.items(), key=_sort_metric_item):
            for bucket in histogram.buckets:
                bucket_labels = (*key.labels, ("le", _format_metric_value(bucket)))
                lines.append(
                    f"{OPERATION_DURATION}_bucket{_format_labels(bucket_labels)} "
                    f"{histogram.bucket_counts.get(bucket, 0)}"
                )
            inf_labels = (*key.labels, ("le", "+Inf"))
            lines.append(
                f"{OPERATION_DURATION}_bucket{_format_labels(inf_labels)} "
                f"{histogram.count}"
            )
            lines.append(
                f"{OPERATION_DURATION}_sum{_format_labels(key.labels)} "
                f"{_format_metric_value(histogram.total)}"
            )
            lines.append(
                f"{OPERATION_DURATION}_count{_format_labels(key.labels)} "
                f"{histogram.count}"
            )

        return "\n".join(lines) + "\n"


def build_structured_log_entry(
    *,
    level: str,
    message: str,
    context: ObservabilityContext,
    payload: Mapping[str, object] | None = None,
    timestamp: datetime | None = None,
) -> dict[str, JSONValue]:
    normalized_level = level.strip().lower()
    if normalized_level not in _LOG_LEVELS:
        raise ValueError("level должен быть debug, info, warning, error или critical")

    _assert_safe_string_value(message, path="message")
    occurred_at = timestamp or datetime.now(UTC)
    entry: dict[str, JSONValue] = {
        "timestamp": occurred_at.astimezone(UTC).isoformat(),
        "level": normalized_level,
        "message": message,
        "tenant_id": context.tenant_id,
        "service": context.service_name,
        "operation": context.operation,
        "payload": sanitize_observability_payload(payload or {}),
    }
    if context.correlation_id is not None:
        entry["correlation_id"] = context.correlation_id
    if context.trace_id is not None:
        entry["trace_id"] = context.trace_id
    if context.span_id is not None:
        entry["span_id"] = context.span_id

    return entry


def format_structured_log(entry: Mapping[str, JSONValue]) -> str:
    return json.dumps(
        dict(entry),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def sanitize_observability_payload(
    payload: Mapping[str, object],
) -> dict[str, JSONValue]:
    sanitized: dict[str, JSONValue] = {}
    for key, value in payload.items():
        normalized_key = _normalize_payload_key(key)
        _assert_safe_field_name(normalized_key)
        sanitized[normalized_key] = _sanitize_json_value(
            value,
            path=normalized_key,
        )

    return sanitized


def observability_context_from_tenant_context(
    context: TenantContext,
    *,
    service_name: str,
    operation: str,
    trace_id: str | None = None,
    span_id: str | None = None,
) -> ObservabilityContext:
    return ObservabilityContext.from_tenant_context(
        context,
        service_name=service_name,
        operation=operation,
        trace_id=trace_id,
        span_id=span_id,
    )


def _metric_labels(
    *,
    context: ObservabilityContext,
    status: str,
) -> tuple[tuple[str, str], ...]:
    normalized_status = _normalize_token(status, "status")
    labels = {
        "operation": context.operation,
        "service": context.service_name,
        "status": normalized_status,
        "tenant_id": context.tenant_id,
    }
    for name, value in labels.items():
        _assert_safe_field_name(name)
        _assert_safe_string_value(value, path=f"label.{name}")

    return tuple(sorted(labels.items()))


def _trace_attributes(context: ObservabilityContext) -> dict[str, str]:
    attributes = {
        "tenant_id": context.tenant_id,
        "service.name": context.service_name,
        "operation": context.operation,
    }
    if context.correlation_id is not None:
        attributes["correlation_id"] = context.correlation_id

    return attributes


def _sort_metric_item(item: tuple[_MetricKey, object]) -> tuple[tuple[str, str], ...]:
    return item[0].labels


def _format_labels(labels: Sequence[tuple[str, str]]) -> str:
    if len(labels) == 0:
        return ""

    rendered = ",".join(
        f'{name}="{_escape_prometheus_label(value)}"' for name, value in labels
    )
    return f"{{{rendered}}}"


def _escape_prometheus_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _format_metric_value(value: float) -> str:
    if value.is_integer():
        return str(int(value))

    return f"{value:.15g}"


def _random_hex_id(length: int) -> str:
    if length <= 0 or length % 2 != 0:
        raise ValueError("length должен быть положительным чётным числом")

    return secrets.token_hex(length // 2)


def _normalize_tenant_id(value: str) -> str:
    normalized = value.strip()
    if TENANT_ID_PATTERN.fullmatch(normalized) is None:
        raise ValueError("tenant_id имеет недопустимый формат")

    return normalized


def _normalize_token(value: str, field_name: str) -> str:
    normalized = value.strip()
    if _TOKEN_PATTERN.fullmatch(normalized) is None:
        raise ValueError(f"{field_name} имеет недопустимый формат")

    return normalized


def _normalize_trace_id(value: str) -> str:
    normalized = value.strip().lower()
    if _TRACE_ID_PATTERN.fullmatch(normalized) is None or normalized == "0" * 32:
        raise ValueError("trace_id должен быть 32 hex символами и не all-zero")

    return normalized


def _normalize_span_id(value: str) -> str:
    normalized = value.strip().lower()
    if _SPAN_ID_PATTERN.fullmatch(normalized) is None or normalized == "0" * 16:
        raise ValueError("span_id должен быть 16 hex символами и не all-zero")

    return normalized


def _normalize_trace_flags(value: str) -> str:
    normalized = value.strip().lower()
    if _TRACE_FLAGS_PATTERN.fullmatch(normalized) is None:
        raise ValueError("trace_flags должен быть 2 hex символами")

    return normalized


def _normalize_payload_key(value: object) -> str:
    if not isinstance(value, str):
        raise ObservabilityPrivacyError("telemetry payload key должен быть строкой")

    normalized = value.strip()
    if _TOKEN_PATTERN.fullmatch(normalized) is None:
        raise ObservabilityPrivacyError(
            f"telemetry payload key {value!r} имеет недопустимый формат"
        )

    return normalized


def _assert_safe_field_name(field_name: str) -> None:
    normalized = field_name.lower().replace("-", "_").replace(".", "_")
    if normalized in _FORBIDDEN_FIELD_NAMES:
        raise ObservabilityPrivacyError(
            f"telemetry field {field_name!r} запрещён для логов и метрик"
        )
    if any(fragment in normalized for fragment in _FORBIDDEN_FIELD_FRAGMENTS):
        raise ObservabilityPrivacyError(
            f"telemetry field {field_name!r} может содержать секреты"
        )


def _sanitize_json_value(value: object, *, path: str) -> JSONValue:
    if value is None or isinstance(value, bool | int | str):
        if isinstance(value, str):
            _assert_safe_string_value(value, path=path)
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ObservabilityPrivacyError(f"{path} должен быть finite float")
        return value
    if isinstance(value, Mapping):
        nested: dict[str, JSONValue] = {}
        for nested_key, nested_value in value.items():
            normalized_key = _normalize_payload_key(nested_key)
            _assert_safe_field_name(normalized_key)
            nested[normalized_key] = _sanitize_json_value(
                nested_value,
                path=f"{path}.{normalized_key}",
            )
        return nested
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_sanitize_json_value(item, path=f"{path}[]") for item in value]

    raise ObservabilityPrivacyError(
        f"{path} должен быть JSON-совместимым значением без ПДн"
    )


def _assert_safe_string_value(value: str, *, path: str) -> None:
    if _EMAIL_PATTERN.search(value) is not None:
        raise ObservabilityPrivacyError(f"{path} содержит email")
    if _BEARER_PATTERN.search(value) is not None:
        raise ObservabilityPrivacyError(f"{path} содержит bearer token")
    if _PRIVATE_KEY_PATTERN.search(value) is not None:
        raise ObservabilityPrivacyError(f"{path} содержит private key")


def _positive_float(value: float) -> float:
    normalized = float(value)
    if not math.isfinite(normalized) or normalized <= 0:
        raise ValueError("bucket должен быть положительным finite float")

    return normalized


def _non_negative_float(value: float) -> float:
    normalized = float(value)
    if not math.isfinite(normalized) or normalized < 0:
        raise ValueError("duration_seconds должен быть неотрицательным finite float")

    return normalized
