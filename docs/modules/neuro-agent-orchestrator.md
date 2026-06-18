# Neuro-Agent Orchestrator

**Статус:** 🟡 планируется · **Этап:** Этап 3 — Расширенные модули · **Компонент:** `component:neuro-agent`

Оркестрация автономных ИИ-агентов под порогами Совета: работа с аудиторией, вовлечение, контент-гигиена, аналитика, анонимность.

## Зона ответственности
- Подмодуль «Аудитория & Парсинг» — анализ аудитории по открытым данным
- Подмодуль «Вовлечение & Авто-ответы» — реакции по шаблонам
- Подмодуль «Контент & Гигиена» — проверки качества и безопасности
- Подмодуль «Аналитика & Оптимизация» — рекомендации (под контролем)
- Ротация прокси (HTTP/SOCKS5/MTProto) для устойчивости и анонимности

## Основные интерфейсы
- **POST** `/agents/run` — запустить задачу агента в рамках порогов
- **GET** `/agents/status` — статус и результаты агентов

## Зависимости
- Policy Manager (пороги и этические правила)
- Agentic RAG/ChromaDB, прокси-инфраструктура

## Безопасность и мультитенантность
- Все автономные действия ограничены порогами Совета и логируются
- Соблюдение ToS площадок и ФЗ обязательно (см. COMPLIANCE)
- Решения AI сопровождаются объяснением (XAI) для проверки Советом

## Связанные задачи (issue)
- [#55](https://github.com/xlabtg/Media_Center/issues/55) — Аудитория и парсинг + вовлечение и авто-ответы (`type:feature`)
- [#56](https://github.com/xlabtg/Media_Center/issues/56) — Контент и гигиена + аналитика и оптимизация (`type:feature`)
- [#57](https://github.com/xlabtg/Media_Center/issues/57) — Ротация прокси (HTTP/SOCKS5/MTProto) (`type:feature`)
- [#58](https://github.com/xlabtg/Media_Center/issues/58) — 🧠 Neuro-Agent Orchestrator (`type:epic`)
- [#64](https://github.com/xlabtg/Media_Center/issues/64) — Agentic RAG / DeepResearch / Content Agent (CUA) (`type:feature`)
- [#65](https://github.com/xlabtg/Media_Center/issues/65) — XAI-аудит решений AI (объяснимость) (`type:feature`)

## Связанные документы
- [COMPLIANCE.md](../COMPLIANCE.md)
- [GOVERNANCE.md](../GOVERNANCE.md)
- [SECURITY.md](../SECURITY.md)
- [Детальный план разработки](../DEVELOPMENT_PLAN.md)

---
<sub>Черновик спецификации. Детализируется на этапе проектирования соответствующего модуля. Сгенерировано `experiments/gen_module_docs.py`.</sub>
