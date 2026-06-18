# Acceptance snapshot этапа 0

Дата фиксации: 2026-06-18.

Статус: acceptance snapshot для issue #15.

Документ закрывает эпик [#15](https://github.com/xlabtg/Media_Center/issues/15)
как итоговую фиксацию готовности этапа 0. Он не заменяет документы задач
[#3](https://github.com/xlabtg/Media_Center/issues/3)-[#14](https://github.com/xlabtg/Media_Center/issues/14),
а собирает их в один проверяемый gate перед переходом к этапу 1.

## 1. Решение по этапу 0

Этап 0 считается завершенным как инженерный фундамент для старта реализации
сервисов:

- требования, границы MVP и KPI пилота зафиксированы;
- архитектура, технологический стек, контракты и ADR-журнал согласованы как
  baseline;
- модель данных, мультитенантная стратегия и security/threat model готовы для
  проектирования этапа 1;
- репозиторий, CI/CD, локальная среда, стандарты кода и шаблоны ревью
  подготовлены;
- UX baseline, комплаенс-модель и реестр рисков заведены как обязательные
  входы для дальнейшей реализации.

Решение: можно переходить к этапу 1, сохраняя P0/P1 gates из
[docs/COMPLIANCE.md](COMPLIANCE.md) и
[docs/RISK_REGISTER.md](RISK_REGISTER.md). Пилот с реальными ПДн, публичными
публикациями, рекламой, паевыми взносами или выплатами остается запрещенным до
прохождения pre-pilot gate.

## 2. Трассировка задач #3-#14

| Issue | Результат | Основные артефакты |
|-------|-----------|--------------------|
| #3 | Требования, границы MVP и KPI пилота зафиксированы. | [docs/REQUIREMENTS.md](REQUIREMENTS.md), [docs/VISION.md](VISION.md), [docs/ROADMAP.md](ROADMAP.md) |
| #4 | Правовая baseline-модель и pre-pilot checklist зафиксированы. | [docs/COMPLIANCE.md](COMPLIANCE.md), [docs/RISK_REGISTER.md](RISK_REGISTER.md) |
| #5 | C4, границы сервисов и контракты взаимодействия зафиксированы. | [docs/ARCHITECTURE.md](ARCHITECTURE.md), [docs/adr/README.md](adr/README.md), [docs/contracts/README.md](contracts/README.md) |
| #6 | Технологический стек и версии закреплены ADR. | [docs/adr/0006-technology-stack-and-versions.md](adr/0006-technology-stack-and-versions.md), [README.md](../README.md) |
| #7 | ER-модель, индексы, tenant-aware хранение и миграционный план описаны. | [docs/DATA_MODEL.md](DATA_MODEL.md), [docs/adr/0007-data-model-and-tenant-storage.md](adr/0007-data-model-and-tenant-storage.md), [docs/SECURITY.md](SECURITY.md) |
| #8 | Монорепо, лицензия, сервисные каталоги и правила участия подготовлены. | [docs/REPOSITORY_STRUCTURE.md](REPOSITORY_STRUCTURE.md), [CONTRIBUTING.md](../CONTRIBUTING.md), [LICENSE](../LICENSE) |
| #9 | CI/CD quality/security/image baseline включен. | [.github/workflows/ci.yml](../.github/workflows/ci.yml), [requirements-dev.txt](../requirements-dev.txt), [infra/docker/service.Dockerfile](../infra/docker/service.Dockerfile) |
| #10 | Локальная docker-compose среда описана и покрыта make-таргетами. | [infra/local/docker-compose.yml](../infra/local/docker-compose.yml), [infra/local/README.md](../infra/local/README.md), [Makefile](../Makefile) |
| #11 | Стандарты кода, pre-commit и шаблоны issue/PR подготовлены. | [docs/CODE_STYLE.md](CODE_STYLE.md), [.pre-commit-config.yaml](../.pre-commit-config.yaml), [.github/ISSUE_TEMPLATE](../.github/ISSUE_TEMPLATE), [.github/pull_request_template.md](../.github/pull_request_template.md) |
| #12 | STRIDE-модель угроз, контрмеры и план security tests описаны. | [docs/SECURITY.md](SECURITY.md) |
| #13 | UX-сценарии, wireframes и дизайн-система v0 зафиксированы. | [docs/UX_RESEARCH.md](UX_RESEARCH.md) |
| #14 | Реестр рисков заполнен, оценен и связан с pre-pilot gate. | [docs/RISK_REGISTER.md](RISK_REGISTER.md), [docs/COMPLIANCE.md](COMPLIANCE.md) |

## 3. Критерии завершения эпика #15

| Критерий issue #15 | Статус | Проверяемые ссылки |
|--------------------|--------|--------------------|
| Утверждены ADR по архитектуре и технологическому стеку | Выполнено: ADR-журнал содержит baseline-решения по границам сервисов, интеграциям, tenant isolation, blockchain audit, HITL, стеку и данным. | [docs/adr/README.md](adr/README.md), [docs/adr/0006-technology-stack-and-versions.md](adr/0006-technology-stack-and-versions.md), [docs/ARCHITECTURE.md](ARCHITECTURE.md) |
| Работает CI/CD | Выполнено: workflow запускает lint, format check, mypy, pytest, SCA, secret scan, Trivy и сборку Docker-образов сервисов. | [.github/workflows/ci.yml](../.github/workflows/ci.yml), [tests/test_ci_contract.py](../tests/test_ci_contract.py) |
| Поднимается локальная среда | Выполнено как reproducible dev baseline: docker-compose описывает PostgreSQL, Redis, RabbitMQ, ChromaDB, MinIO; Makefile содержит `up`, `down`, `migrate`, `test`. | [infra/local/docker-compose.yml](../infra/local/docker-compose.yml), [infra/local/README.md](../infra/local/README.md), [tests/test_local_env_contract.py](../tests/test_local_env_contract.py) |
| Согласованы модель данных, threat model, реестр рисков и глоссарий | Выполнено: документы связаны между собой и покрывают tenant-aware данные, STRIDE, P0/P1 риски и терминологию. | [docs/DATA_MODEL.md](DATA_MODEL.md), [docs/SECURITY.md](SECURITY.md), [docs/RISK_REGISTER.md](RISK_REGISTER.md), [docs/GLOSSARY.md](GLOSSARY.md) |

## 4. Gate перед реализацией

Этап 1 может стартовать при следующих условиях:

- новые задачи используют документы этапа 0 как baseline, а изменения
  архитектуры, стека, модели данных или gates фиксируются через ADR/issue;
- любые новые данные, события, логи и векторные коллекции проектируются с
  обязательным `tenant_id` и отказом `403 tenant_isolation_violation` при
  cross-tenant доступе;
- сценарии, влияющие на деньги, статусы, массовые действия, публичный контент
  или политики, проектируются с Human-in-the-Loop;
- до pre-pilot gate не включаются реальные паевые взносы, фонды, выплаты,
  публичная реклама и промышленная обработка ПДн;
- для каждой новой внешней площадки требуется ToS/legal review и статус в
  platform policy registry.

## 5. Локальная проверка

Минимальный локальный acceptance для этапа 0:

```bash
python -m pip install -r requirements-dev.txt
ruff check .
ruff format --check .
black --check .
mypy .
pytest
```

Для проверки локальной среды:

```bash
bash experiments/validate_issue10_local_env.sh
```

Для проверки CI-контракта:

```bash
bash experiments/validate_issue9_ci.sh
```

## 6. Открытые ограничения

Этап 0 завершает discovery и инженерный фундамент, но не разрешает pilot launch
с реальными пользователями и деньгами. До этапа 7 должны быть отдельно закрыты
pre-pilot пункты из [docs/COMPLIANCE.md](COMPLIANCE.md), включая внешнее
юридическое заключение, оператора ПДн, согласия, DSAR workflow, incident
runbook, content gate и platform registry.
