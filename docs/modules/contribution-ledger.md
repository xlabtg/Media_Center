# Contribution Ledger & Weight Engine

**Статус:** 🟡 планируется · **Этап:** Этап 2 — Ключевые микросервисы · **Компонент:** `component:contribution-ledger`

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
- **GET** `/payout-distribution?period=` — доли распределения для выплат

## Модель данных (черновик)
- **contributions** — `tenant_id`, `member_id`, `type`, `points`, `metadata`, `audit_hash`, `created_at`
- **tenant_weights** — `tenant_id`, `member_id`, `kv_raw`, `kv_capped`, `period`

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
- [Детальный план разработки](../DEVELOPMENT_PLAN.md)

---
<sub>Черновик спецификации. Детализируется на этапе проектирования соответствующего модуля. Сгенерировано `experiments/gen_module_docs.py`.</sub>
