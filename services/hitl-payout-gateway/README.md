# HITL Payout Gateway

**Статус:** доменный слой очереди, вето, 2FA, коннекторы исполнения и REST API
реализованы для in-memory сценариев этапа 2.

## Назначение

HITL Payout Gateway управляет очередью выплат, окном вето Совета,
2FA-подтверждением и интеграциями с платёжными коннекторами. Финальные действия
с деньгами не выполняются без Human-in-the-Loop контроля.

## Границы ответственности

- принимает подготовленные распределения от Contribution Ledger;
- хранит состояния выплат, veto decisions и approval sessions;
- запускает уведомления и подтверждения для Совета;
- требует TOTP-подтверждение операции `payout.confirm` перед финальным
  исполнением;
- отправляет в audit-chain только SHA256-хэши и технические метаданные.

## Реализованный слой

- `hitl_payout_gateway.queue_manager` ставит выплаты в очередь, рассчитывает
  `veto_until` из `VETO_WINDOW_HOURS` (по умолчанию 8 ч, допустимо 4-12 ч) и
  блокирует переход к исполнению до закрытия окна.
- `hitl_payout_gateway.confirmation_manager` принимает 2FA-подтверждение
  операции `payout.confirm`, проверяет роль `council`, фиксирует audit hash и
  публикует `payout.confirmed`.
- `hitl_payout_gateway.veto_manager` принимает решение вето только в открытом
  окне, переводит выплату в `canceled`, сохраняет `decision_id` и audit hash.
- `hitl_payout_gateway.execution_manager` исполняет готовую выплату через
  платёжный коннектор, передаёт SHA256-хэш операции в blockchain-audit
  коннектор, отправляет уведомление участнику и публикует `payout.executed`.
- При сбое платёжного, blockchain-audit или notification коннектора менеджер
  пишет audit record `payout.failed`, логирует структурированное предупреждение,
  публикует `payout.failed` и оставляет выплату в переисполняемом статусе.
- Переход к исполнению возможен только после закрытия окна вето и сохранённого
  2FA-подтверждения.
- Публикуются события `payout.queued`, `payout.confirmed`, `payout.vetoed`,
  `payout.executed` и `payout.failed` по общему `EventEnvelope`-контракту.
- Audit records не содержат денежных сумм и персональных данных: участники и
  причины решений представлены SHA256-хэшами и техническими метаданными.

## REST API

FastAPI-приложение создаётся через `hitl_payout_gateway.create_hitl_payout_app`
или entrypoint `hitl_payout_gateway_app.main:app`. Все рабочие endpoint требуют
JWT tenant context и роль `council`.

- `POST /payouts/queue` ставит выплату в очередь и возвращает `PayoutQueueItem`.
- `GET /payouts?status=` возвращает выплаты текущего tenant, опционально
  отфильтрованные по `queued`, `ready_to_execute`, `canceled` или `executed`.
- `GET /payouts/{payout_id}` возвращает одну выплату текущего tenant.
- `POST /payouts/{payout_id}/veto` отменяет queued-выплату в открытом окне вето.
- `POST /payouts/{payout_id}/confirm` проверяет TOTP-код для операции
  `payout.confirm` по server-side in-memory registry секретов и сохраняет
  2FA-подтверждение.
- `POST /payouts/{payout_id}/execute` исполняет подтверждённую выплату после
  закрытия окна вето через in-memory коннекторы.

API возвращает общий error envelope. Ключевые доменные коды:
`payout_not_found`, `veto_window_closed`, `payout_not_executable`,
`payout_connector_failed`, `hitl_payout_error`.

Для локального entrypoint in-memory TOTP registry можно задать через
`HITL_TOTP_TENANT_ID`, `HITL_TOTP_SUBJECT` и `HITL_TOTP_SECRET`. В тестах и
ручной сборке приложения тот же registry передаётся параметром `totp_secrets` в
`create_hitl_payout_app`.

## Связанные документы

- [Спецификация модуля](../../docs/modules/hitl-payout-gateway.md)
- [ADR-0005: HITL](../../docs/adr/0005-hitl-for-sensitive-operations.md)
- [Комплаенс](../../docs/COMPLIANCE.md)
