# Web Cabinet

**Статус:** 🟢 реализовано для #67 и #68 · **Этап:** Этап 4 — Клиентские приложения и UX · **Компонент:** `component:web-cabinet`

Личный кабинет пайщика показывает вклад, баланс МСЦ, историю операций, контент
и реферальные ссылки в пределах tenant пользователя. Панель Совета собирает
очередь HITL-выплат, окно вето, 2FA-подтверждение, политики и audit timeline в
единый клиентский экран.

## Зона ответственности

- Обзор вклада пайщика за период: баллы, Кв, доля распределения и число
  событий.
- Баланс МСЦ и история операций из Wallet Module.
- Список собственного контента и связанных реферальных ссылок L1/L2/L3.
- Адаптивный HTML-интерфейс для первого клиентского экрана.
- Панель Совета для просмотра очереди HITL, наложения вето в окне,
  2FA-подтверждения выплат и изменения политик Policy Manager.

## Основные интерфейсы

- **GET** `/cabinet/overview` — JSON-обзор кабинета за `period=<YYYY-MM>`.
- **GET** `/cabinet` — адаптивный HTML-интерфейс кабинета за
  `period=<YYYY-MM>`.
- **GET** `/council/panel/overview` — JSON-сводка панели Совета: очередь HITL,
  риск, окно вето, 2FA, политики и audit timeline.
- **GET** `/council/panel` — адаптивный HTML-интерфейс панели Совета.
- **POST** `/council/payouts/{payout_id}/veto` — наложить вето Совета в
  открытом окне с обязательной причиной.
- **POST** `/council/payouts/{payout_id}/confirm` — подтвердить выплату через
  TOTP 2FA.
- **PUT** `/council/policies/{key}` — изменить политику/порог tenant решением
  Совета.

Оба endpoint личного кабинета принимают опциональный `member_id`: пайщик может
читать только свой кабинет, а роли `council`, `presidium`, `board` — кабинет
участника внутри своего tenant.

## Модель данных

- **CabinetContributionRecord** — `tenant_id`, `member_id`, `period`,
  `total_points`, `avg_points_council`, `kv_raw`, `kv_capped`,
  `payout_share`, `contribution_count`.
- **CabinetContentRecord** — `tenant_id`, `owner_id`, `content_id`,
  `template_id`, `title`, `preview`, `content_hash`, `platform_targets`,
  `referral_links`, `points_awarded`, `created_at`.
- **CouncilPanelPayoutAnnotation** — tenant-scoped риск, policy key, источник и
  объяснение расчёта для карточки HITL-выплаты.
- **CouncilPanelAuditRecord** — tenant-scoped timeline событий выплаты с
  `event_type`, `event_id`, `audit_hash` и временем события.
- **WalletBalanceResponse / WalletOperationResponse** — используются напрямую
  из Wallet Module, чтобы баланс и история соответствовали backend.

## Безопасность и мультитенантность

- Все запросы проходят через tenant middleware и проверенный JWT.
- `member_full` и `member_assoc` читают только собственный кабинет.
- Управляющие роли читают другие кабинеты только в пределах текущего tenant.
- tenant-isolation контракт #67 покрывает подмену `X-Tenant-Id` и отсутствие
  данных другого tenant в ответах.
- Панель Совета доступна только роли `council`; tenant-isolation контракт #68
  покрывает подмену `X-Tenant-Id` и отсутствие HITL/audit данных другого tenant.
- Подтверждение выплат из панели требует TOTP-код и переиспользует контракт
  HITL Payout Gateway `payout.confirm`.

## Реализация

- [services/web-cabinet/web_cabinet/api.py](../../services/web-cabinet/web_cabinet/api.py) —
  REST API, HTML-рендер, in-memory projection repositories, HITL/Policy wiring
  и RBAC.
- [services/web-cabinet/README.md](../../services/web-cabinet/README.md) —
  запуск и границы сервиса.
- [tests/test_web_cabinet_issue67_acceptance_contract.py](../../tests/test_web_cabinet_issue67_acceptance_contract.py) —
  acceptance-тест #67.
- [tests/test_council_panel_issue68_acceptance_contract.py](../../tests/test_council_panel_issue68_acceptance_contract.py) —
  acceptance-тест #68.

## Связанные задачи (issue)

- [#67](https://github.com/xlabtg/Media_Center/issues/67) — Веб-кабинет пайщика (вклад, баланс, история)
- [#68](https://github.com/xlabtg/Media_Center/issues/68) — Панель Совета (HITL): вето, пороги, подтверждения
- [#60](https://github.com/xlabtg/Media_Center/issues/60) — Wallet Module: учёт МСЦ и операций

---
<sub>Спецификация синхронизирована с реализацией Web Cabinet для issue #67 и #68.</sub>
