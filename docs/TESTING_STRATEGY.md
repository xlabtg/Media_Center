# Стратегия тестирования НМЦ

Статус: baseline для issue #83, этап 6 — QA, безопасность, нагрузка.

Документ задаёт единую пирамиду тестирования, минимальные пороги покрытия,
правила тестовых данных и критерии включения проверок в CI. Стратегия
дополняет [CONTRIBUTING.md](../CONTRIBUTING.md), модель безопасности
[SECURITY.md](SECURITY.md) и tenant-aware модель данных
[DATA_MODEL.md](DATA_MODEL.md).

## 1. Цели

- Защитить инварианты платформы: `tenant_id` во всех данных, HITL для
  чувствительных действий, hash-only audit-chain и отсутствие секретов/ПДн в
  публичных payload.
- Дать команде единый язык для unit, integration и E2E тестов.
- Измерять покрытие в CI и поднимать пороги по мере роста продуктового кода.
- Сделать тестовые данные воспроизводимыми и изолированными по tenant.

## 2. Пирамида тестирования

| Уровень | Доля | Что проверяем | Инструменты | Gate |
|---------|------|---------------|-------------|------|
| Unit | 70-80 % сценариев | Чистая бизнес-логика, валидаторы, RBAC, tenant guards, hash/canonical helpers, retry policy без сети. | `pytest`, mocks/fakes, `libs.shared.testing` | Каждый PR |
| Integration | 15-25 % сценариев | API, репозитории, middleware, события, cache/object/vector storage contracts, миграции и сервисные adapters с fake transport. | `pytest`, in-memory stores, docker compose/testcontainers по готовности | PR для затронутого сервиса |
| E2E | 5-10 % сценариев | Сквозные пользовательские и операционные потоки: вклад -> вес -> HITL payout, публикация -> аудит, onboarding, tenant leak negative path. | `pytest`, Playwright для UI, локальный compose | Release/pilot gate |

Unit тесты должны быть быстрыми, детерминированными и не зависеть от внешней
сети. Integration тесты допускают инфраструктуру только через управляемые
фикстуры. E2E тесты покрывают меньше путей, но обязаны проверять критичные
tenant/security инварианты.

## 3. Целевые пороги покрытия

Покрытие измеряется в CI через `pytest-cov`:

```bash
pytest --cov=libs --cov=services \
  --cov-report=term-missing:skip-covered \
  --cov-report=xml:coverage.xml \
  --cov-fail-under=35
```

Порог `35 %` — стартовый общий gate для текущего planning-stage репозитория,
где часть каталогов содержит каркас и контрактные спецификации. Он не является
целевым качеством продукта и должен повышаться ratchet-подходом:

| Контур | Минимум сейчас | Цель перед пилотом | Правило роста |
|--------|----------------|--------------------|---------------|
| Общий `libs` + `services` | 35 % | 70 % | Повышать на 5 п.п. после каждого этапа, если CI стабильно зелёный. |
| Новые shared/core модули | 70 % | 80 % | Новый доменный код не должен снижать покрытие своего модуля. |
| Security/tenant/HITL/audit код | 80 % | 90 % | Negative paths обязательны: `401`, `403`, veto, idempotency, redaction. |
| UI и E2E | Нет процентного gate | Критичные сценарии закрыты тестами и скриншотами | Покрытие считается через сценарии, а не line coverage. |

CI публикует `coverage.xml` как artifact, чтобы PR review мог сравнить
покрытие между запусками. Локальный HTML-отчёт можно получить командой
`pytest --cov=libs --cov=services --cov-report=html`.

## 4. Тестовые данные и фикстуры

Все тесты, затрагивающие доменные данные, обязаны явно разделять owner tenant и
foreign tenant. Для этого используется общий helper
`libs.shared.testing`:

- `TenantTestIdentity` создаёт tenant context, trusted headers и JWT для одного
  tenant без реальных секретов;
- `build_tenant_test_dataset` возвращает минимальный набор owner/foreign
  записей с разными `tenant_id`;
- `assert_only_tenant_records` падает с `cross-tenant` ошибкой, если набор
  содержит записи чужого tenant.

Правила данных:

- tenant A и tenant B не используют одинаковые `record_id`, `subject` или
  correlation id;
- фикстуры не содержат ПДн, platform tokens, реальные суммы выплат, proxy URL,
  session strings и raw content;
- negative tests всегда проверяют `tenant_isolation_violation` и отсутствие
  раскрытия данных foreign tenant;
- интеграционные тесты storage/cache/queue/vector/S3 обязаны проверять tenant
  prefix, tenant filter или routing key;
- e2e данные создаются с явным `tenant_id` и удаляются в teardown внутри того
  же tenant context.

## 5. CI и отчёты

Обязательный PR gate:

1. `ruff check .`
2. `ruff format --check .`
3. `mypy .`
4. `pytest --cov=libs --cov=services ... --cov-fail-under=35`
5. `pip-audit`, `gitleaks`, Trivy repository scan
6. Docker build matrix по сервисам

Покрытие считается частью job `Lint, types, tests`. Если тесты упали,
`coverage.xml` всё равно загружается через `if: always()`, чтобы было видно,
какой набор успел выполниться до сбоя.

## 6. Критерии для новых PR

- Новый доменный код сопровождается unit-тестами и хотя бы одним negative path.
- Изменение tenant/security/HITL/audit поведения сопровождается тестом
  межтенантного отказа или авторизационного отказа.
- Изменение API обновляет contract/acceptance тест.
- UI-изменение сопровождается Playwright проверкой или скриншотом результата,
  если поведение визуально значимо.
- Багфикс начинается с воспроизводящего теста; если воспроизвести нельзя,
  причина фиксируется в PR.

## 7. Связанные проверки

- Базовый tenant isolation слой:
  `pytest tests/test_tenant_isolation_layer.py`
- CI contract:
  `pytest tests/test_ci_contract.py`
- Контракт этой стратегии:
  `pytest tests/test_testing_strategy_issue83_contract.py`
