# Acceptance snapshot этапа 3

Дата фиксации: 2026-06-19.

Статус: acceptance snapshot для issue #66.

Документ закрывает эпик [#66](https://github.com/xlabtg/Media_Center/issues/66)
как итоговую фиксацию готовности расширенных модулей. Он не заменяет
документы задач
[#54](https://github.com/xlabtg/Media_Center/issues/54),
[#58](https://github.com/xlabtg/Media_Center/issues/58),
[#59](https://github.com/xlabtg/Media_Center/issues/59),
[#60](https://github.com/xlabtg/Media_Center/issues/60),
[#61](https://github.com/xlabtg/Media_Center/issues/61),
[#62](https://github.com/xlabtg/Media_Center/issues/62),
[#63](https://github.com/xlabtg/Media_Center/issues/63),
[#64](https://github.com/xlabtg/Media_Center/issues/64) и
[#65](https://github.com/xlabtg/Media_Center/issues/65), а собирает их в один
проверяемый gate перед переходом к этапу 4.

## 1. Решение по этапу 3

Этап 3 считается завершенным как in-memory контур расширенных модулей поверх
микросервисов этапа 2:

- Activity Command Center и Policy Manager версионируют пороги Совета,
  применяют политики к фактам AI/автоматизации и ставят спорные задачи в
  tenant-scoped очередь операционного, стратегического или адаптивного контура;
- Neuro-Agent Orchestrator реализует аудиторию/парсинг, вовлечение,
  контент-гигиену, публикационную аналитику, прокси-ротацию, Agentic RAG,
  DeepResearch, Content Agent (CUA) и XAI-объяснения решений AI;
- Voice-to-Chain принимает голосовой ввод, транскрибирует его через
  Whisper.cpp-compatible adapter, пишет hash-only запись в Private Blockchain
  Auditor и удаляет исходное аудио по TTL не позднее 24 часов;
- Wallet Module фиксирует операции МСЦ, баланс участника и hash-only audit/event
  payload без сумм и member id в публикуемых событиях;
- Analytics Engine считает KPI пилота и агрегаты по категориям участия,
  контента, вовлеченности и действий с tenant isolation;
- Notification Gateway доставляет уведомления участникам и Совету по
  preferences/templates и публикует sanitized event без rendered subject/body.

Решение: можно переходить к этапу 4 и строить веб-кабинет, панель Совета,
дашборды, онбординг и голосовой UI поверх зафиксированных REST/event contracts.
Реальные внешние площадки, платёжные операции, продуктивные ПДн, публичные
публикации, production LLM/tools и pilot launch остаются запрещены до
compliance/security/integration gates этапов 5-7.

## 2. Трассировка задач #54, #58-#65

| Issue | Результат | Основные артефакты |
|-------|-----------|--------------------|
| #54 | Activity Command Center реализует пороги Совета, постановку задач, статусы очереди и три контура обратной связи. | [services/activity-command-center/activity_command_center/api.py](../services/activity-command-center/activity_command_center/api.py), [services/activity-command-center/activity_command_center/command_center.py](../services/activity-command-center/activity_command_center/command_center.py), [tests/test_activity_command_center_epic_acceptance_contract.py](../tests/test_activity_command_center_epic_acceptance_contract.py) |
| #58, #55, #56, #57 | Neuro-Agent Orchestrator закрывает epic автономных агентов: аудитория, авто-ответы, контент-гигиена, аналитика публикаций и устойчивость доставки через proxy pools. | [services/neuro-agent-orchestrator/neuro_agent_orchestrator/api.py](../services/neuro-agent-orchestrator/neuro_agent_orchestrator/api.py), [services/neuro-agent-orchestrator/neuro_agent_orchestrator/orchestrator.py](../services/neuro-agent-orchestrator/neuro_agent_orchestrator/orchestrator.py), [services/neuro-agent-orchestrator/neuro_agent_orchestrator/proxy_rotation.py](../services/neuro-agent-orchestrator/neuro_agent_orchestrator/proxy_rotation.py), [tests/test_neuro_agent_orchestrator_epic_acceptance_contract.py](../tests/test_neuro_agent_orchestrator_epic_acceptance_contract.py) |
| #59 | Voice-to-Chain реализует локальную транскрипцию, hash-only blockchain audit и TTL-очистку сырого аудио за 24 часа. | [services/voice-to-chain/voice_to_chain/api.py](../services/voice-to-chain/voice_to_chain/api.py), [services/voice-to-chain/voice_to_chain/service.py](../services/voice-to-chain/voice_to_chain/service.py), [tests/test_voice_to_chain_issue59_acceptance_contract.py](../tests/test_voice_to_chain_issue59_acceptance_contract.py) |
| #60 | Wallet Module хранит операции МСЦ и баланс участника, а наружные события не раскрывают суммы и member id. | [services/wallet/wallet/api.py](../services/wallet/wallet/api.py), [infra/db/alembic/versions/wallet_operations_0004.py](../infra/db/alembic/versions/wallet_operations_0004.py), [tests/test_wallet_issue60_acceptance_contract.py](../tests/test_wallet_issue60_acceptance_contract.py) |
| #61 | Analytics Engine принимает нормализованные события, считает KPI пилота и возвращает tenant-scoped агрегаты. | [services/analytics-engine/analytics_engine/api.py](../services/analytics-engine/analytics_engine/api.py), [tests/test_analytics_engine_issue61_acceptance_contract.py](../tests/test_analytics_engine_issue61_acceptance_contract.py), [docs/ROADMAP.md](ROADMAP.md) |
| #62 | Notification Gateway доставляет уведомления по preferences/templates и публикует sanitized dispatch event. | [services/notification-gateway/notification_gateway/api.py](../services/notification-gateway/notification_gateway/api.py), [tests/test_notification_gateway_issue62_acceptance_contract.py](../tests/test_notification_gateway_issue62_acceptance_contract.py) |
| #63 | Policy Manager версионирует политики Совета, применяет threshold rules и аудирует обновления. | [services/policy-manager/policy_manager/api.py](../services/policy-manager/policy_manager/api.py), [services/policy-manager/policy_manager/manager.py](../services/policy-manager/policy_manager/manager.py), [tests/test_policy_manager_issue63_acceptance_contract.py](../tests/test_policy_manager_issue63_acceptance_contract.py) |
| #64 | Agentic RAG, DeepResearch и Content Agent (CUA) работают tenant-scoped и не исполняют CUA-действия без human approval. | [services/neuro-agent-orchestrator/neuro_agent_orchestrator/api.py](../services/neuro-agent-orchestrator/neuro_agent_orchestrator/api.py), [tests/test_neuro_agent_orchestrator_issue64_acceptance_contract.py](../tests/test_neuro_agent_orchestrator_issue64_acceptance_contract.py) |
| #65 | XAI-аудит решений AI добавляет `DecisionExplanation` и council-only журнал объяснений. | [services/neuro-agent-orchestrator/neuro_agent_orchestrator/orchestrator.py](../services/neuro-agent-orchestrator/neuro_agent_orchestrator/orchestrator.py), [tests/test_neuro_agent_orchestrator_issue65_acceptance_contract.py](../tests/test_neuro_agent_orchestrator_issue65_acceptance_contract.py) |
| #66 | Родительский epic покрыт сквозным stage-3 acceptance contract, который связывает политики, очередь задач, AI-онбординг, голос, кошелек, KPI и уведомления. | [tests/test_stage3_acceptance_contract.py](../tests/test_stage3_acceptance_contract.py), [docs/STAGE_3_ACCEPTANCE.md](STAGE_3_ACCEPTANCE.md) |

## 3. Критерии завершения эпика #66

| Критерий issue #66 | Статус | Проверяемые ссылки |
|--------------------|--------|--------------------|
| Работает онбординг и базовая автоматизация под порогами Совета | Выполнено: acceptance-тест обновляет политику риска, синхронизирует пороги Activity/Neuro, эскалирует AI-onboarding task в очередь Совета и разрешает низкорисковый приветственный авто-ответ с XAI-объяснением. | [tests/test_stage3_acceptance_contract.py](../tests/test_stage3_acceptance_contract.py), [tests/test_activity_command_center_epic_acceptance_contract.py](../tests/test_activity_command_center_epic_acceptance_contract.py), [tests/test_neuro_agent_orchestrator_epic_acceptance_contract.py](../tests/test_neuro_agent_orchestrator_epic_acceptance_contract.py), [tests/test_policy_manager_issue63_acceptance_contract.py](../tests/test_policy_manager_issue63_acceptance_contract.py) |
| Голос превращается в хэш в блокчейне с авто-удалением исходника | Выполнено: Voice-to-Chain получает аудио, возвращает transcript hash, пишет `voice.transcript.recorded` в blockchain transport только с hash-only metadata и удаляет raw audio после 24 часов. | [tests/test_stage3_acceptance_contract.py](../tests/test_stage3_acceptance_contract.py), [tests/test_voice_to_chain_issue59_acceptance_contract.py](../tests/test_voice_to_chain_issue59_acceptance_contract.py), [services/voice-to-chain/voice_to_chain/api.py](../services/voice-to-chain/voice_to_chain/api.py) |
| Считаются KPI и работают уведомления | Выполнено: Analytics Engine считает 8 KPI пилота за период, Notification Gateway доставляет срочное уведомление Совету по двум каналам, а dispatch event не содержит rendered subject/body. | [tests/test_stage3_acceptance_contract.py](../tests/test_stage3_acceptance_contract.py), [tests/test_analytics_engine_issue61_acceptance_contract.py](../tests/test_analytics_engine_issue61_acceptance_contract.py), [tests/test_notification_gateway_issue62_acceptance_contract.py](../tests/test_notification_gateway_issue62_acceptance_contract.py) |
| Кошелек МСЦ связан с операционным контуром этапа 3 | Выполнено: stage-3 acceptance фиксирует ручную МСЦ-корректировку по onboarding task и проверяет баланс участника, audit record и sanitized wallet event. | [tests/test_stage3_acceptance_contract.py](../tests/test_stage3_acceptance_contract.py), [tests/test_wallet_issue60_acceptance_contract.py](../tests/test_wallet_issue60_acceptance_contract.py), [services/wallet/wallet/api.py](../services/wallet/wallet/api.py) |

## 4. Gate перед этапом 4

Этап 4 может стартовать при следующих условиях:

- веб-кабинет, панель Совета, дашборды, AI-ассистент и voice UI используют
  зафиксированные REST contracts этапа 3 без прямого обхода Policy Manager,
  Activity Command Center, XAI и RBAC gates;
- любые действия, которые меняют статусы участников, деньги/МСЦ, публичный
  контент, массовые рассылки, политики или пороги, остаются за
  council/HITL approval;
- UI показывает policy version, audit hash, XAI explanation, approval status и
  veto/queue status там, где действие требует решения Совета;
- raw voice, transcript payload, raw content, source refs, platform tokens,
  суммы и ПДн не попадают в blockchain audit, доменные события, логи, метрики и
  уведомления;
- TTL-очистка сырого голоса не позднее 24 часов остается обязательной даже при
  добавлении объектного хранилища и фоновых workers;
- production persistence, реальные очереди RabbitMQ, ChromaDB/LLM/tools и
  внешние каналы подключаются без изменения публичных схем, уже покрытых
  in-memory tests;
- публичные интеграции, реальные платежи, обработка продуктивных ПДн и pilot
  launch ждут gates этапов 5-7.

## 5. Локальная проверка

Минимальный локальный acceptance для этапа 3:

```bash
pytest tests/test_stage3_acceptance_contract.py
```

Полная проверка stage-3 service contracts:

```bash
pytest \
  tests/test_activity_command_center_epic_acceptance_contract.py \
  tests/test_neuro_agent_orchestrator_epic_acceptance_contract.py \
  tests/test_neuro_agent_orchestrator_issue64_acceptance_contract.py \
  tests/test_neuro_agent_orchestrator_issue65_acceptance_contract.py \
  tests/test_voice_to_chain_issue59_acceptance_contract.py \
  tests/test_wallet_issue60_acceptance_contract.py \
  tests/test_analytics_engine_issue61_acceptance_contract.py \
  tests/test_notification_gateway_issue62_acceptance_contract.py \
  tests/test_policy_manager_issue63_acceptance_contract.py \
  tests/test_stage3_acceptance_contract.py
```

Перед финальным ревью PR должен также проходить общий локальный CI:

```bash
ruff check .
ruff format --check .
black --check .
mypy .
pytest
```

## 6. Открытые ограничения

Этап 3 завершает функциональный in-memory контур расширенных модулей, но не
является разрешением на пилот или production automation. До этапов 5-7 ещё
нужны production persistence, интеграции с реальными площадками, платёжными
шлюзами и LLM/tools, security review, legal/compliance review по ПДн и
контенту, incident runbook, нагрузочные проверки и UX-поверхность для
человеческого контроля.
