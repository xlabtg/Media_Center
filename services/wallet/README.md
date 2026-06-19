# Wallet

Сервис ведёт внутренний tenant-scoped учёт МСЦ: записывает операции участника,
считает баланс и возвращает историю операций. МСЦ остаётся внутренней метрикой,
не криптовалютой и не командой на реальную выплату.

## Интерфейсы

- `POST /wallet/operations` — записать МСЦ-операцию с `Idempotency-Key`,
  audit hash и ссылкой на payout distribution или payout.
- `GET /wallet/balance?member_id=` — получить баланс МСЦ участника.
- `GET /wallet/operations?member_id=&ref_type=&ref_id=` — получить историю
  операций tenant с фильтрами.

`create_wallet_app` собирает FastAPI-приложение на общем
`ServiceTemplateConfig`. Для быстрых тестов и локального контура используется
`InMemoryWalletRepository`; production persistence добавляется без изменения
REST-контракта.

## Безопасность

- Все записи и чтения используют проверенный `tenant_id` из JWT и shared
  tenant middleware.
- Запись операций доступна ролям `council` и `board`; участник читает свой
  баланс, роли управления могут читать баланс и историю tenant.
- Audit/event payload не содержит `member_id`; наружу публикуется
  `member_hash`, `operation_id`, reference и `audit_hash`.
- `amount_mcv` и `balance_after_mcv` входят в локальный audit hash для
  проверяемости операции, но не передаются в event payload и не являются
  денежной суммой.
