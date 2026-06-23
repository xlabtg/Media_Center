# Сервисы НМЦ

Каталог содержит каркас микросервисов и gateway. Реализация добавляется
по задачам этапов, а текущие README фиксируют назначение и границы владения.

[`service-template`](service-template/) не является продуктовым сервисом. Это
эталонный FastAPI-шаблон для создания новых сервисов с healthcheck, метриками,
tenant middleware, DB settings и структурой миграций.

## Единый ASGI-entrypoint

Каждый продуктовый сервис должен иметь модуль `*_app/main.py`, который:

- строит `app` через общий `create_base_app()` контракт;
- экспортирует ASGI-объект `app` для тестов и внешних ASGI-runner;
- содержит `run()`, вызывающий `uvicorn.run(app, host=APP_HOST, port=APP_PORT)`;
- использует порт `7700` по умолчанию через `APP_PORT`;
- поддерживает запуск командой `python -m <service_app>.main`.

Пример для сервиса учёта вклада:

```bash
PYTHONPATH=services/contribution-ledger:. \
JWT_SECRET=local-jwt-secret \
python -m contribution_ledger_app.main
```

Если `APP_HOST` не задан, сервис слушает `0.0.0.0`. Если `APP_PORT` не задан,
используется единый runtime-порт `7700`.

| Сервис | Entrypoint | Назначение |
|--------|------------|------------|
| [activity-command-center](activity-command-center/) | `activity_command_center_app.main` | Пороги Совета, очереди задач и контуры обратной связи. |
| [analytics-engine](analytics-engine/) | `analytics_engine_app.main` | KPI пилота и агрегаты активности, контента, вовлечённости и действий. |
| [api-gateway](api-gateway/) | `api_gateway_app.main` | Единая tenant-aware точка входа для клиентов и внутренних вызовов. |
| [blockchain-auditor](blockchain-auditor/) | `blockchain_auditor_app.main` | Неизменяемый аудит SHA256-хэшей и метаданных. |
| [cglr](cglr/) | `cglr_app.main` | Генерация контента и маршрутизация ссылок. |
| [contribution-ledger](contribution-ledger/) | `contribution_ledger_app.main` | Учёт вклада, расчёт баллов, Кв и долей выплат. |
| [hitl-payout-gateway](hitl-payout-gateway/) | `hitl_payout_gateway_app.main` | Выплаты под контролем человека: вето, 2FA, коннекторы. |
| [messenger-adapter](messenger-adapter/) | `messenger_adapter_app.main` | Публикация и трансформация контента для площадок. |
| [neuro-agent-orchestrator](neuro-agent-orchestrator/) | `neuro_agent_orchestrator_app.main` | RAG, прокси-ротация и оркестрация нейроагентов. |
| [notification-gateway](notification-gateway/) | `notification_gateway_app.main` | Уведомления участников и Совета по событиям, каналам и шаблонам. |
| [policy-manager](policy-manager/) | `policy_manager_app.main` | Централизованные политики, пороги Совета и применение актуальных правил. |
| [voice-to-chain](voice-to-chain/) | `voice_to_chain_app.main` | Голосовые поручения, транскрипция и hash-only аудит в цепочке. |
| [wallet](wallet/) | `wallet_app.main` | Внутренний учёт МСЦ, балансов и операций участника. |
| [web-cabinet](web-cabinet/) | `web_cabinet_app.main` | Личный кабинет пайщика: вклад, баланс МСЦ, история, контент и ссылки. |
