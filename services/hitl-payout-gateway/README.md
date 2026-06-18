# HITL Payout Gateway

**Статус:** доменный слой очереди, вето, 2FA и коннекторы исполнения
реализован; REST API планируется отдельной задачей этапа 2.

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

## Связанные документы

- [Спецификация модуля](../../docs/modules/hitl-payout-gateway.md)
- [ADR-0005: HITL](../../docs/adr/0005-hitl-for-sensitive-operations.md)
- [Комплаенс](../../docs/COMPLIANCE.md)
