from __future__ import annotations

import html
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, cast

from fastapi import APIRouter, Depends, FastAPI, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import Field
from wallet import (
    InMemoryWalletRepository,
    WalletBalance,
    WalletBalanceResponse,
    WalletOperationRecord,
    WalletOperationResponse,
    WalletOperationType,
)

from libs.shared import (
    BOARD_ROLE,
    COUNCIL_ROLE,
    MEMBER_ASSOC_ROLE,
    MEMBER_FULL_ROLE,
    PRESIDIUM_ROLE,
    VALIDATION_ERROR_CODE,
    AccessPolicy,
    InMemoryAuditSink,
    ServiceTemplateConfig,
    SharedBaseModel,
    SharedError,
    SubjectId,
    TenantContext,
    TenantCoreError,
    TenantId,
    TenantScopedRepository,
    create_service_app,
    error_response_body,
    require_access,
    require_tenant_context,
)

WEB_CABINET_SERVICE_NAME = "web-cabinet"

_PERIOD_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"
_REFERRAL_LEVEL_PATTERN = r"^L[1-3]$"
_PLATFORM_TARGET_PATTERN = r"^[a-z][a-z0-9_-]{0,63}$"

WEB_CABINET_READ_POLICY = AccessPolicy.allow_roles(
    COUNCIL_ROLE,
    PRESIDIUM_ROLE,
    BOARD_ROLE,
    MEMBER_FULL_ROLE,
    MEMBER_ASSOC_ROLE,
    action="web_cabinet.read",
    resource_type="web_cabinet",
)
WEB_CABINET_GOVERNANCE_READ_POLICY = AccessPolicy.allow_roles(
    COUNCIL_ROLE,
    PRESIDIUM_ROLE,
    BOARD_ROLE,
    action="web_cabinet.member.read",
    resource_type="web_cabinet",
)


class CabinetReferralLink(SharedBaseModel):
    level: str = Field(pattern=_REFERRAL_LEVEL_PATTERN)
    owner_id: SubjectId
    url: str = Field(min_length=1, max_length=2048)
    reward_share: float = Field(ge=0, le=1, allow_inf_nan=False)


class CabinetContributionSummary(SharedBaseModel):
    member_id: SubjectId
    period: str = Field(pattern=_PERIOD_PATTERN)
    total_points: float = Field(ge=0, allow_inf_nan=False)
    avg_points_council: float = Field(ge=0, allow_inf_nan=False)
    kv_raw: float = Field(ge=0, allow_inf_nan=False)
    kv_capped: float = Field(ge=0, allow_inf_nan=False)
    payout_share: float = Field(ge=0, le=1, allow_inf_nan=False)
    contribution_count: int = Field(ge=0)


class CabinetContentItem(SharedBaseModel):
    content_id: str = Field(min_length=1, max_length=128)
    template_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=256)
    preview: str = Field(min_length=1, max_length=500)
    content_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    platform_targets: tuple[
        Annotated[str, Field(pattern=_PLATFORM_TARGET_PATTERN)],
        ...,
    ] = Field(default_factory=tuple)
    points_awarded: float = Field(ge=0, allow_inf_nan=False)
    created_at: datetime


class WebCabinetOverviewResponse(SharedBaseModel):
    tenant_id: TenantId
    member_id: SubjectId
    period: str = Field(pattern=_PERIOD_PATTERN)
    contribution: CabinetContributionSummary
    balance: WalletBalanceResponse
    operations: tuple[WalletOperationResponse, ...]
    content: tuple[CabinetContentItem, ...]
    referral_links: tuple[CabinetReferralLink, ...]
    generated_at: datetime


@dataclass(frozen=True, slots=True)
class CabinetContributionRecord:
    tenant_id: str
    member_id: str
    period: str
    total_points: float
    avg_points_council: float
    kv_raw: float
    kv_capped: float
    payout_share: float
    contribution_count: int


@dataclass(frozen=True, slots=True)
class CabinetContentRecord:
    tenant_id: str
    owner_id: str
    content_id: str
    template_id: str
    title: str
    preview: str
    content_hash: str
    platform_targets: tuple[str, ...]
    referral_links: tuple[CabinetReferralLink, ...]
    points_awarded: float
    created_at: datetime


@dataclass(slots=True)
class InMemoryWebCabinetRepository:
    _contributions: dict[tuple[str, str, str], CabinetContributionRecord] = field(
        default_factory=dict
    )
    _content: list[CabinetContentRecord] = field(default_factory=list)
    _contribution_guard: TenantScopedRepository[CabinetContributionRecord] = field(
        default_factory=lambda: TenantScopedRepository("cabinet_contributions")
    )
    _content_guard: TenantScopedRepository[CabinetContentRecord] = field(
        default_factory=lambda: TenantScopedRepository("cabinet_content")
    )

    def save_contribution(
        self,
        record: CabinetContributionRecord,
    ) -> CabinetContributionRecord:
        self._contributions[(record.tenant_id, record.member_id, record.period)] = (
            record
        )
        return record

    def get_contribution(
        self,
        *,
        context: TenantContext,
        member_id: str,
        period: str,
    ) -> CabinetContributionRecord | None:
        records = self._contribution_guard.list_for_tenant(
            self._contributions.values(),
            context,
        )
        for record in records:
            if record.member_id == member_id and record.period == period:
                return record

        return None

    def add_content(self, record: CabinetContentRecord) -> CabinetContentRecord:
        self._content.append(record)
        return record

    def list_content(
        self,
        *,
        context: TenantContext,
        owner_id: str,
        limit: int = 20,
    ) -> tuple[CabinetContentRecord, ...]:
        records = self._content_guard.list_for_tenant(self._content, context)
        filtered = (record for record in records if record.owner_id == owner_id)
        sorted_records = sorted(
            filtered,
            key=lambda record: (record.created_at, record.content_id),
            reverse=True,
        )
        return tuple(sorted_records[:limit])

    def list_referral_links(
        self,
        *,
        context: TenantContext,
        owner_id: str,
        limit: int = 20,
    ) -> tuple[CabinetReferralLink, ...]:
        links: list[CabinetReferralLink] = []
        seen: set[tuple[str, str, str]] = set()
        for record in self.list_content(
            context=context,
            owner_id=owner_id,
            limit=limit,
        ):
            for link in record.referral_links:
                key = (link.level, link.owner_id, link.url)
                if key in seen:
                    continue
                seen.add(key)
                links.append(link)

        return tuple(links)


@dataclass(slots=True)
class WebCabinetAPIState:
    repository: InMemoryWebCabinetRepository
    wallet_repository: InMemoryWalletRepository
    tenant_audit_sink: InMemoryAuditSink


router = APIRouter(tags=["Web Cabinet"])


def create_web_cabinet_app(
    config: ServiceTemplateConfig,
    *,
    repository: InMemoryWebCabinetRepository | None = None,
    wallet_repository: InMemoryWalletRepository | None = None,
    tenant_audit_sink: InMemoryAuditSink | None = None,
) -> FastAPI:
    resolved_tenant_audit_sink = tenant_audit_sink or InMemoryAuditSink()
    app = create_service_app(
        config,
        title="Media Center Web Cabinet",
        audit_sink=resolved_tenant_audit_sink,
    )
    app.state.web_cabinet_api = WebCabinetAPIState(
        repository=repository or InMemoryWebCabinetRepository(),
        wallet_repository=wallet_repository or InMemoryWalletRepository(),
        tenant_audit_sink=resolved_tenant_audit_sink,
    )
    app.add_exception_handler(TenantCoreError, _tenant_core_error_handler)
    app.add_exception_handler(SharedError, _shared_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.include_router(router)
    return app


@router.get(
    "/cabinet/overview",
    response_model=WebCabinetOverviewResponse,
    summary="Получить личный обзор кабинета пайщика",
)
def get_cabinet_overview(
    state: Annotated[WebCabinetAPIState, Depends(_api_state)],
    context: Annotated[TenantContext, Depends(_tenant_context)],
    period: Annotated[str, Query(pattern=_PERIOD_PATTERN)],
    member_id: Annotated[SubjectId | None, Query()] = None,
    operations_limit: Annotated[int, Query(ge=1, le=100)] = 20,
    content_limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> WebCabinetOverviewResponse:
    target_member_id = _target_member_id(context, member_id)
    _ensure_cabinet_read_allowed(context, target_member_id)
    return _build_overview(
        state=state,
        context=context,
        member_id=target_member_id,
        period=period,
        operations_limit=operations_limit,
        content_limit=content_limit,
    )


@router.get(
    "/cabinet",
    response_class=HTMLResponse,
    summary="Открыть адаптивный личный кабинет пайщика",
)
def get_cabinet_page(
    state: Annotated[WebCabinetAPIState, Depends(_api_state)],
    context: Annotated[TenantContext, Depends(_tenant_context)],
    period: Annotated[str, Query(pattern=_PERIOD_PATTERN)],
    member_id: Annotated[SubjectId | None, Query()] = None,
    operations_limit: Annotated[int, Query(ge=1, le=100)] = 20,
    content_limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> HTMLResponse:
    target_member_id = _target_member_id(context, member_id)
    _ensure_cabinet_read_allowed(context, target_member_id)
    overview = _build_overview(
        state=state,
        context=context,
        member_id=target_member_id,
        period=period,
        operations_limit=operations_limit,
        content_limit=content_limit,
    )
    return HTMLResponse(_render_cabinet_html(overview))


def _build_overview(
    *,
    state: WebCabinetAPIState,
    context: TenantContext,
    member_id: str,
    period: str,
    operations_limit: int,
    content_limit: int,
) -> WebCabinetOverviewResponse:
    contribution = state.repository.get_contribution(
        context=context,
        member_id=member_id,
        period=period,
    )
    balance = state.wallet_repository.balance_for_member(
        context=context,
        member_id=member_id,
    )
    operations = state.wallet_repository.list_operations(
        context=context,
        member_id=member_id,
        limit=operations_limit,
    )
    content = state.repository.list_content(
        context=context,
        owner_id=member_id,
        limit=content_limit,
    )
    referral_links = state.repository.list_referral_links(
        context=context,
        owner_id=member_id,
        limit=content_limit,
    )
    return WebCabinetOverviewResponse(
        tenant_id=context.tenant_id,
        member_id=member_id,
        period=period,
        contribution=_contribution_summary(
            contribution,
            member_id=member_id,
            period=period,
        ),
        balance=_wallet_balance_response(balance),
        operations=tuple(_wallet_operation_response(record) for record in operations),
        content=tuple(_content_item(record) for record in content),
        referral_links=referral_links,
        generated_at=datetime.now(UTC),
    )


def _api_state(request: Request) -> WebCabinetAPIState:
    return cast(WebCabinetAPIState, request.app.state.web_cabinet_api)


def _tenant_context() -> TenantContext:
    return require_tenant_context()


def _target_member_id(context: TenantContext, member_id: str | None) -> str:
    if member_id is not None:
        return member_id
    if context.subject is None or context.subject.strip() == "":
        raise SharedError(
            status_code=400,
            error_code="actor_required",
            message="Кабинет пайщика требует subject в tenant context",
            correlation_id=context.correlation_id,
        )

    return context.subject


def _ensure_cabinet_read_allowed(context: TenantContext, member_id: str) -> None:
    require_access(WEB_CABINET_READ_POLICY, context=context)
    if context.subject == member_id:
        return

    require_access(WEB_CABINET_GOVERNANCE_READ_POLICY, context=context)


def _contribution_summary(
    record: CabinetContributionRecord | None,
    *,
    member_id: str,
    period: str,
) -> CabinetContributionSummary:
    if record is None:
        return CabinetContributionSummary(
            member_id=member_id,
            period=period,
            total_points=0.0,
            avg_points_council=0.0,
            kv_raw=0.0,
            kv_capped=0.0,
            payout_share=0.0,
            contribution_count=0,
        )

    return CabinetContributionSummary(
        member_id=record.member_id,
        period=record.period,
        total_points=record.total_points,
        avg_points_council=record.avg_points_council,
        kv_raw=record.kv_raw,
        kv_capped=record.kv_capped,
        payout_share=record.payout_share,
        contribution_count=record.contribution_count,
    )


def _content_item(record: CabinetContentRecord) -> CabinetContentItem:
    return CabinetContentItem(
        content_id=record.content_id,
        template_id=record.template_id,
        title=record.title,
        preview=record.preview,
        content_hash=record.content_hash,
        platform_targets=record.platform_targets,
        points_awarded=record.points_awarded,
        created_at=record.created_at,
    )


def _wallet_balance_response(balance: WalletBalance) -> WalletBalanceResponse:
    return WalletBalanceResponse(
        tenant_id=balance.tenant_id,
        member_id=balance.member_id,
        balance_mcv=balance.balance_mcv,
        credited_mcv=balance.credited_mcv,
        debited_mcv=balance.debited_mcv,
        operation_count=balance.operation_count,
    )


def _wallet_operation_response(
    record: WalletOperationRecord,
) -> WalletOperationResponse:
    return WalletOperationResponse(
        operation_id=record.operation_id,
        tenant_id=record.tenant_id,
        member_id=record.member_id,
        member_hash=record.member_hash,
        amount_mcv=record.amount_mcv,
        balance_after_mcv=record.balance_after_mcv,
        type=WalletOperationType(record.type),
        ref_type=record.ref_type,
        ref_id=record.ref_id,
        period=record.period,
        distribution_hash=record.distribution_hash,
        payout_share=record.payout_share,
        metadata=record.metadata,
        audit_hash=record.audit_hash,
        idempotency_key=record.idempotency_key,
        created_by=record.created_by,
        created_by_hash=record.created_by_hash,
        created_at=record.created_at,
    )


def _render_cabinet_html(overview: WebCabinetOverviewResponse) -> str:
    operations = _render_operations(overview.operations)
    content = _render_content(overview.content)
    links = _render_links(overview.referral_links)
    identity = f"{_escape(overview.member_id)} · {_escape(overview.tenant_id)}"
    balance_mcv = _format_mcv(overview.balance.balance_mcv)
    credited_mcv = _format_mcv(overview.balance.credited_mcv)
    debited_mcv = _format_mcv(overview.balance.debited_mcv)
    avg_points = _format_float(overview.contribution.avg_points_council)
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="icon" href="data:,">
  <title>Кабинет пайщика</title>
  <style>
    :root {{
      --page: #f6f7f9;
      --panel: #ffffff;
      --ink: #17191f;
      --muted: #5d6675;
      --line: #d9dde5;
      --accent: #146c62;
      --accent-soft: #e6f1ee;
      --signal: #8a5a00;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--page);
      color: var(--ink);
      font-family: Inter, system-ui, -apple-system, BlinkMacSystemFont,
        "Segoe UI", sans-serif;
      line-height: 1.5;
    }}
    main {{
      width: min(1180px, calc(100% - 32px));
      margin: 0 auto;
      padding: 28px 0 40px;
    }}
    header {{
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: end;
      margin-bottom: 18px;
    }}
    h1, h2, h3, p {{ margin: 0; }}
    h1 {{ font-size: 28px; line-height: 1.15; font-weight: 700; }}
    h2 {{ font-size: 18px; margin-bottom: 12px; }}
    h3 {{ font-size: 15px; margin-bottom: 4px; }}
    .muted {{ color: var(--muted); }}
    .period {{
      color: var(--muted);
      font-size: 14px;
      text-align: right;
    }}
    .summary-grid {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
      margin-bottom: 16px;
    }}
    .metric, .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: 0 1px 2px rgba(23, 25, 31, 0.04);
    }}
    .metric {{ padding: 14px; min-height: 104px; }}
    .metric-label {{
      color: var(--muted);
      font-size: 13px;
      margin-bottom: 8px;
    }}
    .metric-value {{
      font-size: 24px;
      line-height: 1.15;
      font-weight: 700;
      overflow-wrap: anywhere;
    }}
    .metric-note {{
      color: var(--muted);
      font-size: 13px;
      margin-top: 8px;
    }}
    .cabinet-shell {{
      display: grid;
      grid-template-columns: minmax(0, 1.25fr) minmax(300px, 0.75fr);
      gap: 16px;
      align-items: start;
    }}
    .stack {{ display: grid; gap: 16px; }}
    .panel {{ padding: 16px; }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 14px;
    }}
    th, td {{
      padding: 10px 8px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
    }}
    th {{ color: var(--muted); font-weight: 600; }}
    tr:last-child td {{ border-bottom: 0; }}
    .amount-positive {{ color: var(--accent); font-weight: 700; }}
    .amount-negative {{ color: var(--signal); font-weight: 700; }}
    .content-list, .link-list {{
      list-style: none;
      padding: 0;
      margin: 0;
      display: grid;
      gap: 12px;
    }}
    .content-item, .link-item {{
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      background: #fbfcfd;
    }}
    .tags {{
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-top: 10px;
    }}
    .tag {{
      background: var(--accent-soft);
      color: var(--accent);
      border-radius: 999px;
      padding: 3px 8px;
      font-size: 12px;
      font-weight: 700;
    }}
    .link-url {{
      color: var(--accent);
      overflow-wrap: anywhere;
      font-size: 14px;
    }}
    @media (max-width: 720px) {{
      main {{ width: min(100% - 20px, 1180px); padding-top: 18px; }}
      header {{ display: grid; align-items: start; }}
      .period {{ text-align: left; }}
      .summary-grid, .cabinet-shell {{ grid-template-columns: 1fr; }}
      .metric-value {{ font-size: 22px; }}
      th:nth-child(3), td:nth-child(3) {{ display: none; }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>Личный кабинет пайщика</h1>
        <p class="muted">{identity}</p>
      </div>
      <p class="period">{_escape(overview.period)}</p>
    </header>
    <section class="summary-grid" aria-label="Сводка">
      <article class="metric">
        <p class="metric-label">Баланс</p>
        <p class="metric-value">{balance_mcv} МСЦ</p>
        <p class="metric-note">+{credited_mcv} / -{debited_mcv}</p>
      </article>
      <article class="metric">
        <p class="metric-label">Вклад</p>
        <p class="metric-value">{_format_float(overview.contribution.total_points)}</p>
        <p class="metric-note">{overview.contribution.contribution_count} событий</p>
      </article>
      <article class="metric">
        <p class="metric-label">Кв</p>
        <p class="metric-value">{_format_share(overview.contribution.kv_capped)}</p>
        <p class="metric-note">raw {_format_share(overview.contribution.kv_raw)}</p>
      </article>
      <article class="metric">
        <p class="metric-label">Доля</p>
        <p class="metric-value">{_format_share(overview.contribution.payout_share)}</p>
        <p class="metric-note">среднее Совета {avg_points}</p>
      </article>
    </section>
    <section class="cabinet-shell">
      <div class="stack">
        <article class="panel">
          <h2>История операций</h2>
          {operations}
        </article>
        <article class="panel">
          <h2>Контент</h2>
          {content}
        </article>
      </div>
      <aside class="stack">
        <article class="panel">
          <h2>Реферальные ссылки</h2>
          {links}
        </article>
      </aside>
    </section>
  </main>
</body>
</html>"""


def _render_operations(operations: tuple[WalletOperationResponse, ...]) -> str:
    if not operations:
        return '<p class="muted">Операций нет</p>'

    rows = []
    for operation in operations:
        amount_class = (
            "amount-positive"
            if operation.amount_mcv >= Decimal("0")
            else "amount-negative"
        )
        amount = _format_signed_mcv(operation.amount_mcv)
        rows.append(
            "<tr>"
            f"<td>{_escape(_operation_type_label(operation.type))}</td>"
            f'<td class="{amount_class}">{amount}</td>'
            f"<td>{_escape(operation.ref_type)} · {_escape(operation.ref_id)}</td>"
            f"<td>{_escape(_format_datetime(operation.created_at))}</td>"
            "</tr>"
        )

    return (
        "<table>"
        "<thead><tr><th>Тип</th><th>Сумма</th><th>Связь</th><th>Дата</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
    )


def _render_content(content: tuple[CabinetContentItem, ...]) -> str:
    if not content:
        return '<p class="muted">Контента нет</p>'

    items = []
    for item in content:
        tags = "".join(
            f'<span class="tag">{_escape(target)}</span>'
            for target in item.platform_targets
        )
        items.append(
            '<li class="content-item">'
            f"<h3>{_escape(item.title)}</h3>"
            f'<p class="muted">{_escape(item.preview)}</p>'
            f'<p class="metric-note">{_format_float(item.points_awarded)} баллов · '
            f"{_escape(_format_datetime(item.created_at))}</p>"
            f'<div class="tags">{tags}</div>'
            "</li>"
        )

    return f'<ul class="content-list">{"".join(items)}</ul>'


def _render_links(links: tuple[CabinetReferralLink, ...]) -> str:
    if not links:
        return '<p class="muted">Ссылок нет</p>'

    items = []
    for link in links:
        items.append(
            '<li class="link-item">'
            f"<h3>{_escape(link.level)} · {_escape(link.owner_id)}</h3>"
            f'<p class="link-url">{_escape(link.url)}</p>'
            f'<p class="metric-note">Доля {_format_share(link.reward_share)}</p>'
            "</li>"
        )

    return f'<ul class="link-list">{"".join(items)}</ul>'


def _operation_type_label(operation_type: WalletOperationType) -> str:
    labels = {
        WalletOperationType.DISTRIBUTION_CREDIT: "Начисление",
        WalletOperationType.PAYOUT_DEBIT: "Списание",
        WalletOperationType.MANUAL_ADJUSTMENT: "Корректировка",
    }
    return labels[operation_type]


def _format_mcv(value: Decimal) -> str:
    return f"{value:.2f}"


def _format_signed_mcv(value: Decimal) -> str:
    sign = "+" if value >= Decimal("0") else ""
    return f"{sign}{_format_mcv(value)}"


def _format_float(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _format_share(value: float) -> str:
    return f"{value * 100:.1f}%"


def _format_datetime(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%d.%m.%Y %H:%M UTC")


def _escape(value: str) -> str:
    return html.escape(value, quote=True)


async def _tenant_core_error_handler(
    _request: Request,
    exc: Exception,
) -> JSONResponse:
    error = cast(TenantCoreError, exc)
    return JSONResponse(
        status_code=error.status_code,
        content=error.to_response_body(),
    )


async def _shared_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    error = cast(SharedError, exc)
    return JSONResponse(
        status_code=error.status_code,
        content=error.to_response_body(),
    )


async def _validation_error_handler(
    _request: Request,
    exc: Exception,
) -> JSONResponse:
    validation_error = cast(RequestValidationError, exc)
    return JSONResponse(
        status_code=400,
        content=jsonable_encoder(
            error_response_body(
                code=VALIDATION_ERROR_CODE,
                message="Запрос не прошёл валидацию",
                details={"errors": validation_error.errors()},
            )
        ),
    )
