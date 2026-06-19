# Сервисы НМЦ

Каталог содержит каркас микросервисов и gateway. Реализация добавляется
по задачам этапов, а текущие README фиксируют назначение и границы владения.

[`service-template`](service-template/) не является продуктовым сервисом. Это
эталонный FastAPI-шаблон для создания новых сервисов с healthcheck, метриками,
tenant middleware, DB settings и структурой миграций.

| Сервис | Назначение |
|--------|------------|
| [api-gateway](api-gateway/) | Единая tenant-aware точка входа для клиентов и внутренних вызовов. |
| [contribution-ledger](contribution-ledger/) | Учёт вклада, расчёт баллов, Кв и долей выплат. |
| [cglr](cglr/) | Генерация контента и маршрутизация ссылок. |
| [hitl-payout-gateway](hitl-payout-gateway/) | Выплаты под контролем человека: вето, 2FA, коннекторы. |
| [activity-command-center](activity-command-center/) | Пороги Совета, очереди задач и контуры обратной связи. |
| [analytics-engine](analytics-engine/) | KPI пилота и агрегаты активности, контента, вовлечённости и действий. |
| [messenger-adapter](messenger-adapter/) | Публикация и трансформация контента для площадок. |
| [blockchain-auditor](blockchain-auditor/) | Неизменяемый аудит SHA256-хэшей и метаданных. |
| [wallet](wallet/) | Внутренний учёт МСЦ, балансов и операций участника. |
