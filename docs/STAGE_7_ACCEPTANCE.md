# Acceptance snapshot этапа 7

Дата фиксации: 2026-06-20.

Статус: acceptance snapshot для issue #91.

Документ фиксирует готовность ограниченного пилотного запуска на tenant
`nmc-pilot`. Он не является разрешением на production launch: реальные ПДн,
площадочные credentials, выплаты и массовые публикации включаются только после
ручной go/no-go Совета, security/compliance review и проверки наблюдаемости.

## 1. Решение по этапу 7

Этап 7 считается готовым к ограниченному пилоту:

- tenant `nmc-pilot` создан как `pilot_ready` и описан в
  `infra/local/fixtures/pilot-tenant.json`;
- зарегистрированы 20 synthetic handles, что попадает в приемочный диапазон
  15-25 участников;
- участники находятся в онбординге со статусами `scheduled`, `in_progress` и
  `ready_for_review`;
- Роли и пороги Совета заданы: `council`, `presidium`, `board`,
  `member_full`, `member_assoc`, кворум 2/3 и окно вето 8 часов;
- KPI пилота зафиксированы для tenant dashboard;
- rollback описан без удаления audit history.

## 2. Критерии приемки issue #91

| Критерий | Статус | Проверяемые ссылки |
|----------|--------|--------------------|
| Тенант создан и настроен | Выполнено: fixture фиксирует tenant id, slug `nmc-pilot`, статус `pilot_ready`, политику synthetic data и launch window. | [infra/local/fixtures/pilot-tenant.json](../infra/local/fixtures/pilot-tenant.json), [docs/PILOT_TENANT_ONBOARDING.md](PILOT_TENANT_ONBOARDING.md) |
| 15-25 участников зарегистрированы и онбордятся | Выполнено: в pilot fixture 20 участников, каждый имеет `registered` status, роль, куратора и обязательный onboarding checklist. | [tests/test_pilot_tenant_issue91_acceptance_contract.py](../tests/test_pilot_tenant_issue91_acceptance_contract.py) |
| Роли и пороги Совета заданы | Выполнено: fixture задает RBAC-распределение, стратегический кворум 2/3, 8-часовое окно вето, 2FA и approvals для чувствительных операций. | [docs/GOVERNANCE.md](GOVERNANCE.md), [infra/local/fixtures/pilot-tenant.json](../infra/local/fixtures/pilot-tenant.json) |

## 3. Gate перед фактическим запуском

Перед включением реальных каналов Совет проводит ручной go/no-go:

- все реальные contacts/consents загружаются только через tenant-scoped
  onboarding flow, без коммита ПДн в репозиторий;
- внешние площадки остаются за Messenger Adapter и platform registry;
- выплаты остаются в HITL-контуре с 2FA и окном вето;
- tenant dashboard показывает labels `tenant_id`, `service`, `operation`,
  `status`;
- audit trail содержит только SHA256-хэши и metadata;
- rollback plan проверен на dry-run.

## 4. Локальная проверка

```bash
pytest tests/test_pilot_tenant_issue91_acceptance_contract.py
```

Полный PR gate остается стандартным:

```bash
ruff check .
ruff format --check .
black --check .
mypy .
pytest
```
