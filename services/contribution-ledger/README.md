# Contribution Ledger & Weight Engine

**Статус:** каркас сервиса, реализация запланирована в этапе 2.

## Назначение

Contribution Ledger & Weight Engine фиксирует вклад участников, начисляет
баллы, рассчитывает коэффициент влияния Кв и готовит данные для выплат под
контролем HITL Payout Gateway.

## Границы ответственности

- владеет таблицами `contributions`, `tenant_weights`,
  `payout_distributions`;
- рассчитывает баллы и Кв по правилам экономической модели;
- экспортирует распределение долей без прямого исполнения выплат;
- отправляет audit-запросы только через Private Blockchain Auditor.

## Связанные документы

- [Спецификация модуля](../../docs/modules/contribution-ledger.md)
- [Экономическая модель](../../docs/ECONOMICS.md)
- [Модель данных](../../docs/DATA_MODEL.md)
