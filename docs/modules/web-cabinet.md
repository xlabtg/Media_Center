# Web Cabinet

**Статус:** 🟢 реализовано для #67 · **Этап:** Этап 4 — Клиентские приложения и UX · **Компонент:** `component:web-cabinet`

Личный кабинет пайщика показывает вклад, баланс МСЦ, историю операций, контент
и реферальные ссылки в пределах tenant пользователя.

## Зона ответственности

- Обзор вклада пайщика за период: баллы, Кв, доля распределения и число
  событий.
- Баланс МСЦ и история операций из Wallet Module.
- Список собственного контента и связанных реферальных ссылок L1/L2/L3.
- Адаптивный HTML-интерфейс для первого клиентского экрана.

## Основные интерфейсы

- **GET** `/cabinet/overview` — JSON-обзор кабинета за `period=<YYYY-MM>`.
- **GET** `/cabinet` — адаптивный HTML-интерфейс кабинета за
  `period=<YYYY-MM>`.

Оба endpoint принимают опциональный `member_id`: пайщик может читать только
свой кабинет, а роли `council`, `presidium`, `board` — кабинет участника внутри
своего tenant.

## Модель данных

- **CabinetContributionRecord** — `tenant_id`, `member_id`, `period`,
  `total_points`, `avg_points_council`, `kv_raw`, `kv_capped`,
  `payout_share`, `contribution_count`.
- **CabinetContentRecord** — `tenant_id`, `owner_id`, `content_id`,
  `template_id`, `title`, `preview`, `content_hash`, `platform_targets`,
  `referral_links`, `points_awarded`, `created_at`.
- **WalletBalanceResponse / WalletOperationResponse** — используются напрямую
  из Wallet Module, чтобы баланс и история соответствовали backend.

## Безопасность и мультитенантность

- Все запросы проходят через tenant middleware и проверенный JWT.
- `member_full` и `member_assoc` читают только собственный кабинет.
- Управляющие роли читают другие кабинеты только в пределах текущего tenant.
- tenant-isolation контракт #67 покрывает подмену `X-Tenant-Id` и отсутствие
  данных другого tenant в ответах.

## Реализация

- [services/web-cabinet/web_cabinet/api.py](../../services/web-cabinet/web_cabinet/api.py) —
  REST API, HTML-рендер, in-memory projection repository и RBAC.
- [services/web-cabinet/README.md](../../services/web-cabinet/README.md) —
  запуск и границы сервиса.
- [tests/test_web_cabinet_issue67_acceptance_contract.py](../../tests/test_web_cabinet_issue67_acceptance_contract.py) —
  acceptance-тест #67.

## Связанные задачи (issue)

- [#67](https://github.com/xlabtg/Media_Center/issues/67) — Веб-кабинет пайщика (вклад, баланс, история)
- [#60](https://github.com/xlabtg/Media_Center/issues/60) — Wallet Module: учёт МСЦ и операций

---
<sub>Спецификация синхронизирована с реализацией Web Cabinet для issue #67.</sub>
