# Design System и UI-kit

**Статус:** реализовано для #73 · **Этап:** Этап 4 — Клиентские приложения и UX · **Компонент:** `component:web-cabinet`

Дизайн-система `nmc-ui` превращает baseline из
[docs/UX_RESEARCH.md](../UX_RESEARCH.md#5-дизайн-система-v0) в исполняемый
контракт Web Cabinet: токены, каталог компонентов, accessibility baseline и
HTML UI-kit доступны через API и переиспользуются клиентскими страницами.

## Интерфейсы

- **GET** `/design-system/tokens` — JSON-контракт токенов, компонентов,
  гайдлайнов и accessibility baseline.
- **GET** `/design-system/ui-kit` — HTML-витрина UI-kit с теми же токенами,
  компонентами и `data-component` markers.

Оба endpoint требуют валидный tenant context так же, как остальные экраны Web
Cabinet, но не раскрывают tenant-данные и не читают бизнес-проекции.

## Токены

Базовые токены синхронизированы с UX baseline:

- `color.bg.canvas` / `--mc-color-bg-canvas` — `#F6F7F9`;
- `color.bg.surface` / `--mc-color-bg-surface` — `#FFFFFF`;
- `color.text.primary` / `--mc-color-text-primary` — `#111827`;
- `color.text.secondary` / `--mc-color-text-secondary` — `#4B5563`;
- `color.border.default` / `--mc-color-border-default` — `#D8DEE6`;
- `color.brand.primary` / `--mc-color-brand-primary` — `#1F6F8B`;
- `color.brand.accent` / `--mc-color-brand-accent` — `#C45A2A`;
- `color.state.success`, `color.state.warning`, `color.state.danger`,
  `color.state.info` — статусы без передачи смысла только цветом;
- `color.focus` / `--mc-color-focus` — видимый keyboard focus-ring;
- `font.family.base`, `font.family.mono`, `font.size.*`;
- `space.1`, `space.2`, `space.3`, `space.4`, `space.6`, `space.8`;
- `radius.control`, `radius.card`, `shadow.focus`.

## Компоненты

UI-kit v0 фиксирует переиспользуемые UI-компоненты для всех интерфейсов этапа
4:

- `AppShell` — оболочка экрана с tenant/role/status контекстом.
- `MetricTile` — KPI, баланс, Кв, таймеры и статусы.
- `DataTable` — история операций, KPI, агрегаты и audit-таблицы.
- `StatusBadge` — текстовый статус риска, готовности или операции.
- `TaskCard` — следующее действие участника или оператора.
- `HITLQueueItem` — элемент очереди Совета.
- `VetoActionBar` — действия вето/подтверждения.
- `TwoFactorDialog` — паттерн 2FA для чувствительных операций.
- `AuditHash` — hash/id/chain reference с моноширинным отображением.
- `Timeline` — цепочка решений и audit-событий.
- `ConsentControl` — согласия онбординга.
- `EmptyState` — пустые состояния без декоративной перегрузки.
- `InlineAlert` — встроенные предупреждения и сообщения.

Существующие страницы `/cabinet`, `/council/panel`,
`/analytics/dashboard`, `/onboarding` и `/voice-assistant` подключают общий CSS
из `web_cabinet.design_system`, имеют `data-design-system="nmc-ui"` и
используют `data-component` markers для `AppShell`, `MetricTile` и `Panel`.

## Доступность

Accessibility baseline:

- `keyboard_focus_visible` — все кликабельные элементы получают
  `:focus-visible` с `shadow.focus`;
- `contrast_minimum_4_5_1` — основной и вторичный текст используют пары цветов
  с достаточным контрастом;
- `status_not_color_only` — статус дублируется текстом, а не только цветом;
- `stable_layout_dimensions` — карточки, таблицы, кнопки и hash-значения не
  ломают сетку при длинных ID и статусах.

## Реализация

- [services/web-cabinet/web_cabinet/design_system.py](../../services/web-cabinet/web_cabinet/design_system.py) —
  токены, Pydantic-контракт, CSS и HTML helper-ы UI-kit.
- [services/web-cabinet/web_cabinet/api.py](../../services/web-cabinet/web_cabinet/api.py) —
  endpoint-ы `/design-system/tokens` и `/design-system/ui-kit`, подключение
  общего UI layer к HTML-страницам.
- [tests/test_design_system_issue73_acceptance_contract.py](../../tests/test_design_system_issue73_acceptance_contract.py) —
  acceptance-контракт #73.

## Связанные задачи

- [#13](https://github.com/xlabtg/Media_Center/issues/13) — UX-исследование и прототипы
- [#67](https://github.com/xlabtg/Media_Center/issues/67) — Веб-кабинет пайщика
- [#68](https://github.com/xlabtg/Media_Center/issues/68) — Панель Совета
- [#69](https://github.com/xlabtg/Media_Center/issues/69) — Дашборды аналитики и KPI
- [#70](https://github.com/xlabtg/Media_Center/issues/70) — Онбординг + AI-ассистент
- [#72](https://github.com/xlabtg/Media_Center/issues/72) — UI голосового ассистента
- [#73](https://github.com/xlabtg/Media_Center/issues/73) — Дизайн-система и UI-кит
