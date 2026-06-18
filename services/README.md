# Сервисы НМЦ

Каталог содержит каркас микросервисов и gateway. Реализация будет добавляться
по задачам этапов 1-2, а текущие README фиксируют назначение и границы владения.

[`service-template`](service-template/) не является продуктовым сервисом. Это
эталонный FastAPI-шаблон для создания новых сервисов с healthcheck, метриками,
tenant middleware, DB settings и структурой миграций.

| Сервис | Назначение |
|--------|------------|
| [api-gateway](api-gateway/) | Единая tenant-aware точка входа для клиентов и внутренних вызовов. |
| [contribution-ledger](contribution-ledger/) | Учёт вклада, расчёт баллов, Кв и долей выплат. |
| [cglr](cglr/) | Генерация контента и маршрутизация ссылок. |
| [hitl-payout-gateway](hitl-payout-gateway/) | Выплаты под контролем человека: вето, 2FA, коннекторы. |
| [messenger-adapter](messenger-adapter/) | Публикация и трансформация контента для площадок. |
| [blockchain-auditor](blockchain-auditor/) | Неизменяемый аудит SHA256-хэшей и метаданных. |
