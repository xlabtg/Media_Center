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

| Сервис | Назначение |
|--------|------------|
| [api-gateway](api-gateway/) | Единая tenant-aware точка входа для клиентов и внутренних вызовов. |
| [contribution-ledger](contribution-ledger/) | Учёт вклада, расчёт баллов, Кв и долей выплат. |
| [cglr](cglr/) | Генерация контента и маршрутизация ссылок. |
| [hitl-payout-gateway](hitl-payout-gateway/) | Выплаты под контролем человека: вето, 2FA, коннекторы. |
| [activity-command-center](activity-command-center/) | Пороги Совета, очереди задач и контуры обратной связи. |
| [analytics-engine](analytics-engine/) | KPI пилота и агрегаты активности, контента, вовлечённости и действий. |
| [policy-manager](policy-manager/) | Централизованные политики, пороги Совета и применение актуальных правил. |
| [messenger-adapter](messenger-adapter/) | Публикация и трансформация контента для площадок. |
| [notification-gateway](notification-gateway/) | Уведомления участников и Совета по событиям, каналам и шаблонам. |
| [blockchain-auditor](blockchain-auditor/) | Неизменяемый аудит SHA256-хэшей и метаданных. |
| [wallet](wallet/) | Внутренний учёт МСЦ, балансов и операций участника. |
| [web-cabinet](web-cabinet/) | Личный кабинет пайщика: вклад, баланс МСЦ, история, контент и ссылки. |
