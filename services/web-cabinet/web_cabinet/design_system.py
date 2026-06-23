from __future__ import annotations

import html
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime

from pydantic import Field

from libs.shared.models import SharedBaseModel

DESIGN_SYSTEM_VERSION = "0.1.0"
DESIGN_SYSTEM_NAME = "nmc-ui"
DESIGN_SYSTEM_SOURCE = "docs/UX_RESEARCH.md#5"


class DesignToken(SharedBaseModel):
    name: str = Field(min_length=1, max_length=128)
    value: str = Field(min_length=1, max_length=256)
    category: str = Field(min_length=1, max_length=64)
    purpose: str = Field(min_length=1, max_length=512)
    css_variable: str = Field(min_length=1, max_length=128)


class DesignComponent(SharedBaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=512)
    variants: tuple[str, ...] = Field(default_factory=tuple)
    states: tuple[str, ...] = Field(default_factory=tuple)
    accessibility: tuple[str, ...] = Field(default_factory=tuple)
    reused_in: tuple[str, ...] = Field(default_factory=tuple)


class DesignGuideline(SharedBaseModel):
    title: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=512)
    checks: tuple[str, ...] = Field(default_factory=tuple)


class DesignSystemResponse(SharedBaseModel):
    version: str
    name: str
    source: str
    tokens: tuple[DesignToken, ...]
    components: tuple[DesignComponent, ...]
    guidelines: tuple[DesignGuideline, ...]
    accessibility_baseline: tuple[str, ...]
    generated_at: datetime


DESIGN_TOKENS: tuple[DesignToken, ...] = (
    DesignToken(
        name="color.bg.canvas",
        value="#F6F7F9",
        category="color",
        purpose="Фон приложения.",
        css_variable="--mc-color-bg-canvas",
    ),
    DesignToken(
        name="color.bg.surface",
        value="#FFFFFF",
        category="color",
        purpose="Рабочие области, таблицы и формы.",
        css_variable="--mc-color-bg-surface",
    ),
    DesignToken(
        name="color.text.primary",
        value="#111827",
        category="color",
        purpose="Основной текст.",
        css_variable="--mc-color-text-primary",
    ),
    DesignToken(
        name="color.text.secondary",
        value="#4B5563",
        category="color",
        purpose="Подписи, метаданные и вторичный текст.",
        css_variable="--mc-color-text-secondary",
    ),
    DesignToken(
        name="color.border.default",
        value="#D8DEE6",
        category="color",
        purpose="Разделители и границы.",
        css_variable="--mc-color-border-default",
    ),
    DesignToken(
        name="color.brand.primary",
        value="#1F6F8B",
        category="color",
        purpose="Основные действия и активная навигация.",
        css_variable="--mc-color-brand-primary",
    ),
    DesignToken(
        name="color.brand.accent",
        value="#C45A2A",
        category="color",
        purpose="Акцент для важных, но не опасных действий.",
        css_variable="--mc-color-brand-accent",
    ),
    DesignToken(
        name="color.state.success",
        value="#2F855A",
        category="color",
        purpose="Успешная обработка и подтверждение.",
        css_variable="--mc-color-state-success",
    ),
    DesignToken(
        name="color.state.warning",
        value="#B7791F",
        category="color",
        purpose="Истекающее окно и ручная проверка.",
        css_variable="--mc-color-state-warning",
    ),
    DesignToken(
        name="color.state.danger",
        value="#C53030",
        category="color",
        purpose="Вето, отказ и критичный риск.",
        css_variable="--mc-color-state-danger",
    ),
    DesignToken(
        name="color.state.info",
        value="#2B6CB0",
        category="color",
        purpose="Нейтральная информация и ссылки.",
        css_variable="--mc-color-state-info",
    ),
    DesignToken(
        name="color.focus",
        value="#6B46C1",
        category="color",
        purpose="Видимый focus-ring для клавиатурной навигации.",
        css_variable="--mc-color-focus",
    ),
    DesignToken(
        name="font.family.base",
        value=(
            'Inter, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", '
            "sans-serif"
        ),
        category="typography",
        purpose="Основной интерфейс.",
        css_variable="--mc-font-family-base",
    ),
    DesignToken(
        name="font.family.mono",
        value="ui-monospace, SFMono-Regular, Menlo, monospace",
        category="typography",
        purpose="Hash, ID и технические значения.",
        css_variable="--mc-font-family-mono",
    ),
    DesignToken(
        name="font.size.xs",
        value="12px/16px",
        category="typography",
        purpose="Метаданные и подписи таблиц.",
        css_variable="--mc-font-size-xs",
    ),
    DesignToken(
        name="font.size.sm",
        value="14px/20px",
        category="typography",
        purpose="Основной текст плотных интерфейсов.",
        css_variable="--mc-font-size-sm",
    ),
    DesignToken(
        name="font.size.md",
        value="16px/24px",
        category="typography",
        purpose="Формы, описания и модальные окна.",
        css_variable="--mc-font-size-md",
    ),
    DesignToken(
        name="font.size.lg",
        value="20px/28px",
        category="typography",
        purpose="Заголовки экранов.",
        css_variable="--mc-font-size-lg",
    ),
    DesignToken(
        name="font.size.xl",
        value="24px/32px",
        category="typography",
        purpose="Редкие сводные заголовки.",
        css_variable="--mc-font-size-xl",
    ),
    DesignToken(
        name="space.1",
        value="4px",
        category="spacing",
        purpose="Минимальный внутренний зазор.",
        css_variable="--mc-space-1",
    ),
    DesignToken(
        name="space.2",
        value="8px",
        category="spacing",
        purpose="Зазор между компактными элементами.",
        css_variable="--mc-space-2",
    ),
    DesignToken(
        name="space.3",
        value="12px",
        category="spacing",
        purpose="Внутренний отступ плотных карточек.",
        css_variable="--mc-space-3",
    ),
    DesignToken(
        name="space.4",
        value="16px",
        category="spacing",
        purpose="Базовый отступ секций и панелей.",
        css_variable="--mc-space-4",
    ),
    DesignToken(
        name="space.6",
        value="24px",
        category="spacing",
        purpose="Вертикальный ритм экранов.",
        css_variable="--mc-space-6",
    ),
    DesignToken(
        name="space.8",
        value="32px",
        category="spacing",
        purpose="Широкий отступ верхнего уровня.",
        css_variable="--mc-space-8",
    ),
    DesignToken(
        name="radius.control",
        value="6px",
        category="shape",
        purpose="Кнопки, поля и compact controls.",
        css_variable="--mc-radius-control",
    ),
    DesignToken(
        name="radius.card",
        value="8px",
        category="shape",
        purpose="Карточки, панели и элементы списков.",
        css_variable="--mc-radius-card",
    ),
    DesignToken(
        name="shadow.focus",
        value="0 0 0 3px rgba(107, 70, 193, 0.35)",
        category="elevation",
        purpose="Видимое состояние клавиатурного фокуса.",
        css_variable="--mc-shadow-focus",
    ),
)

DESIGN_COMPONENTS: tuple[DesignComponent, ...] = (
    DesignComponent(
        name="AppShell",
        description="Рабочая оболочка экрана: заголовок, tenant, роль и контент.",
        variants=("wide", "compact", "governance"),
        states=("active", "collapsed", "forbidden"),
        accessibility=("landmark_main", "visible_heading", "responsive_reflow"),
        reused_in=(
            "/cabinet",
            "/council/panel",
            "/analytics/dashboard",
            "/onboarding",
            "/voice-assistant",
        ),
    ),
    DesignComponent(
        name="MetricTile",
        description="Сканируемая карточка числового показателя, таймера или статуса.",
        variants=("neutral", "success", "warning", "danger", "info"),
        states=("normal", "warning", "stale", "loading"),
        accessibility=("aria_label_or_heading", "not_color_only", "stable_size"),
        reused_in=(
            "/cabinet",
            "/council/panel",
            "/analytics/dashboard",
            "/onboarding",
            "/voice-assistant",
        ),
    ),
    DesignComponent(
        name="DataTable",
        description="Табличный компонент для операций, KPI, аудита и агрегатов.",
        variants=("compact", "financial", "audit"),
        states=("sort", "filter", "empty", "error"),
        accessibility=("aria_label_or_role", "semantic_table", "mobile_columns"),
        reused_in=("/cabinet", "/analytics/dashboard"),
    ),
    DesignComponent(
        name="StatusBadge",
        description="Короткий статус задачи, операции, риска или готовности.",
        variants=("success", "warning", "danger", "info", "neutral"),
        states=("normal", "critical", "expired", "verified"),
        accessibility=("not_color_only", "text_status", "contrast_minimum_4_5_1"),
        reused_in=(
            "/council/panel",
            "/analytics/dashboard",
            "/onboarding",
            "/voice-assistant",
        ),
    ),
    DesignComponent(
        name="TaskCard",
        description="Карточка следующего действия участника или оператора.",
        variants=("member", "council", "assistant"),
        states=("available", "assigned", "overdue", "done"),
        accessibility=("heading_first", "keyboard_action"),
        reused_in=("/onboarding",),
    ),
    DesignComponent(
        name="HITLQueueItem",
        description="Строка очереди Совета с риском, дедлайном и действием.",
        variants=("low", "medium", "high", "critical"),
        states=("queued", "expired", "confirmed", "vetoed"),
        accessibility=("deadline_text", "not_color_only", "action_group_label"),
        reused_in=("/council/panel",),
    ),
    DesignComponent(
        name="VetoActionBar",
        description="Группа кнопок для вето и подтверждения чувствительной операции.",
        variants=("default", "requires_2fa", "danger"),
        states=("disabled", "needs-2fa", "confirm", "veto"),
        accessibility=("button_labels", "focus_visible", "danger_explanation"),
        reused_in=("/council/panel",),
    ),
    DesignComponent(
        name="TwoFactorDialog",
        description="Паттерн подтверждения выплат и критичных политик через 2FA.",
        variants=("totp", "locked"),
        states=("input", "invalid", "locked", "success"),
        accessibility=("labelled_input", "error_message", "focus_trap_ready"),
        reused_in=("/council/panel",),
    ),
    DesignComponent(
        name="AuditHash",
        description="Моноширинное отображение hash, audit id и chain reference.",
        variants=("full", "truncated", "copyable"),
        states=("copied", "verified", "mismatch"),
        accessibility=("full_value_in_text", "overflow_wrap", "copy_label"),
        reused_in=("/council/panel", "/voice-assistant"),
    ),
    DesignComponent(
        name="Timeline",
        description="Цепочка решений, статусов и audit-событий.",
        variants=("audit", "decision", "retention"),
        states=("queued", "reviewed", "approved", "rejected"),
        accessibility=("ordered_events", "timestamps_absolute"),
        reused_in=("/council/panel",),
    ),
    DesignComponent(
        name="ConsentControl",
        description="Строка согласия онбординга с обязательностью и статусом.",
        variants=("required", "optional"),
        states=("granted", "revoked", "pending"),
        accessibility=("label_text", "state_text", "keyboard_toggle_ready"),
        reused_in=("/onboarding",),
    ),
    DesignComponent(
        name="EmptyState",
        description="Пустое состояние без декоративной перегрузки.",
        variants=("first-task", "no-results", "no-access"),
        states=("empty", "forbidden", "error"),
        accessibility=("clear_message", "no_motion_required"),
        reused_in=(
            "/cabinet",
            "/council/panel",
            "/analytics/dashboard",
            "/onboarding",
        ),
    ),
    DesignComponent(
        name="InlineAlert",
        description="Встроенное уведомление об ошибке, риске или результате.",
        variants=("info", "warning", "danger", "success"),
        states=("visible", "dismissed"),
        accessibility=("role_status_or_alert", "not_color_only"),
        reused_in=("/design-system/ui-kit", "/voice-assistant"),
    ),
)

DESIGN_GUIDELINES: tuple[DesignGuideline, ...] = (
    DesignGuideline(
        title="Рабочая плотность",
        description=(
            "Интерфейсы выглядят как операционный инструмент: таблицы, очереди, "
            "фильтры и контекстные панели важнее декоративных блоков."
        ),
        checks=("stable_layout_dimensions", "compact_headings", "dense_tables"),
    ),
    DesignGuideline(
        title="Проверяемость",
        description=(
            "Для важных действий рядом показываются источник, policy version, "
            "actor, timestamp и audit hash."
        ),
        checks=("audit_hash_visible", "absolute_timestamp", "policy_version_visible"),
    ),
    DesignGuideline(
        title="Human-in-the-Loop",
        description=(
            "AI-рекомендации отделены от финальных решений человека, а опасные "
            "действия требуют явного подтверждения."
        ),
        checks=("danger_explanation", "two_factor_ready", "council_control"),
    ),
    DesignGuideline(
        title="Минимизация ПДн",
        description=(
            "По умолчанию показываются статусы и агрегаты; детали раскрываются "
            "только по роли и tenant-контексту."
        ),
        checks=("tenant_scoped", "no_sensitive_payload", "role_based_detail"),
    ),
    DesignGuideline(
        title="Доступность",
        description=(
            "Все действия доступны с клавиатуры, статус не передается только "
            "цветом, а контраст текста к фону не ниже 4.5:1."
        ),
        checks=(
            "keyboard_focus_visible",
            "contrast_minimum_4_5_1",
            "status_not_color_only",
        ),
    ),
)

ACCESSIBILITY_BASELINE: tuple[str, ...] = (
    "keyboard_focus_visible",
    "contrast_minimum_4_5_1",
    "status_not_color_only",
    "stable_layout_dimensions",
)


def design_system_response(
    *,
    generated_at: datetime | None = None,
) -> DesignSystemResponse:
    return DesignSystemResponse(
        version=DESIGN_SYSTEM_VERSION,
        name=DESIGN_SYSTEM_NAME,
        source=DESIGN_SYSTEM_SOURCE,
        tokens=DESIGN_TOKENS,
        components=DESIGN_COMPONENTS,
        guidelines=DESIGN_GUIDELINES,
        accessibility_baseline=ACCESSIBILITY_BASELINE,
        generated_at=generated_at or datetime.now(UTC),
    )


def design_system_css(*, mobile_breakpoint_px: int = 760) -> str:
    return f"""
    :root {{
      --mc-color-bg-canvas: #F6F7F9;
      --mc-color-bg-surface: #FFFFFF;
      --mc-color-text-primary: #111827;
      --mc-color-text-secondary: #4B5563;
      --mc-color-border-default: #D8DEE6;
      --mc-color-brand-primary: #1F6F8B;
      --mc-color-brand-accent: #C45A2A;
      --mc-color-state-success: #2F855A;
      --mc-color-state-warning: #B7791F;
      --mc-color-state-danger: #C53030;
      --mc-color-state-info: #2B6CB0;
      --mc-color-focus: #6B46C1;
      --mc-font-family-base: Inter, system-ui, -apple-system, BlinkMacSystemFont,
        "Segoe UI", sans-serif;
      --mc-font-family-mono: ui-monospace, SFMono-Regular, Menlo, monospace;
      --mc-font-size-xs: 12px;
      --mc-line-height-xs: 16px;
      --mc-font-size-sm: 14px;
      --mc-line-height-sm: 20px;
      --mc-font-size-md: 16px;
      --mc-line-height-md: 24px;
      --mc-font-size-lg: 20px;
      --mc-line-height-lg: 28px;
      --mc-font-size-xl: 24px;
      --mc-line-height-xl: 32px;
      --mc-space-1: 4px;
      --mc-space-2: 8px;
      --mc-space-3: 12px;
      --mc-space-4: 16px;
      --mc-space-6: 24px;
      --mc-space-8: 32px;
      --mc-radius-control: 6px;
      --mc-radius-card: 8px;
      --mc-shadow-focus: 0 0 0 3px rgba(107, 70, 193, 0.35);
      --mc-shadow-card: 0 1px 2px rgba(17, 24, 39, 0.05);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--mc-color-bg-canvas);
      color: var(--mc-color-text-primary);
      font-family: var(--mc-font-family-base);
      line-height: 1.45;
    }}
    :focus-visible {{
      outline: 2px solid var(--mc-color-focus);
      outline-offset: 2px;
      box-shadow: var(--mc-shadow-focus);
    }}
    .mc-app-shell {{
      width: min(1240px, calc(100% - 32px));
      margin: 0 auto;
      padding: var(--mc-space-6) 0 36px;
    }}
    .mc-page-header {{
      display: flex;
      justify-content: space-between;
      gap: var(--mc-space-4);
      align-items: end;
      margin-bottom: var(--mc-space-4);
    }}
    .mc-page-title {{
      font-size: var(--mc-font-size-xl);
      line-height: var(--mc-line-height-xl);
      margin: 0;
    }}
    .mc-muted, .muted {{
      color: var(--mc-color-text-secondary);
    }}
    .mc-status-line, .status-line, .period {{
      color: var(--mc-color-text-secondary);
      font-size: var(--mc-font-size-sm);
      line-height: var(--mc-line-height-sm);
      text-align: right;
    }}
    .mc-summary-grid {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: var(--mc-space-3);
      margin-bottom: var(--mc-space-4);
    }}
    .mc-metric, .mc-panel {{
      background: var(--mc-color-bg-surface);
      border: 1px solid var(--mc-color-border-default);
      border-radius: var(--mc-radius-card);
      box-shadow: var(--mc-shadow-card);
    }}
    .mc-metric {{
      min-height: 96px;
      padding: var(--mc-space-3);
      overflow-wrap: anywhere;
    }}
    .mc-metric-label {{
      color: var(--mc-color-text-secondary);
      font-size: var(--mc-font-size-xs);
      line-height: var(--mc-line-height-xs);
      margin: 0 0 var(--mc-space-2);
    }}
    .mc-metric-value {{
      color: var(--mc-color-text-primary);
      font-size: var(--mc-font-size-xl);
      line-height: 1.15;
      font-weight: 700;
      margin: 0;
      overflow-wrap: anywhere;
    }}
    .mc-metric-note {{
      color: var(--mc-color-text-secondary);
      font-size: var(--mc-font-size-xs);
      line-height: var(--mc-line-height-xs);
      margin: var(--mc-space-2) 0 0;
    }}
    .mc-panel {{
      padding: var(--mc-space-4);
    }}
    .mc-panel-title {{
      font-size: 17px;
      line-height: var(--mc-line-height-md);
      margin: 0 0 var(--mc-space-3);
    }}
    .mc-button {{
      min-height: 36px;
      border: 1px solid var(--mc-color-border-default);
      border-radius: var(--mc-radius-control);
      background: var(--mc-color-bg-surface);
      color: var(--mc-color-text-primary);
      padding: 0 var(--mc-space-3);
      font-weight: 800;
      cursor: pointer;
      text-decoration: none;
      display: inline-flex;
      align-items: center;
      justify-content: center;
    }}
    .mc-button:disabled {{
      cursor: not-allowed;
      opacity: 0.5;
    }}
    .mc-button-primary {{
      background: var(--mc-color-brand-primary);
      border-color: var(--mc-color-brand-primary);
      color: #FFFFFF;
    }}
    .mc-button-danger {{
      background: var(--mc-color-state-danger);
      border-color: var(--mc-color-state-danger);
      color: #FFFFFF;
    }}
    .mc-badge, .badge, .status, .risk {{
      display: inline-flex;
      align-items: center;
      min-height: 24px;
      border-radius: 999px;
      padding: 3px var(--mc-space-2);
      font-size: var(--mc-font-size-xs);
      line-height: var(--mc-line-height-xs);
      font-weight: 800;
      white-space: nowrap;
    }}
    .mc-badge-success {{
      background: #E7F4ED;
      color: var(--mc-color-state-success);
    }}
    .mc-badge-warning {{
      background: #FFF4D6;
      color: #8A5A00;
    }}
    .mc-badge-danger {{
      background: #FCE8E6;
      color: var(--mc-color-state-danger);
    }}
    .mc-badge-info, .mc-badge-neutral {{
      background: #E8F1FA;
      color: var(--mc-color-state-info);
    }}
    .mc-table {{
      width: 100%;
      border-collapse: collapse;
      font-size: var(--mc-font-size-sm);
      line-height: var(--mc-line-height-sm);
    }}
    .mc-table th, .mc-table td {{
      padding: 10px var(--mc-space-2);
      border-bottom: 1px solid var(--mc-color-border-default);
      text-align: left;
      vertical-align: top;
    }}
    .mc-table th {{
      color: var(--mc-color-text-secondary);
      font-weight: 600;
    }}
    .mc-table tr:last-child td {{
      border-bottom: 0;
    }}
    .mc-empty-state {{
      color: var(--mc-color-text-secondary);
      min-height: 44px;
      display: flex;
      align-items: center;
      margin: 0;
    }}
    .mc-hash {{
      color: var(--mc-color-text-secondary);
      font-family: var(--mc-font-family-mono);
      font-size: var(--mc-font-size-xs);
      line-height: var(--mc-line-height-xs);
      overflow-wrap: anywhere;
    }}
    .mc-alert {{
      border: 1px solid var(--mc-color-border-default);
      border-radius: var(--mc-radius-card);
      padding: var(--mc-space-3);
      background: var(--mc-color-bg-surface);
    }}
    .mc-alert-info {{
      border-color: #B7D2EE;
      background: #EEF6FF;
    }}
    @media (max-width: {mobile_breakpoint_px}px) {{
      .mc-app-shell {{
        width: min(100% - 20px, 1240px);
        padding-top: 18px;
      }}
      .mc-page-header {{
        display: grid;
        align-items: start;
      }}
      .mc-status-line, .status-line, .period {{
        text-align: left;
      }}
      .mc-summary-grid {{
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }}
    }}
    """.strip()


def render_metric_tile(
    *,
    label: str,
    value: str,
    note: str = "",
    tone: str = "neutral",
) -> str:
    note_html = (
        f'<p class="metric-note mc-metric-note">{_escape(note)}</p>' if note else ""
    )
    return (
        '<article class="metric mc-metric" data-component="MetricTile" '
        f'data-tone="{_escape(tone)}">'
        f'<p class="metric-label mc-metric-label">{_escape(label)}</p>'
        f'<p class="metric-value mc-metric-value">{_escape(value)}</p>'
        f"{note_html}"
        "</article>"
    )


def render_panel(
    *,
    title: str,
    body: str,
    tag: str = "article",
    class_name: str = "",
) -> str:
    extra_class = f" {_escape(class_name)}" if class_name else ""
    safe_tag = tag if tag in {"article", "aside", "section"} else "article"
    return (
        f'<{safe_tag} class="panel mc-panel{extra_class}" data-component="Panel">'
        f'<h2 class="mc-panel-title">{_escape(title)}</h2>'
        f"{body}"
        f"</{safe_tag}>"
    )


def render_status_badge(
    *,
    label: str,
    tone: str = "info",
    class_name: str = "",
) -> str:
    extra_class = f" {_escape(class_name)}" if class_name else ""
    return (
        '<span class="badge mc-badge '
        f'mc-badge-{_escape(tone)}{extra_class}" data-component="StatusBadge">'
        f"{_escape(label)}</span>"
    )


def render_action_button(
    *,
    label: str,
    tone: str = "neutral",
    disabled: bool = False,
) -> str:
    disabled_attr = " disabled" if disabled else ""
    tone_class = f" mc-button-{_escape(tone)}" if tone != "neutral" else ""
    return (
        f'<button class="mc-button{tone_class}" type="button"{disabled_attr}>'
        f"{_escape(label)}</button>"
    )


def render_data_table(
    *,
    headers: Sequence[str],
    rows: Iterable[Sequence[str]],
    empty_label: str,
    aria_label: str,
) -> str:
    row_items = tuple(tuple(row) for row in rows)
    if not row_items:
        return render_empty_state(empty_label)

    header_html = "".join(f"<th>{_escape(header)}</th>" for header in headers)
    rows_html = "".join(
        "<tr>" + "".join(f"<td>{_escape(cell)}</td>" for cell in row) + "</tr>"
        for row in row_items
    )
    return (
        '<table class="mc-table" data-component="DataTable" '
        f'aria-label="{_escape(aria_label)}">'
        f"<thead><tr>{header_html}</tr></thead>"
        f"<tbody>{rows_html}</tbody>"
        "</table>"
    )


def render_empty_state(label: str) -> str:
    return (
        '<p class="muted mc-empty-state" data-component="EmptyState">'
        f"{_escape(label)}</p>"
    )


def render_inline_alert(
    *,
    title: str,
    body: str,
    tone: str = "info",
) -> str:
    return (
        '<section class="mc-alert '
        f'mc-alert-{_escape(tone)}" data-component="InlineAlert" role="status">'
        f"<h3>{_escape(title)}</h3>"
        f'<p class="mc-muted">{_escape(body)}</p>'
        "</section>"
    )


def render_design_system_ui_kit() -> str:
    token_rows = tuple(
        (token.name, token.value, token.category, token.css_variable)
        for token in DESIGN_TOKENS
    )
    component_rows = tuple(
        (
            component.name,
            ", ".join(component.states),
            ", ".join(component.accessibility),
            ", ".join(component.reused_in) or "UI-kit",
        )
        for component in DESIGN_COMPONENTS
    )
    sample_hash = "a" * 64
    content = (
        '<section class="summary-grid mc-summary-grid" aria-label="Сводка UI-кита">'
        + render_metric_tile(
            label="Токены",
            value=str(len(DESIGN_TOKENS)),
            note="цвета, шрифты, отступы",
        )
        + render_metric_tile(
            label="Компоненты",
            value=str(len(DESIGN_COMPONENTS)),
            note="переиспользуемые паттерны",
        )
        + render_metric_tile(
            label="A11y",
            value="4.5:1",
            note="минимальный контраст",
            tone="success",
        )
        + render_metric_tile(
            label="Focus",
            value="visible",
            note="keyboard baseline",
            tone="info",
        )
        + "</section>"
        + '<section class="dashboard-shell" aria-label="Компоненты UI-кита">'
        + '<div class="stack">'
        + render_panel(
            title="Токены",
            body=render_data_table(
                headers=("Токен", "Значение", "Категория", "CSS variable"),
                rows=token_rows,
                empty_label="Токены не описаны",
                aria_label="Токены дизайн-системы",
            ),
        )
        + render_panel(
            title="Компоненты",
            body=render_data_table(
                headers=("Компонент", "Состояния", "A11y", "Где используется"),
                rows=component_rows,
                empty_label="Компоненты не описаны",
                aria_label="Каталог компонентов дизайн-системы",
            ),
        )
        + "</div>"
        + '<aside class="stack">'
        + render_panel(
            title="Статусы",
            body=(
                '<div class="controls">'
                + render_status_badge(label="success", tone="success")
                + render_status_badge(label="warning", tone="warning")
                + render_status_badge(label="danger", tone="danger")
                + render_status_badge(label="info", tone="info")
                + "</div>"
            ),
            tag="aside",
        )
        + render_panel(
            title="VetoActionBar",
            body=(
                '<div class="actions" data-component="VetoActionBar">'
                + render_action_button(label="Вето", tone="danger")
                + render_action_button(label="Подтвердить", tone="primary")
                + render_action_button(label="2FA нужно", disabled=True)
                + "</div>"
            ),
            tag="aside",
        )
        + render_panel(
            title="AuditHash",
            body=(
                f'<p class="hash mc-hash" data-component="AuditHash">{sample_hash}</p>'
            ),
            tag="aside",
        )
        + render_inline_alert(
            title="Доступность",
            body="Статус не кодируется только цветом, фокус видим с клавиатуры.",
            tone="info",
        )
        + "</aside>"
        + "</section>"
    )
    return render_html_document(
        title="Дизайн-система и UI-кит",
        subtitle="nmc-ui · токены, компоненты и accessibility baseline",
        status_line=f"{DESIGN_SYSTEM_SOURCE} · v{DESIGN_SYSTEM_VERSION}",
        content=content,
    )


def render_html_document(
    *,
    title: str,
    subtitle: str,
    status_line: str,
    content: str,
    mobile_breakpoint_px: int = 760,
) -> str:
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="icon" href="data:,">
  <title>{_escape(title)}</title>
  <style>
    {design_system_css(mobile_breakpoint_px=mobile_breakpoint_px)}
    h1, h2, h3, p {{ margin-top: 0; }}
    .dashboard-shell {{
      display: grid;
      grid-template-columns: minmax(0, 1.25fr) minmax(320px, 0.75fr);
      gap: var(--mc-space-4);
      align-items: start;
    }}
    .stack {{
      display: grid;
      gap: var(--mc-space-4);
    }}
    .controls, .actions {{
      display: flex;
      flex-wrap: wrap;
      gap: var(--mc-space-2);
    }}
    @media (max-width: {mobile_breakpoint_px}px) {{
      .dashboard-shell {{
        grid-template-columns: 1fr;
      }}
    }}
  </style>
</head>
<body data-design-system="{DESIGN_SYSTEM_NAME}">
  <main class="mc-app-shell" data-component="AppShell">
    <header class="mc-page-header">
      <div>
        <h1 class="mc-page-title">{_escape(title)}</h1>
        <p class="mc-muted">{_escape(subtitle)}</p>
      </div>
      <p class="mc-status-line">{_escape(status_line)}</p>
    </header>
    {content}
  </main>
</body>
</html>"""


def _escape(value: str) -> str:
    return html.escape(value, quote=True)
