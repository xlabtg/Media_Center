# Contribution Ledger & Weight Engine

**Статус:** 🟢 реализовано · **Этап:** Этап 2 — Ключевые микросервисы · **Компонент:** `component:contribution-ledger`

Учёт вклада участников в баллах, расчёт коэффициента влияния Кв с потолком и формирование долей распределения с неизменяемым аудитом.

## Зона ответственности
- Приём и фиксация событий вклада (контент, действия, вовлечение)
- Расчёт баллов вклада по утверждённой формуле
- Расчёт коэффициента влияния Кв с ограничением сверху (анти-монополия)
- Формирование долей распределения для HITL Payout Gateway
- Генерация аудит-хэша каждого события и публикация в блокчейн-аудит

## Ключевые правила и формулы
- `final_points = round(base × platform_mult × reach_mult × amp_mult, 2)`
- `Кв = min(баллы / среднее_по_Совету; 0.10)` — потолок `COUNCIL_CAP_KV = 0.10`
- `payout_share = kv_capped / Σ kv_capped`

## Основные интерфейсы
- **POST** `/contributions` — зарегистрировать вклад (возвращает баллы и audit_hash)
- **GET** `/weights?period=` — веса Кв (raw/capped) по участникам тенанта
- **POST** `/weights/recalculate` — пересчитать и сохранить snapshot весов
- **GET** `/payout-distribution?period=` — доли распределения для выплат

Все доменные endpoints требуют Bearer JWT, `X-Tenant-Id`,
`X-Correlation-Id`, а mutating operations используют `Idempotency-Key` там,
где результат должен быть воспроизводимым.

## Компоненты реализации
- `points_calculator` — расчёт баллов по таблицам `BASE_POINTS` и
  `PLATFORM_MULTIPLIERS` с reach/amplification multipliers.
- `weight_engine` — расчёт `kv_raw`, `kv_capped`, нормализация
  `payout_share` и защита потолком `COUNCIL_CAP_KV = 0.10`.
- `payout_exporter` — immutable snapshot распределения для HITL,
  `distribution_hash` и событие `payout.distribution_ready`.
- `contribution_events` — публикация `contribution.recorded` и
  `audit.record.requested` с SHA256 `audit_hash`.
- `api` — FastAPI REST-слой с tenant middleware, idempotency,
  validation/error envelope и in-memory repository для тестового контура.

## Модель данных (черновик)
- **contributions** — `tenant_id`, `member_id`, `event_type`, `source_ref`,
  `points_awarded`, `metadata`, `audit_hash`, `idempotency_key`, `occurred_at`,
  `created_at`
- **tenant_weights** — `tenant_id`, `member_id`, `period`, `total_points`,
  `avg_points_council`, `kv_raw`, `kv_capped`, `payout_share`,
  `calculation_hash`
- **payout_distributions** — immutable snapshot долей для HITL с
  `total_kv_capped`, `total_payout_share`, `member_count`,
  `distribution_json` и `distribution_hash`
- Канонические индексы и ограничения зафиксированы в
  [DATA_MODEL.md](../DATA_MODEL.md): `idx_contributions_tenant_event_created`,
  `uq_tenant_weights_tenant_member_period`, `ck_tenant_weights_kv_cap`,
  `idx_payout_distributions_tenant_period`,
  `uq_payout_distributions_tenant_hash`

## Зависимости
- Общая библиотека `shared` (модели, `audit_logger`, утилиты тенанта)
- Private Blockchain Auditor (фиксация хэшей)
- RabbitMQ (события вклада), PostgreSQL

## Безопасность и мультитенантность
- Все запросы и записи изолированы по `tenant_id`
- `audit_hash = SHA256(json.dumps({event_type, tenant_id, points, metadata, timestamp}, sort_keys=True))`
- В аудит и блокчейн не попадают денежные суммы и ПДн

## Связанные задачи (issue)
- [#29](https://github.com/xlabtg/Media_Center/issues/29) — points_calculator: расчёт баллов вклада (`type:feature`)
- [#30](https://github.com/xlabtg/Media_Center/issues/30) — weight_engine: коэффициент влияния Кв с потолком 0.10 (`type:feature`)
- [#31](https://github.com/xlabtg/Media_Center/issues/31) — Модель данных contributions/tenant_weights + миграции (`type:feature`)
- [#32](https://github.com/xlabtg/Media_Center/issues/32) — payout_exporter + аудит вклада (SHA256) (`type:feature`)
- [#33](https://github.com/xlabtg/Media_Center/issues/33) — REST API сервиса (Pydantic v2) + тесты (`type:feature`)
- [#34](https://github.com/xlabtg/Media_Center/issues/34) — 📒 Contribution Ledger & Weight Engine (`type:epic`)

## Связанные документы
- [ECONOMICS.md](../ECONOMICS.md)
- [SECURITY.md](../SECURITY.md)
- [ARCHITECTURE.md](../ARCHITECTURE.md)
- [DATA_MODEL.md](../DATA_MODEL.md)
- [Детальный план разработки](../DEVELOPMENT_PLAN.md)

---
<sub>Спецификация синхронизирована с реализацией Contribution Ledger & Weight Engine для этапа 2.</sub>
