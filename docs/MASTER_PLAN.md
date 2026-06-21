# Acceptance snapshot мастер-плана

Дата фиксации: 2026-06-21.

Статус: acceptance snapshot для issue #104.

Документ закрывает эпик
[#104](https://github.com/xlabtg/Media_Center/issues/104) как верхнеуровневую
фиксацию полного цикла разработки НМЦ. Он не заменяет
[docs/DEVELOPMENT_PLAN.md](DEVELOPMENT_PLAN.md) и
[docs/ROADMAP.md](ROADMAP.md), а связывает источник плана, GitHub issue,
milestones, метки и документацию в один проверяемый gate.

## 1. Решение по мастер-плану

Мастер-план считается зафиксированным как единая трассировка
`этап -> эпик -> задача`:

- `experiments/plan_data.py` содержит декларативное дерево плана с корневым
  узлом `M`;
- `experiments/issue_map.json` связывает все 102 узла дерева с GitHub issue,
  включая `M -> 104`;
- [docs/DEVELOPMENT_PLAN.md](DEVELOPMENT_PLAN.md) публикует детальную
  декомпозицию задач, эпиков и service-level workstreams;
- [docs/ROADMAP.md](ROADMAP.md) фиксирует порядок этапов 0-8, milestones,
  сроки и критерии выхода;
- профиль участия, правила веток, меток и локального CI описаны в
  [CONTRIBUTING.md](../CONTRIBUTING.md);
- README ведет команду к мастер-плану, дорожной карте, acceptance snapshots и
  ключевой документации продукта.

Решение: команда может начинать реализацию с этапа 0, используя issue #104 как
точку входа, а `docs/DEVELOPMENT_PLAN.md` как рабочую декомпозицию задач.

## 2. Трассировка этапов #15-#103

| Этап | Метка | Родительский epic | Проверяемый контур |
|------|-------|-------------------|--------------------|
| 0 | `stage:0-discovery` | issue #15: [Этап 0 — Discovery и фундамент](https://github.com/xlabtg/Media_Center/issues/15) | [docs/STAGE_0_ACCEPTANCE.md](STAGE_0_ACCEPTANCE.md), [docs/REQUIREMENTS.md](REQUIREMENTS.md), [docs/ARCHITECTURE.md](ARCHITECTURE.md), [docs/adr/](adr/) |
| 1 | `stage:1-foundation` | issue #28: [Этап 1 — Базовая инфраструктура и мультитенантность](https://github.com/xlabtg/Media_Center/issues/28) | [docs/STAGE_1_ACCEPTANCE.md](STAGE_1_ACCEPTANCE.md), `libs/shared/*`, `services/service-template/*`, `infra/local/*` |
| 2 | `stage:2-core-services` | issue #53: [Этап 2 — Ключевые микросервисы](https://github.com/xlabtg/Media_Center/issues/53) | [docs/STAGE_2_ACCEPTANCE.md](STAGE_2_ACCEPTANCE.md), `services/contribution-ledger`, `services/cglr`, `services/hitl-payout-gateway`, `services/messenger-adapter`, `services/blockchain-auditor` |
| 3 | `stage:3-extended-modules` | issue #66: [Этап 3 — Расширенные модули](https://github.com/xlabtg/Media_Center/issues/66) | [docs/STAGE_3_ACCEPTANCE.md](STAGE_3_ACCEPTANCE.md), `services/activity-command-center`, `services/neuro-agent-orchestrator`, `services/wallet`, `services/analytics-engine`, `services/notification-gateway`, `services/policy-manager` |
| 4 | `stage:4-clients-ux` | issue #74: [Этап 4 — Клиентские приложения и UX](https://github.com/xlabtg/Media_Center/issues/74) | [docs/STAGE_4_ACCEPTANCE.md](STAGE_4_ACCEPTANCE.md), `services/web-cabinet`, Telegram client, design system, UX snapshots |
| 5 | `stage:5-integrations` | issue #82: [Этап 5 — Интеграции](https://github.com/xlabtg/Media_Center/issues/82) | [docs/STAGE_5_ACCEPTANCE.md](STAGE_5_ACCEPTANCE.md), РФ-платежи, Telegram/VK/Dzen/OK, Besu/QBFT, platform registry, fallback routes |
| 6 | `stage:6-qa-security` | issue #90: [Этап 6 — QA, безопасность, нагрузка](https://github.com/xlabtg/Media_Center/issues/90) | [docs/TESTING_STRATEGY.md](TESTING_STRATEGY.md), [docs/LOAD_TESTING.md](LOAD_TESTING.md), [docs/SECURITY.md](SECURITY.md), [docs/SECURITY_PENTEST_ISSUE_86.md](SECURITY_PENTEST_ISSUE_86.md), [docs/COMPLIANCE.md](COMPLIANCE.md) |
| 7 | `stage:7-pilot` | issue #96: [Этап 7 — Пилотный запуск](https://github.com/xlabtg/Media_Center/issues/96) | [docs/STAGE_7_ACCEPTANCE.md](STAGE_7_ACCEPTANCE.md), [docs/PILOT_TENANT_ONBOARDING.md](PILOT_TENANT_ONBOARDING.md), [docs/USER_GUIDE.md](USER_GUIDE.md), [docs/COUNCIL_GUIDE.md](COUNCIL_GUIDE.md), [docs/PILOT_SUPPORT_RUNBOOK.md](PILOT_SUPPORT_RUNBOOK.md) |
| 8 | `stage:8-scale-ops` | issue #103: [Этап 8 — Масштабирование и эксплуатация](https://github.com/xlabtg/Media_Center/issues/103) | [docs/STAGE_8_ACCEPTANCE.md](STAGE_8_ACCEPTANCE.md), [docs/MULTITENANT_SCALING.md](MULTITENANT_SCALING.md), [docs/SRE_RUNBOOK.md](SRE_RUNBOOK.md), [docs/DISASTER_RECOVERY.md](DISASTER_RECOVERY.md), [docs/OPERATIONS_MANUAL.md](OPERATIONS_MANUAL.md) |

## 3. Контроль таксономии меток и milestones

| Контур | Контроль |
|--------|----------|
| Milestones | Этапы 0-8 соответствуют GitHub milestones 1-9 и меткам `stage:*` из [docs/ROADMAP.md](ROADMAP.md). |
| Типы работ | Все задачи классифицируются через `type:*`: `epic`, `feature`, `task`, `research`, `docs`, `chore`, `test`, `bug`. |
| Приоритет | Приоритеты фиксируются через `priority:critical`, `priority:high`, `priority:medium`, `priority:low`. |
| Области | Работы распределяются по `area:*`: backend, frontend, devops, ai-ml, data, security, compliance, design, qa, product. |
| Компоненты | Сервисные задачи используют `component:*` для границ владения: `contribution-ledger`, `cglr`, `hitl-payout`, `messenger-adapter`, `blockchain-auditor`, `web-cabinet`, `tenant-core`, `infra` и смежные модули. |
| Источник документа | При изменении дерева плана обновляется `experiments/plan_data.py`, затем `experiments/issue_map.json` и генерируемый [docs/DEVELOPMENT_PLAN.md](DEVELOPMENT_PLAN.md). |

Ключевые продуктовые документы, которые остаются согласованными с задачами:

| Документ | Роль в мастер-плане |
|----------|---------------------|
| docs/VISION.md | Видение, роли человека и AI, границы кооперативной платформы. |
| docs/ARCHITECTURE.md | C4, сервисные границы, tenant-aware контуры и технологический baseline. |
| docs/ROADMAP.md | Этапы, milestones, pilot timeline и criteria of exit. |
| docs/ECONOMICS.md | Баллы, веса, МСЦ, паи, фонды и принципы выплат. |
| docs/GOVERNANCE.md | Совет, Правление, Президиум, HITL и правила вето. |
| docs/COMPLIANCE.md | ФЗ-152, ФЗ-3085-1, ФЗ-149/436, ToS и privacy gates. |
| docs/SECURITY.md | Threat model, tenant isolation, security tests и audit-chain invariants. |
| docs/GLOSSARY.md | Единый словарь терминов НМЦ. |
| docs/DEVELOPMENT_PLAN.md | Полная issue-level декомпозиция мастер-плана. |

## 4. Критерии завершения issue #104

| Критерий issue #104 | Статус | Проверяемые ссылки |
|---------------------|--------|--------------------|
| Все этапы (0-8) заведены как milestones и наполнены эпиками/задачами | Выполнено: корневой узел `M` содержит 9 stage epics, а `issue_map.json` связывает 102 узла с issue #3-#104. | [experiments/plan_data.py](../experiments/plan_data.py), [experiments/issue_map.json](../experiments/issue_map.json), [docs/DEVELOPMENT_PLAN.md](DEVELOPMENT_PLAN.md) |
| Каждая задача имеет метки type/priority/stage/area/component | Выполнено: таксономия описана в CONTRIBUTING, ROADMAP и DEVELOPMENT_PLAN; service/component labels отражены в таблицах задач. | [CONTRIBUTING.md](../CONTRIBUTING.md), [docs/ROADMAP.md](ROADMAP.md), [docs/DEVELOPMENT_PLAN.md](DEVELOPMENT_PLAN.md) |
| Документация (docs/*) согласована с задачами | Выполнено: документы продукта, acceptance snapshots и module specs связаны с issue-level планом и проверяются контрактными тестами. | [README.md](../README.md), [docs/DEVELOPMENT_PLAN.md](DEVELOPMENT_PLAN.md), [tests/test_master_plan_issue104_contract.py](../tests/test_master_plan_issue104_contract.py) |
| Команда может начать реализацию с этапа 0 | Выполнено: этап 0 имеет acceptance snapshot, contributing rules, локальный CI, архитектурные ADR и gate перед этапом 1. | [docs/STAGE_0_ACCEPTANCE.md](STAGE_0_ACCEPTANCE.md), [CONTRIBUTING.md](../CONTRIBUTING.md), [.github/workflows/ci.yml](../.github/workflows/ci.yml) |

## 5. Локальная проверка

Минимальный контракт мастер-плана:

```bash
pytest tests/test_master_plan_issue104_contract.py
```

Проверка генерируемой декомпозиции после изменения дерева плана:

```bash
python experiments/gen_dev_plan.py
pytest tests/test_master_plan_issue104_contract.py
```

Перед финальным ревью PR должен также проходить стандартный локальный gate:

```bash
ruff check .
ruff format --check .
black --check .
mypy .
pytest
```

## 6. Открытые правила сопровождения

- Issue #104 не закрывает дочерние issue автоматически: каждый stage epic и
  task сохраняет собственный acceptance/gate.
- Новые workstreams добавляются сначала в `experiments/plan_data.py`, затем
  синхронизируются с GitHub issue и генерируемой документацией.
- Acceptance snapshots не должны содержать реальные ПДн, секреты, platform
  tokens, суммы выплат, raw content или приватные endpoints.
