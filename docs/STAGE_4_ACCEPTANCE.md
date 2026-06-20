# Acceptance snapshot этапа 4

Дата фиксации: 2026-06-20.

Статус: acceptance snapshot для issue #74.

Документ закрывает эпик [#74](https://github.com/xlabtg/Media_Center/issues/74)
как итоговую фиксацию готовности клиентских приложений и UX. Он не заменяет
документы задач
[#67](https://github.com/xlabtg/Media_Center/issues/67),
[#68](https://github.com/xlabtg/Media_Center/issues/68),
[#69](https://github.com/xlabtg/Media_Center/issues/69),
[#70](https://github.com/xlabtg/Media_Center/issues/70),
[#71](https://github.com/xlabtg/Media_Center/issues/71),
[#72](https://github.com/xlabtg/Media_Center/issues/72) и
[#73](https://github.com/xlabtg/Media_Center/issues/73), а собирает их в один
проверяемый gate перед переходом к этапу 5.

## 1. Решение по этапу 4

Этап 4 считается завершенным как in-memory клиентский контур поверх сервисных
REST/domain contracts этапов 2-3:

- Веб-кабинет пайщика показывает вклад, баланс МСЦ, историю операций, контент и
  реферальные ссылки в пределах tenant пользователя;
- Панель Совета управляет HITL-выплатами: показывает очередь, окно вето, риск,
  audit timeline, 2FA-статус и позволяет менять политики/пороги;
- Дашборд KPI визуализирует метрики Analytics Engine, периодные срезы,
  категории и CSV-выгрузку;
- Онбординг ведет нового участника по 12-36-часовому сценарию, показывает
  прогресс, согласия, readiness и ответы AI-ассистента;
- Telegram-клиент дает участнику входящий канал `/start`, `/help`, `/status`,
  `/balance`, `/tasks` с tenant-scoped шифрованием идентичности и proxy
  rotation;
- UI голосового ассистента принимает запись через MediaRecorder, передает audio
  payload в Voice-to-Chain и показывает transcript/hash/TTL receipt без
  хранения сырого аудио в Web Cabinet;
- Дизайн-система `nmc-ui` фиксирует токены, UI-компоненты, общий CSS layer и
  accessibility baseline для всех клиентских экранов.

Решение: можно переходить к этапу 5 и подключать реальные внешние интеграции,
не меняя публичные UI/API contracts этапа 4. Реальные площадочные аккаунты,
платежные шлюзы, production ПДн, продуктивные LLM/tools, pilot launch и
массовые действия остаются запрещены до gates этапов 5-7.

## 2. Трассировка задач #67-#73

| Issue | Результат | Основные артефакты |
|-------|-----------|--------------------|
| #67 | Веб-кабинет пайщика показывает вклад, баланс, историю операций, контент и реферальные ссылки с tenant isolation. | [services/web-cabinet/web_cabinet/api.py](../services/web-cabinet/web_cabinet/api.py), [tests/test_web_cabinet_issue67_acceptance_contract.py](../tests/test_web_cabinet_issue67_acceptance_contract.py), [docs/screenshots/web-cabinet-issue67-desktop.png](screenshots/web-cabinet-issue67-desktop.png) |
| #68 | Панель Совета управляет HITL-очередью, вето, порогами Policy Manager, 2FA-подтверждениями и audit timeline. | [services/web-cabinet/web_cabinet/api.py](../services/web-cabinet/web_cabinet/api.py), [tests/test_council_panel_issue68_acceptance_contract.py](../tests/test_council_panel_issue68_acceptance_contract.py), [docs/screenshots/council-panel-issue68-desktop.png](screenshots/council-panel-issue68-desktop.png) |
| #69 | Дашборд KPI визуализирует метрики Analytics Engine, категории, периоды и CSV export. | [services/analytics-engine/analytics_engine/api.py](../services/analytics-engine/analytics_engine/api.py), [tests/test_analytics_dashboard_issue69_acceptance_contract.py](../tests/test_analytics_dashboard_issue69_acceptance_contract.py), [docs/screenshots/analytics-dashboard-issue69-desktop.png](screenshots/analytics-dashboard-issue69-desktop.png) |
| #70 | Онбординг участника отслеживает шаги, согласия, окно 12-36 часов, readiness и AI-ответы. | [services/web-cabinet/web_cabinet/api.py](../services/web-cabinet/web_cabinet/api.py), [tests/test_onboarding_issue70_acceptance_contract.py](../tests/test_onboarding_issue70_acceptance_contract.py), [docs/screenshots/onboarding-issue70-desktop.png](screenshots/onboarding-issue70-desktop.png) |
| #71 | Telegram-клиент участника реализует базовые команды, AES-256-GCM identity binding, tenant-scoped hashes и proxy rotation. | [services/messenger-adapter/messenger_adapter/telegram_client.py](../services/messenger-adapter/messenger_adapter/telegram_client.py), [tests/test_telegram_client_issue71_acceptance_contract.py](../tests/test_telegram_client_issue71_acceptance_contract.py), [examples/telegram_client_demo.py](../examples/telegram_client_demo.py) |
| #72 | UI голосового ассистента добавляет экран записи, отправку audio payload в Voice-to-Chain и отображение hash/retention receipt. | [services/web-cabinet/web_cabinet/api.py](../services/web-cabinet/web_cabinet/api.py), [tests/test_voice_assistant_issue72_acceptance_contract.py](../tests/test_voice_assistant_issue72_acceptance_contract.py), [docs/screenshots/voice-assistant-issue72.png](screenshots/voice-assistant-issue72.png) |
| #73 | Дизайн-система и UI-кит фиксируют токены, компоненты, CSS layer и accessibility baseline `nmc-ui`. | [services/web-cabinet/web_cabinet/design_system.py](../services/web-cabinet/web_cabinet/design_system.py), [tests/test_design_system_issue73_acceptance_contract.py](../tests/test_design_system_issue73_acceptance_contract.py), [docs/screenshots/design-system-issue73.png](screenshots/design-system-issue73.png) |
| #74 | Родительский epic покрыт сквозным stage-4 acceptance contract, который связывает кабинет, Совет, дашборды, онбординг, Telegram, voice UI и дизайн-систему. | [tests/test_stage4_acceptance_contract.py](../tests/test_stage4_acceptance_contract.py), [docs/STAGE_4_ACCEPTANCE.md](STAGE_4_ACCEPTANCE.md) |

## 3. Критерии завершения эпика #74

| Критерий issue #74 | Статус | Проверяемые ссылки |
|--------------------|--------|--------------------|
| Совет управляет вето и порогами через панель | Выполнено: stage-4 acceptance открывает `/council/panel/overview`, проверяет очередь HITL, `requires_2fa`, доступность вето, обновляет `hitl.veto_window_hours` через `PUT /council/policies/{key}` и накладывает вето через `POST /council/payouts/{payout_id}/veto`. | [tests/test_stage4_acceptance_contract.py](../tests/test_stage4_acceptance_contract.py), [tests/test_council_panel_issue68_acceptance_contract.py](../tests/test_council_panel_issue68_acceptance_contract.py) |
| Пайщик видит вклад, баланс и историю | Выполнено: stage-4 acceptance открывает `/cabinet/overview`, проверяет tenant, баллы вклада, баланс МСЦ, порядок истории операций и контент участника. | [tests/test_stage4_acceptance_contract.py](../tests/test_stage4_acceptance_contract.py), [tests/test_web_cabinet_issue67_acceptance_contract.py](../tests/test_web_cabinet_issue67_acceptance_contract.py) |
| Новый участник проходит онбординг | Выполнено: stage-4 acceptance открывает `/onboarding/overview`, проверяет окно 24 часа, 100% прогресс, readiness `ready_for_review` и включенный AI-ассистент. | [tests/test_stage4_acceptance_contract.py](../tests/test_stage4_acceptance_contract.py), [tests/test_onboarding_issue70_acceptance_contract.py](../tests/test_onboarding_issue70_acceptance_contract.py) |
| Дашборды, voice UI, Telegram и дизайн-система связаны с клиентским контуром | Выполнено: stage-4 acceptance проверяет HTML KPI-дашборда с `nmc-ui`, дизайн-компоненты `AppShell`/`MetricTile`/`HITLQueueItem`/`ConsentControl` и Telegram `/balance` через proxy lease без сырого Telegram ID в ответной модели. Voice UI и дизайн-система дополнительно покрыты отдельными issue contracts. | [tests/test_stage4_acceptance_contract.py](../tests/test_stage4_acceptance_contract.py), [tests/test_analytics_dashboard_issue69_acceptance_contract.py](../tests/test_analytics_dashboard_issue69_acceptance_contract.py), [tests/test_telegram_client_issue71_acceptance_contract.py](../tests/test_telegram_client_issue71_acceptance_contract.py), [tests/test_voice_assistant_issue72_acceptance_contract.py](../tests/test_voice_assistant_issue72_acceptance_contract.py), [tests/test_design_system_issue73_acceptance_contract.py](../tests/test_design_system_issue73_acceptance_contract.py) |

## 4. Gate перед этапом 5

Этап 5 может стартовать при следующих условиях:

- внешние Telegram/VK/Dzen/OK интеграции подключаются через существующие
  adapter/gateway contracts без обхода tenant isolation, шифрования токенов и
  proxy policy;
- платежные шлюзы и приватная blockchain-сеть подключаются за текущими
  Wallet/HITL/Blockchain Auditor interfaces, чтобы панель Совета продолжала
  видеть вето, 2FA, policy version и audit hash;
- реальные KPI и выгрузки подключаются к Analytics Engine без изменения
  публичных `/analytics/dashboard/*` схем;
- voice UI продолжает отправлять сырой звук только в Voice-to-Chain, а наружу
  возвращает hash/retention receipt без transcript payload в blockchain
  metadata;
- дизайн-система `nmc-ui` остается общей основой клиентских экранов, а новые
  integration UI используют те же токены, focus states и responsive markers;
- продуктивные ПДн, площадочные credentials, raw Telegram ID, raw audio,
  платежные суммы и закрытые audit payload не попадают в HTML, события, логи,
  метрики и PR-артефакты.

## 5. Локальная проверка

Минимальный локальный acceptance для этапа 4:

```bash
pytest tests/test_stage4_acceptance_contract.py
```

Полная проверка клиентских contracts этапа 4:

```bash
pytest \
  tests/test_web_cabinet_issue67_acceptance_contract.py \
  tests/test_council_panel_issue68_acceptance_contract.py \
  tests/test_analytics_dashboard_issue69_acceptance_contract.py \
  tests/test_onboarding_issue70_acceptance_contract.py \
  tests/test_telegram_client_issue71_acceptance_contract.py \
  tests/test_voice_assistant_issue72_acceptance_contract.py \
  tests/test_design_system_issue73_acceptance_contract.py \
  tests/test_stage4_acceptance_contract.py
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

Этап 4 завершает проверяемый in-memory клиентский UX-контур, но не является
разрешением на production launch. До этапов 5-7 еще нужны реальные интеграции
площадок и платежей, production persistence, security review, legal/compliance
review по ПДн и контенту, нагрузочные проверки, incident runbooks,
наблюдаемость внешних каналов и пилотный запуск на ограниченном tenant.
