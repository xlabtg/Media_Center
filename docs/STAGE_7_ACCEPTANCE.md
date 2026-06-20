# Acceptance snapshot этапа 7

Дата фиксации: 2026-06-20.

Статус: acceptance snapshot для issue #96.

Snapshot агрегирует приемку issue #91, issue #92, issue #93, issue #94 и
issue #95.

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
- KPI пилота собираются через telemetry collector и доступны в отчёте Совету;
- пользовательская документация опубликована: руководство участника,
  отдельная инструкция Совета и FAQ пилота;
- поддержка пилота работает по SLA, severity matrix P0-P3 и release gate через
  CI;
- ретроспектива пилота проведена, выводы согласованы Советом, а план
  масштабирования этапа 8 утверждён как `approved_for_stage_8`;
- rollback описан без удаления audit history.

## 2. Критерии приемки issue #91

| Критерий | Статус | Проверяемые ссылки |
|----------|--------|--------------------|
| Тенант создан и настроен | Выполнено: fixture фиксирует tenant id, slug `nmc-pilot`, статус `pilot_ready`, политику synthetic data и launch window. | [infra/local/fixtures/pilot-tenant.json](../infra/local/fixtures/pilot-tenant.json), [docs/PILOT_TENANT_ONBOARDING.md](PILOT_TENANT_ONBOARDING.md) |
| 15-25 участников зарегистрированы и онбордятся | Выполнено: в pilot fixture 20 участников, каждый имеет `registered` status, роль, куратора и обязательный onboarding checklist. | [tests/test_pilot_tenant_issue91_acceptance_contract.py](../tests/test_pilot_tenant_issue91_acceptance_contract.py) |
| Роли и пороги Совета заданы | Выполнено: fixture задает RBAC-распределение, стратегический кворум 2/3, 8-часовое окно вето, 2FA и approvals для чувствительных операций. | [docs/GOVERNANCE.md](GOVERNANCE.md), [infra/local/fixtures/pilot-tenant.json](../infra/local/fixtures/pilot-tenant.json) |

## 3. Критерии приемки issue #92

| Критерий | Статус | Проверяемые ссылки |
|----------|--------|--------------------|
| KPI и телеметрия собираются автоматически | Выполнено: `POST /analytics/pilot/telemetry/collect` принимает batch collector, превращает KPI в `analytics.event_recorded` и сохраняет usage/incidents telemetry. | [services/analytics-engine/README.md](../services/analytics-engine/README.md), [tests/test_pilot_kpi_telemetry_issue92_acceptance_contract.py](../tests/test_pilot_kpi_telemetry_issue92_acceptance_contract.py) |
| Отчёты доступны Совету | Выполнено: `GET /analytics/pilot/reports?period=` доступен роли `council` и возвращает KPI, агрегаты, usage summary, incidents summary и feedback-loop статус. | [docs/modules/analytics-engine.md](modules/analytics-engine.md), [tests/test_pilot_kpi_telemetry_issue92_acceptance_contract.py](../tests/test_pilot_kpi_telemetry_issue92_acceptance_contract.py) |
| Данные изолированы по тенанту | Выполнено: tenant-isolation контракт #92 проверяет, что данные другого tenant не попадают в council report, а подмена `X-Tenant-Id` возвращает `403 tenant_isolation_violation`. | [tests/test_pilot_kpi_telemetry_issue92_acceptance_contract.py](../tests/test_pilot_kpi_telemetry_issue92_acceptance_contract.py) |

## 4. Критерии приемки issue #93

| Критерий | Статус | Проверяемые ссылки |
|----------|--------|--------------------|
| Документация опубликована и доступна | Выполнено: материалы добавлены в навигацию README и launch packet пилота. | [docs/USER_GUIDE.md](USER_GUIDE.md), [docs/COUNCIL_GUIDE.md](COUNCIL_GUIDE.md), [docs/FAQ.md](FAQ.md), [README.md](../README.md) |
| Покрыты ключевые сценарии | Выполнено: руководство участника описывает быстрый старт, онбординг 12-36 часов, вклад, МСЦ, согласия, ПДн, безопасность и поддержку; FAQ закрывает вопросы участников, Совета, правил и инцидентов. | [docs/USER_GUIDE.md](USER_GUIDE.md), [docs/FAQ.md](FAQ.md), [tests/test_user_docs_issue93_acceptance_contract.py](../tests/test_user_docs_issue93_acceptance_contract.py) |
| Совет имеет отдельные инструкции | Выполнено: инструкция Совета фиксирует роли `council`/`presidium`/`board`, ежедневный цикл, HITL, окно вето 8 часов, 2FA, пороги, KPI, ручной go/no-go и compliance gate. | [docs/COUNCIL_GUIDE.md](COUNCIL_GUIDE.md), [docs/GOVERNANCE.md](GOVERNANCE.md), [tests/test_user_docs_issue93_acceptance_contract.py](../tests/test_user_docs_issue93_acceptance_contract.py) |

## 5. Критерии приемки issue #94

| Критерий | Статус | Проверяемые ссылки |
|----------|--------|--------------------|
| Поддержка и приём обращений | Выполнено: `support-intake`, `security-privacy`, `council-escalation` и `ci-release-gate` имеют владельцев, SLA ответа и safe evidence policy без ПДн/секретов. | [docs/PILOT_SUPPORT_RUNBOOK.md](PILOT_SUPPORT_RUNBOOK.md), [infra/local/fixtures/pilot-support-queue.json](../infra/local/fixtures/pilot-support-queue.json) |
| Триаж и приоритизация дефектов | Выполнено: severity matrix P0-P3 задаёт response/fix SLA, эскалацию Совету и обязательный CI для P0/P1; очередь содержит P0 `tenant_isolation` и P1 пилотные кейсы. | [docs/PILOT_SUPPORT_RUNBOOK.md](PILOT_SUPPORT_RUNBOOK.md), [tests/test_pilot_support_issue94_acceptance_contract.py](../tests/test_pilot_support_issue94_acceptance_contract.py) |
| Выпуск исправлений | Выполнено: bugfix records связывают кейс, воспроизводящий тест, workflow `CI`, rollback и monitoring window 24-48 часов. | [infra/local/fixtures/pilot-support-queue.json](../infra/local/fixtures/pilot-support-queue.json), [tests/test_pilot_support_issue94_acceptance_contract.py](../tests/test_pilot_support_issue94_acceptance_contract.py) |

## 6. Критерии приемки issue #95

| Критерий | Статус | Проверяемые ссылки |
|----------|--------|--------------------|
| Ретроспектива проведена и задокументирована | Выполнено: `pilot-retro-2026-06-20` фиксирует KPI `2026-W26`, incidents summary, вопросы онбординга, поддержку, документацию и ограничения запуска без ПДн. | [docs/PILOT_RETROSPECTIVE_SCALE_PLAN.md](PILOT_RETROSPECTIVE_SCALE_PLAN.md), [tests/test_pilot_retrospective_issue95_acceptance_contract.py](../tests/test_pilot_retrospective_issue95_acceptance_contract.py) |
| Выводы согласованы с Советом | Выполнено: решение Совета принято с кворумом 2/3, статус `approved_for_stage_8`, без разрешения на production launch с реальными данными. | [docs/PILOT_RETROSPECTIVE_SCALE_PLAN.md](PILOT_RETROSPECTIVE_SCALE_PLAN.md), [docs/COUNCIL_GUIDE.md](COUNCIL_GUIDE.md) |
| План масштабирования утверждён | Выполнено: план этапа 8 разложен на workstreams #97-#102, gates, rollback и владельцев. | [docs/PILOT_RETROSPECTIVE_SCALE_PLAN.md](PILOT_RETROSPECTIVE_SCALE_PLAN.md), [docs/DEVELOPMENT_PLAN.md](DEVELOPMENT_PLAN.md) |

## 7. Gate перед фактическим запуском

Перед включением реальных каналов Совет проводит ручной go/no-go:

- все реальные contacts/consents загружаются только через tenant-scoped
  onboarding flow, без коммита ПДн в репозиторий;
- внешние площадки остаются за Messenger Adapter и platform registry;
- выплаты остаются в HITL-контуре с 2FA и окном вето;
- tenant dashboard показывает labels `tenant_id`, `service`, `operation`,
  `status`;
- pilot telemetry collector пишет usage/incidents без ПДн и публикует отчёт
  Совету по расписанию weekly/monthly;
- audit trail содержит только SHA256-хэши и metadata;
- участникам и Совету выданы актуальные [docs/USER_GUIDE.md](USER_GUIDE.md),
  [docs/COUNCIL_GUIDE.md](COUNCIL_GUIDE.md), [docs/FAQ.md](FAQ.md) и
  [docs/PILOT_SUPPORT_RUNBOOK.md](PILOT_SUPPORT_RUNBOOK.md);
- итоги ретроспективы и план масштабирования опубликованы в
  [docs/PILOT_RETROSPECTIVE_SCALE_PLAN.md](PILOT_RETROSPECTIVE_SCALE_PLAN.md);
- P0/P1 support queue проверена, открытые критичные дефекты отсутствуют или
  имеют зафиксированный workaround, владельца, rollback и CI-backed fix plan;
- rollback plan проверен на dry-run.

## 8. Локальная проверка

```bash
pytest tests/test_stage7_acceptance_contract.py
pytest tests/test_pilot_tenant_issue91_acceptance_contract.py
pytest tests/test_pilot_kpi_telemetry_issue92_acceptance_contract.py
pytest tests/test_user_docs_issue93_acceptance_contract.py
pytest tests/test_pilot_support_issue94_acceptance_contract.py
pytest tests/test_pilot_retrospective_issue95_acceptance_contract.py
```

Полный PR gate остается стандартным:

```bash
ruff check .
ruff format --check .
black --check .
mypy .
pytest
```
