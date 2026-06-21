# Acceptance snapshot этапа 6

Дата фиксации: 2026-06-21.

Статус: acceptance snapshot для issue #90.

Snapshot агрегирует приемку issue #83, issue #84, issue #85, issue #86,
issue #87, issue #88 и issue #89.

Документ закрывает эпик
[#90](https://github.com/xlabtg/Media_Center/issues/90) как проверяемый
pre-pilot gate качества, безопасности и нагрузки. Он не является разрешением
на обработку реальных ПДн, production-платежи, массовые публикации или внешний
black-box pentest: фактический пилот остается ручным go/no-go Совета после
security/compliance review, актуального CI и проверки окружения без секретов.

## 1. Решение по этапу 6

Этап 6 считается готовым как QA/security/load пакет:

- качество, безопасность и производительность подтверждены через
  unit/integration/e2e стратегию, CI coverage gate 35 %, security scan и
  stage-6 acceptance contracts;
- тестовые данные изолированы по tenant: `TenantTestIdentity`,
  `build_tenant_test_dataset` и negative paths проверяют `tenant_id` и
  `403 tenant_isolation_violation`;
- достигнут критерий 0 межтенантных утечек в проверенных слоях: БД,
  SQLAlchemy repository, ChromaDB-like vector store, S3/MinIO-like object
  storage, cache, structured logs и audit events;
- нагрузочные цели воспроизводимы в CI baseline: CGLR 100 req/s при p95 < 200 мс,
  Contribution Ledger 50 событий/с, Messenger 200 публикаций/мин при
  > 99 % успеха, HITL 10 очередей/ч и veto p95 < 5 с;
- critical/high findings закрыты: high-находка F-86-01 по audit metadata
  устранена общим `audit_safe_metadata()` и подтверждена повторной проверкой;
- аудит ФЗ-152 пройден для engineering baseline: data map, согласия, DSAR,
  удаление и ограничение обработки доступны в Web Cabinet;
- полный цикл выплаты проходит e2e: расчет вклада, распределение, очередь
  HITL, veto/2FA confirmation, execution, audit hash, notification и negative
  paths;
- отказоустойчивость проверена для PostgreSQL, RabbitMQ, external API и proxy
  через retries, timeout budget, circuit breaker, controlled degradation и
  recovery confirmation.

Решение: можно переходить к этапу 7 и готовить ограниченный пилотный tenant
при соблюдении gate из раздела 9. Репозиторий фиксирует проверяемые контракты,
но не содержит реальные ПДн, secrets, платежные реквизиты, platform tokens,
proxy URL, raw content или реальные суммы выплат.

## 2. Критерии приемки issue #83

| Критерий | Статус | Проверяемые ссылки |
|----------|--------|--------------------|
| Стратегия задокументирована | Выполнено: [docs/TESTING_STRATEGY.md](TESTING_STRATEGY.md) фиксирует пирамиду Unit/Integration/E2E, обязательные negative paths и правила tenant-aware данных. | [docs/TESTING_STRATEGY.md](TESTING_STRATEGY.md), [tests/test_testing_strategy_issue83_contract.py](../tests/test_testing_strategy_issue83_contract.py) |
| Покрытие измеряется в CI | Выполнено: workflow запускает `pytest --cov=libs --cov=services`, публикует `coverage.xml` и держит `--cov-fail-under=35`. | [.github/workflows/ci.yml](../.github/workflows/ci.yml), [requirements-dev.txt](../requirements-dev.txt) |
| Тестовые данные изолированы по tenant | Выполнено: shared fixtures создают owner/foreign tenant, а `assert_only_tenant_records` падает при cross-tenant наборе. | [libs/shared/testing.py](../libs/shared/testing.py), [tests/test_testing_strategy_issue83_contract.py](../tests/test_testing_strategy_issue83_contract.py) |

## 3. Критерии приемки issue #84

| Критерий | Статус | Проверяемые ссылки |
|----------|--------|--------------------|
| Любой межтенантный доступ -> 403 | Выполнено: cross-tenant обращения нормализуются в `403 tenant_isolation_violation` и пишут sanitized audit event. | [docs/modules/tenant-isolation.md](modules/tenant-isolation.md), [tests/test_tenant_isolation_issue84_contract.py](../tests/test_tenant_isolation_issue84_contract.py) |
| 0 утечек на всех слоях | Выполнено: контракт проверяет БД/repository, vector store, object storage, cache и structured logs на отсутствие foreign records, identifiers и payload. | [libs/shared/tenant.py](../libs/shared/tenant.py), [libs/shared/object_storage.py](../libs/shared/object_storage.py), [libs/shared/vector.py](../libs/shared/vector.py) |
| Тесты включены в CI | Выполнено: stage-6 tenant isolation tests входят в общий `pytest` gate и связаны с security baseline. | [docs/SECURITY.md](SECURITY.md), [tests/test_tenant_isolation_issue84_contract.py](../tests/test_tenant_isolation_issue84_contract.py) |

## 4. Критерии приемки issue #85

| Критерий | Статус | Проверяемые ссылки |
|----------|--------|--------------------|
| Целевые показатели достигнуты | Выполнено: `LoadTestReport` проверяет CGLR, Contribution Ledger, Messenger и HITL against target throughput/latency/success ratio. | [docs/LOAD_TESTING.md](LOAD_TESTING.md), [tests/test_load_testing_issue85_acceptance_contract.py](../tests/test_load_testing_issue85_acceptance_contract.py) |
| Узкие места выявлены и устранены/задокументированы | Выполнено: CGLR warmup/ASGI transport и template cache зафиксированы, оставшиеся production bottlenecks вынесены в pilot gate. | [docs/LOAD_TESTING.md](LOAD_TESTING.md), [libs/shared/load_testing.py](../libs/shared/load_testing.py) |
| Сценарии нагрузки воспроизводимы | Выполнено: быстрый контракт запускается pytest, а тот же сценарий доступен как experiment. | [experiments/validate_issue85_load_targets.py](../experiments/validate_issue85_load_targets.py), [tests/test_load_testing_issue85_acceptance_contract.py](../tests/test_load_testing_issue85_acceptance_contract.py) |

## 5. Критерии приемки issue #86

| Критерий | Статус | Проверяемые ссылки |
|----------|--------|--------------------|
| Критические и высокие уязвимости устранены | Выполнено: high-находка F-86-01 закрыта через единый redaction helper для payout audit metadata. | [docs/SECURITY_PENTEST_ISSUE_86.md](SECURITY_PENTEST_ISSUE_86.md), [services/hitl-payout-gateway/hitl_payout_gateway/audit_redaction.py](../services/hitl-payout-gateway/hitl_payout_gateway/audit_redaction.py) |
| Отчет pentest подготовлен | Выполнено: внутренний source-code security audit описывает scope, OWASP Top 10:2025 matrix, finding, fix и остаточные риски. | [docs/SECURITY.md](SECURITY.md), [docs/SECURITY_PENTEST_ISSUE_86.md](SECURITY_PENTEST_ISSUE_86.md) |
| Повторная проверка подтверждает исправления | Выполнено: retest command для audit redaction и контракт security docs закреплены в тестах. | [tests/test_security_contract.py](../tests/test_security_contract.py), [tests/test_hitl_payout_queue_veto.py](../tests/test_hitl_payout_queue_veto.py) |

## 6. Критерии приемки issue #87

| Критерий | Статус | Проверяемые ссылки |
|----------|--------|--------------------|
| Чек-лист ФЗ-152 пройден | Выполнено: `GET /compliance/fz152/checklist` возвращает passed checklist с data map, consent registry, minimization и hash-only audit evidence. | [docs/COMPLIANCE.md](COMPLIANCE.md), [tests/test_compliance_issue87_contract.py](../tests/test_compliance_issue87_contract.py) |
| Выявленные несоответствия устранены | Выполнено: Web Cabinet добавил privacy endpoints для data map, consent registry и DSAR workflow. | [docs/modules/web-cabinet.md](modules/web-cabinet.md), [services/web-cabinet/README.md](../services/web-cabinet/README.md) |
| Процедуры удаления ПДн работают | Выполнено: erasure DSAR удаляет member projection в пределах tenant, сохраняет audit hash и не затрагивает foreign tenant. | [tests/test_compliance_issue87_contract.py](../tests/test_compliance_issue87_contract.py), [docs/COMPLIANCE.md](COMPLIANCE.md) |

## 7. Критерии приемки issue #88

| Критерий | Статус | Проверяемые ссылки |
|----------|--------|--------------------|
| Полный цикл выплаты проходит e2e | Выполнено: контракт создает вклад, пересчитывает веса, строит payout distribution, ставит выплату в очередь, подтверждает через 2FA и исполняет после окна вето. | [tests/test_hitl_payout_issue88_e2e_contract.py](../tests/test_hitl_payout_issue88_e2e_contract.py), [docs/modules/hitl-payout-gateway.md](modules/hitl-payout-gateway.md) |
| Аудит и уведомления формируются | Выполнено: payout events, audit log, blockchain audit record и notification metadata проверяются без раскрытия raw member ids, recipient token, operator token и суммы. | [services/hitl-payout-gateway/hitl_payout_gateway/execution_manager.py](../services/hitl-payout-gateway/hitl_payout_gateway/execution_manager.py), [tests/test_hitl_payout_issue88_e2e_contract.py](../tests/test_hitl_payout_issue88_e2e_contract.py) |
| Негативные сценарии корректны | Выполнено: execution без 2FA, member confirmation, execution vetoed payout, early execution и late veto возвращают ожидаемые ошибки. | [tests/test_hitl_payout_issue88_e2e_contract.py](../tests/test_hitl_payout_issue88_e2e_contract.py), [docs/GOVERNANCE.md](GOVERNANCE.md) |

## 8. Критерии приемки issue #89

| Критерий | Статус | Проверяемые ссылки |
|----------|--------|--------------------|
| Система деградирует контролируемо | Выполнено: PostgreSQL получает readonly/cache fallback, RabbitMQ - durable outbox, external API - stale cache, proxy - disabled route/fallback. | [docs/CHAOS_TESTING.md](CHAOS_TESTING.md), [libs/shared/resilience.py](../libs/shared/resilience.py) |
| Ретраи и таймауты работают | Выполнено: `RetryPolicy` и `TimeoutBudget` ограничивают попытки и зависшие вызовы, результат нормализуется как `DependencyCallResult`. | [tests/test_chaos_resilience_issue89_contract.py](../tests/test_chaos_resilience_issue89_contract.py), [libs/shared/resilience.py](../libs/shared/resilience.py) |
| Восстановление подтверждено | Выполнено: `CircuitBreakerPolicy` открывает контур после отказов и закрывает его после успешной recovery probe. | [docs/CHAOS_TESTING.md](CHAOS_TESTING.md), [tests/test_chaos_resilience_issue89_contract.py](../tests/test_chaos_resilience_issue89_contract.py) |

## 9. Gate перед пилотом

Перед фактическим запуском этапа 7 Совет, security/compliance и SRE проводят
ручной go/no-go:

- актуальный commit проходит `ruff`, `black --check`, `mypy`, `pytest` с
  coverage gate, SCA, secret scan и container scan;
- stage-6 quick contracts #83-#89 зелёные локально и в CI;
- external black-box pentest production/sandbox окружения запланирован или
  проведён для реального deployment target, а risk acceptance оформлен Советом;
- юридический review ФЗ-152 подтверждает оператора/обработчика, уведомление
  Роскомнадзора или письменное обоснование исключения, публичные документы и
  контакты субъекта ПДн;
- load gate повторен на pilot-like compose с PostgreSQL, Redis, RabbitMQ,
  network limits и rate-limit stubs, а p95/throughput приложены как sanitized
  evidence;
- tenant isolation smoke покрывает все включаемые tenant'ы и storage backends;
- HITL, veto window, 2FA, idempotency, payout redaction и hash-only audit
  включены для любых чувствительных операций;
- SRE/chaos runbook проверен dry-run отказами PostgreSQL, RabbitMQ, external
  API и proxy без раскрытия ПДн, токенов, сумм выплат или raw content;
- открытых P0/P1 security, compliance, tenant isolation или payout defects нет
  либо у каждого есть owner, workaround, rollback и формальный risk acceptance;
- реальные credentials, ПДн, платежные реквизиты и площадочные токены не
  коммитятся в репозиторий и не прикладываются к PR/CI artifacts.

## 10. Локальная проверка

Минимальный stage-6 acceptance:

```bash
pytest tests/test_stage6_acceptance_contract.py
pytest tests/test_testing_strategy_issue83_contract.py
pytest tests/test_tenant_isolation_issue84_contract.py
pytest tests/test_load_testing_issue85_acceptance_contract.py
pytest tests/test_security_contract.py
pytest tests/test_compliance_issue87_contract.py
pytest tests/test_hitl_payout_issue88_e2e_contract.py
pytest tests/test_chaos_resilience_issue89_contract.py
```

Полный PR gate остается стандартным:

```bash
ruff check .
ruff format --check .
black --check .
mypy .
pytest
```
