# Neuro-Agent Orchestrator

**Статус:** 🟢 реализовано для контуров #55 и #56 · **Этап:** Этап 3 — Расширенные модули · **Компонент:** `component:neuro-agent`

Оркестрация автономных ИИ-агентов под порогами Совета: работа с аудиторией, вовлечение, контент-гигиена, аналитика, устойчивость доставки.

## Зона ответственности
- Подмодуль «Аудитория & Парсинг» — анализ аудитории по открытым данным
- Подмодуль «Вовлечение & Авто-ответы» — реакции по шаблонам
- Подмодуль «Контент & Гигиена» — проверки качества и безопасности
- Подмодуль «Аналитика & Оптимизация» — рекомендации (под контролем)
- Политики ретраев и резервные разрешенные каналы для устойчивости доставки

## Основные интерфейсы
- **POST** `/agents/run` — запустить задачу агента в рамках порогов
- **GET** `/agents/status` — статус и результаты агентов
- **GET/PUT** `/thresholds` — чтение и обновление порогов Совета для автономных
  AI-действий

## Реализованный контур issue #55

Спецификация синхронизирована с реализацией Neuro-Agent Orchestrator:
`create_neuro_agent_orchestrator_app`,
`services/neuro-agent-orchestrator/neuro_agent_orchestrator/orchestrator.py` и
`services/neuro-agent-orchestrator/neuro_agent_orchestrator/api.py`.

- `AudienceSource` принимает только открытые источники с
  `access_scope=public`, `tos_status=allowed`, допустимым правовым основанием и
  пустым `personal_data_fields`; приватные импорты и источники с ПДн получают
  ошибку `pdn_scope_violation`.
- `AudienceProfile` агрегирует reach, engagement rate, topic tags, legal basis и
  evidence hash без сырого handle, телефона, контакта или пользовательского
  идентификатора.
- `AutoReplyDecision` исполняет шаблонный авто-ответ только если
  `risk_score`, `agent_confidence`, `estimated_recipients` и `template_key`
  проходят текущие пороги Совета; иначе запуск получает
  `needs_council_review`.
- Пороги Совета версионируются tenant-scoped моделью `CouncilThresholds`, а
  каждое изменение публикует `neuro_agent.thresholds.updated`.
- Авто-ответы и профили аудитории пишут hash-only audit records и события
  `neuro_agent.audience_profile.created`, `neuro_agent.auto_reply.sent` или
  `neuro_agent.auto_reply.escalated`.

## Реализованный контур issue #56

Контент-гигиена и аналитика публикаций расширяют тот же контракт `/agents/run`
и `/agents/status` новыми типами задач:

- `content_hygiene` принимает `ContentHygieneRequest`, рассчитывает качество и
  safety risk, возвращает `ContentHygieneAssessment` с `content_hash`,
  `author_ref_hash`, флагами и причинами политики без сырого текста публикации.
  Небезопасный или некачественный материал получает статус
  `needs_council_review` и событие `neuro_agent.content_hygiene.flagged`.
- `publication_optimization` принимает метрики публикации, формирует
  `PublicationAnalyticsReport` с engagement rate, CTR, conversion rate,
  performance band и списком рекомендаций.
- Рекомендации по оптимизации помечаются как `proposed` или
  `needs_council_review`; поле `auto_applied` всегда `false`, а применение
  требует человеческого подтверждения через `requires_human_approval=true`.
- Порог `min_content_quality_score` входит в `CouncilThresholds`, а рисковые
  рекомендации ограничиваются существующими `max_autonomous_risk_score` и
  `min_agent_confidence`.

## Зависимости
- Policy Manager (пороги и этические правила)
- Agentic RAG/ChromaDB, инфраструктура резервных каналов

## Безопасность и мультитенантность
- Все автономные действия ограничены порогами Совета и логируются
- Соблюдение ToS площадок и ФЗ обязательно (см. COMPLIANCE)
- Решения AI сопровождаются объяснением (XAI) для проверки Советом

## Связанные задачи (issue)
- [#55](https://github.com/xlabtg/Media_Center/issues/55) — Аудитория и парсинг + вовлечение и авто-ответы (`type:feature`)
- [#56](https://github.com/xlabtg/Media_Center/issues/56) — Контент и гигиена + аналитика и оптимизация (`type:feature`)
- [#57](https://github.com/xlabtg/Media_Center/issues/57) — Резервные каналы и политики ретраев (`type:feature`)
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
