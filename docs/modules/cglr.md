# Content Generator & Link Router (CGLR)

**Статус:** 🟡 планируется · **Этап:** Этап 2 — Ключевые микросервисы · **Компонент:** `component:cglr`

Генерация публикуемого контента по шаблонам и маршрутизация многоуровневых реферальных ссылок (L1/L2/L3) с учётом вклада.

## Зона ответственности
- Рендеринг контента по шаблонам Jinja2 (sandboxed) и его валидация
- Генерация и ротация реферальных ссылок уровней L1/L2/L3
- Применение реферальной политики распределения
- Логирование факта генерации как вклада в Contribution Ledger

## Ключевые правила и формулы
- Реферальные уровни: **L1 = 20 %**, **L2 = 10 %**, **L3 = 5 %**

## Основные интерфейсы
- **POST** `/generate` — сгенерировать контент по шаблону и данным
- **GET** `/content/{id}` — получить готовый контент с встроенными ссылками

## Модель данных (черновик)
- **templates** — `tenant_id`, `name`, `body`, `version`
- **generated_content** — `tenant_id`, `template_id`, `payload`, `links`, `created_at`

## Зависимости
- Contribution Ledger & Weight Engine (логирование вклада)
- Unified Messenger Adapter (инъекция ссылок при публикации)
- Jinja2, PostgreSQL

## Безопасность и мультитенантность
- Шаблоны исполняются в песочнице Jinja2 (защита от инъекций)
- Реферальные ссылки и шаблоны изолированы по `tenant_id`

## Связанные задачи (issue)
- [#35](https://github.com/xlabtg/Media_Center/issues/35) — template_engine: рендеринг и валидация (Jinja2) (`type:feature`)
- [#36](https://github.com/xlabtg/Media_Center/issues/36) — link_rotator: реферальные ссылки L1/L2/L3 (`type:feature`)
- [#37](https://github.com/xlabtg/Media_Center/issues/37) — API CGLR + contribution_logger + тесты (`type:feature`)
- [#38](https://github.com/xlabtg/Media_Center/issues/38) — ✍️ Content Generator & Link Router (CGLR) (`type:epic`)

## Связанные документы
- [ECONOMICS.md](../ECONOMICS.md)
- [ARCHITECTURE.md](../ARCHITECTURE.md)
- [Детальный план разработки](../DEVELOPMENT_PLAN.md)

---
<sub>Черновик спецификации. Детализируется на этапе проектирования соответствующего модуля. Сгенерировано `experiments/gen_module_docs.py`.</sub>
