# HITL Payout Gateway

**Статус:** 🟡 планируется · **Этап:** Этап 2 — Ключевые микросервисы · **Компонент:** `component:hitl-payout`

Шлюз выплат с обязательным контролем человека: очередь, окно вето Совета и подтверждение через 2FA. AI исполняет — Совет контролирует.

## Зона ответственности
- Постановка рассчитанных выплат в очередь со статусами
- Окно вето: Совет может отменить выплату до её исполнения
- Обязательное подтверждение выплаты через 2FA (TOTP)
- Исполнение через коннекторы и фиксация результата в аудит

## Ключевые правила и формулы
- Окно вето: `VETO_WINDOW_HOURS` (по умолчанию **8 ч**)

## Основные интерфейсы
- **POST** `/payouts/queue` — поставить выплату в очередь
- **POST** `/payouts/{id}/veto` — наложить вето (роль Совета)
- **POST** `/payouts/{id}/confirm` — подтвердить выплату (2FA)
- **GET** `/payouts?status=` — список выплат тенанта по статусу

## Модель данных (черновик)
- **payouts** — `tenant_id`, `member_id`, `share`, `status`, `veto_until`, `audit_hash`, `created_at`

## Зависимости
- Contribution Ledger (доли распределения)
- Сервис аутентификации (2FA/TOTP), RBAC
- Private Blockchain Auditor, Notification Gateway, платёжный шлюз

## Безопасность и мультитенантность
- Ни одна выплата не исполняется без истечения окна вето и подтверждения 2FA
- Право вето и подтверждения ограничено ролью Совета (RBAC)
- Все решения (вето/подтверждение/исполнение) фиксируются в аудите

## Связанные задачи (issue)
- [#39](https://github.com/xlabtg/Media_Center/issues/39) — queue_manager + veto_manager (окно вето) (`type:feature`)
- [#40](https://github.com/xlabtg/Media_Center/issues/40) — Поток подтверждения 2FA для выплат (`type:feature`)
- [#41](https://github.com/xlabtg/Media_Center/issues/41) — Коннекторы: платёжный, блокчейн-аудит, уведомления (`type:feature`)
- [#42](https://github.com/xlabtg/Media_Center/issues/42) — REST API + E2E-тесты сценария вето (`type:feature`)
- [#43](https://github.com/xlabtg/Media_Center/issues/43) — 💸 HITL Payout Gateway (`type:epic`)
- [#88](https://github.com/xlabtg/Media_Center/issues/88) — E2E-тесты HITL и выплат (`type:test`)

## Связанные документы
- [GOVERNANCE.md](../GOVERNANCE.md)
- [SECURITY.md](../SECURITY.md)
- [ECONOMICS.md](../ECONOMICS.md)
- [Детальный план разработки](../DEVELOPMENT_PLAN.md)

---
<sub>Черновик спецификации. Детализируется на этапе проектирования соответствующего модуля. Сгенерировано `experiments/gen_module_docs.py`.</sub>
