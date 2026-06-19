# Neuro-Agent Orchestrator

**Статус:** 🟢 реализовано для epic #58 и контуров #55, #56, #57, #64, #65 · **Этап:** Этап 3 — Расширенные модули · **Компонент:** `component:neuro-agent`

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
- **GET** `/agents/explanations` — XAI-журнал объяснений решений AI для Совета
- **GET/PUT** `/thresholds` — чтение и обновление порогов Совета для автономных
  AI-действий
- **POST** `/rag/documents` — добавить tenant-scoped документы в Agentic RAG
- **PUT/GET** `/proxy-pools/{pool_id}` — управление tenant-scoped прокси-пулом
- **POST** `/proxy-pools/{pool_id}/lease` — выдача следующего живого прокси
- **POST** `/proxy-pools/{pool_id}/health-checks` — фиксация проверки живости

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

## Реализованный контур issue #57

Прокси-ротация добавляет инфраструктурный контур устойчивого доступа к
площадкам через HTTP/SOCKS5/MTProto proxy endpoint без хранения секретов в
публичных моделях ответа.

- `ProxyProtocol` фиксирует поддержанные типы: `http`, `socks5`, `mtproto`;
  схема `url` валидируется по выбранному протоколу, а credentials в URL
  запрещены — для них используется `secret_ref`.
- `ProxyPoolState` хранит tenant-scoped состояние пула: `pool_id`, platform,
  revision, rotation cursor, counts живых/неживых/disabled proxy и список
  `ProxyEndpointState` с `redacted_url`, `url_hash` и `secret_ref_hash`.
- `ProxyRotationManager` выдаёт lease через round-robin только по живым proxy;
  если health-check пометил endpoint как `unhealthy`, он исключается из
  `/proxy-pools/{pool_id}/lease` до следующей успешной проверки.
- `ProxyHealthSignal` обновляет живость endpoint через
  `/proxy-pools/{pool_id}/health-checks`; failures увеличивают
  `consecutive_failures`, successes сбрасывают счётчик отказов.
- Одинаковые `pool_id` у разных tenant изолированы по `tenant_id`, поэтому
  tenant A не видит proxy endpoint, cursor и health state tenant B.
- Audit/events `neuro_agent.proxy_pool.updated`,
  `neuro_agent.proxy_health.checked` и `neuro_agent.proxy.leased` содержат
  только `proxy_id`, protocol, counts и hash-ссылки, без raw URL и `secret_ref`.

## Реализованный контур issue #64

Agentic RAG, DeepResearch и Content Agent (CUA) расширяют тот же backend-контур
Neuro-Agent Orchestrator без отдельного сервиса.

- `POST /rag/documents` принимает `RagDocumentInput`, рассчитывает
  deterministic embedding и пишет документы через общий `TenantVectorStore`
  (`InMemoryTenantVectorStore` локально или ChromaDB-backed реализация). Vector
  metadata содержит `tenant_id`, `source_type`, `content_hash` и hash
  `source_ref`; raw content хранится только в vector store.
- `agentic_rag` принимает `AgenticRagQueryRequest` и возвращает
  `AgenticRagAnswer`: tenant-scoped `context_items`, `retrieval_count`,
  `query_hash`, `evidence_hash` и краткий ответ на основе найденных документов.
  Запросы tenant A не читают документы tenant B, потому что поиск идёт через
  tenant-aware collection/filter слой ChromaDB.
- `deep_research` принимает `DeepResearchRequest` и формирует
  `DeepResearchDraft` с `draft_status=drafted`, outline, citations и
  `requires_human_review=true`; публикация черновика остаётся вне автономного
  действия агента.
- `content_agent_action` принимает `ContentAgentActionRequest` и возвращает
  `ContentAgentActionPlan` только как proposal: `approval_status` всегда
  `awaiting_human_approval`, `requires_human_approval=true`,
  `auto_executed=false`, а raw workspace/target refs заменяются hash-ссылками.
- Audit/events `neuro_agent.rag.documents.upserted`,
  `neuro_agent.rag.query.completed`, `neuro_agent.deep_research.draft.created`
  и `neuro_agent.content_agent.action_proposed` не содержат raw content, query,
  source refs или CUA target refs.

## Реализованный контур issue #65

XAI-аудит решений AI добавлен в Neuro-Agent Orchestrator как часть каждого
`AgentRun`, без отдельного сервиса и без сырых ПДн/контента в audit/events.

- `DecisionExplanation` сопровождает каждый запуск агента: хранит `run_id`,
  `task_type`, `policy_decision`, `policy_revision`, краткое объяснение,
  `reason_codes`, безопасные `input_facts`, `evidence_refs`, `action_refs`,
  `explanation_hash` и `audit_hash`.
- Объяснения привязаны к агентским действиям через `action_refs`: авто-ответы
  ссылаются на `trigger_id`, контент-гигиена — на `content_id`, RAG — на
  `query_id`, DeepResearch — на `research_id`, CUA — на `action_id`.
- Совет, Президиум и Правление читают tenant-scoped журнал через
  `GET /agents/explanations`; обычные участники не получают доступ к журналу.
- Audit metadata каждого AI-решения содержит пространство
  `neuro_agent.decision_explanation`, `decision_explanation_id`,
  `decision_explanation_hash` и hash summary; события получают только id/hash
  объяснения.
- `input_facts` и `evidence_refs` используют агрегаты, reason codes и hash-ref:
  raw recipient refs, content, source refs, workspace refs и target refs не
  попадают в audit/events.

## Зависимости
- Policy Manager (пороги и этические правила)
- Agentic RAG/ChromaDB, инфраструктура резервных каналов

## Безопасность и мультитенантность
- Все автономные действия ограничены порогами Совета и логируются
- Соблюдение ToS площадок и ФЗ обязательно (см. COMPLIANCE)
- Решения AI сопровождаются `DecisionExplanation` (XAI) для проверки Советом

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
<sub>Спецификация синхронизирована с реализацией Neuro-Agent Orchestrator для issue #58, issue #64 и issue #65.</sub>
