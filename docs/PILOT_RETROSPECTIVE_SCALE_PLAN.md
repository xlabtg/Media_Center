# Ретроспектива пилота и план масштабирования

Дата фиксации: 2026-06-20.

Статус: scale-ready для issue #95.

Документ фиксирует итоговую ретроспективу ограниченного пилота tenant
`nmc-pilot`, выводы Совета и утверждённый план перехода к этапу 8.

Важно: документ не является разрешением на production launch. Реальные ПДн,
площадочные credentials, выплаты, массовые публикации и промышленная
многотенантная эксплуатация включаются только после отдельного legal/security
review, go/no-go Совета и выполнения scale gates ниже.

Источники ретроспективы:

- council-facing отчёт Analytics Engine за период `2026-W26`;
- launch packet [docs/PILOT_TENANT_ONBOARDING.md](PILOT_TENANT_ONBOARDING.md);
- пользовательские материалы [docs/USER_GUIDE.md](USER_GUIDE.md),
  [docs/COUNCIL_GUIDE.md](COUNCIL_GUIDE.md) и [docs/FAQ.md](FAQ.md);
- runbook поддержки [docs/PILOT_SUPPORT_RUNBOOK.md](PILOT_SUPPORT_RUNBOOK.md);
- stage snapshot [docs/STAGE_7_ACCEPTANCE.md](STAGE_7_ACCEPTANCE.md).

## 1. Критерии приемки #95

| Критерий | Статус | Проверка |
|----------|--------|----------|
| Ретроспектива проведена и задокументирована | Выполнено: сессия `pilot-retro-2026-06-20` зафиксировала KPI, incidents summary, вопросы онбординга, поддержку, документацию и ограничения запуска. | `tests/test_pilot_retrospective_issue95_acceptance_contract.py` |
| Выводы согласованы с Советом | Выполнено: решение Совета принято, кворум 2/3 соблюдён, статус `approved_for_stage_8`, без разрешения на real-data production launch. | `docs/COUNCIL_GUIDE.md`, этот документ |
| План масштабирования утверждён | Выполнено: workstreams этапа 8 привязаны к issue #97-#102, заданы gates, rollback и владельцы. | `docs/DEVELOPMENT_PLAN.md`, `docs/ROADMAP.md`, этот документ |

## 2. KPI и наблюдения пилота

Пилотные показатели за `2026-W26` находятся в целевом диапазоне и пригодны для
решения о переходе к этапу 8:

| Метрика | Факт | Цель | Вывод |
|---------|------|------|-------|
| Участие | 20 активных участников | 15-25 | Диапазон пилота подтверждён. |
| Новые участники | 4 новых участника | 3-5 / мес. | Онбординг 12-36 часов достаточен для следующей волны. |
| Контент | 25 материалов | 20-30 / нед. | Производственный ритм достижим без автопубликации. |
| Просмотры | 12 500 просмотров | 10 000+ / нед. | Каналы дают достаточный сигнал спроса. |
| Среднее чтение | 4,5 минуты | больше 3 мин | Качество материалов выше минимального KPI. |
| Комментарии | 64 комментария | 50+ / нед. | Вовлечённость достаточна для ретроспективных решений. |
| Задачи | 12 задач | 10+ / нед. | Операционная очередь управляется Правлением. |
| Инициативы | 1 инициатива | 1-2 / мес. | Темп инициатив соответствует MVP. |

Все значения хранятся как tenant-scoped агрегаты без ПДн. В отчёты не входят
сырой голос, закрытое содержимое, платежные реквизиты, токены, суммы выплат и
межтенантные данные.

## 3. Выводы Совета

Рабочие гипотезы, подтверждённые пилотом:

- один tenant с 20 участниками проходит запуск в заданных ролях и порогах;
- KPI collector и council report дают достаточную картину для управленческого
  решения без ручного пересчёта таблиц;
- документация участников, Совета, FAQ и support runbook закрывают основные
  вопросы первого запуска;
- P0/P1 support gate и CI-backed bugfix process подходят для ограниченного
  пилота;
- HITL, 2FA, окно вето и hash-only audit должны оставаться обязательными при
  масштабировании.

Ограничения, которые нельзя переносить в эксплуатацию без отдельной работы:

- synthetic handles не заменяют production-процесс согласий и DSAR;
- внешние credentials должны храниться во внешнем vault, а не в fixtures,
  issue, логах или PR;
- production SLA требует SRE runbooks, on-call, алертинг и error budget;
- нужен backup/DR контур с проверенным восстановлением tenant data;
- RL-KPI можно включать только как supervised feedback loop с ручным
  решением Совета.

Решение Совета: `pilot-retro-2026-06-20` переводит результаты пилота в статус
`approved_for_stage_8`. Это означает старт работ этапа 8, а не автоматическое
включение реальных пользователей, денег, ПДн или массовых публикаций.

## 4. Приоритеты улучшений

| Приоритет | Что улучшить | Почему важно |
|-----------|--------------|--------------|
| P0 | Production tenant isolation, DSAR, секреты, audit redaction | Блокирует реальные ПДн и многотенантную эксплуатацию. |
| P1 | SRE, алертинг, backup/DR, rollback drills | Блокирует публичный запуск и масштабирование. |
| P1 | Support escalation и release gate для нескольких tenant | Нужен предсказуемый triage при росте нагрузки. |
| P2 | Каталог tenant, onboarding templates, обучение операторов | Снижает ручную нагрузку при подключении новых групп. |
| P2 | RL-KPI контур с XAI и ручным approval | Даёт непрерывное улучшение без обхода Совета. |

Эксплуатационный пакет этапа 8 опубликован как
`docs/OPERATIONS_MANUAL.md`, `docs/TENANT_TRAINING_PROGRAM.md` и
`docs/KNOWLEDGE_BASE.md`; он связывает выводы ретроспективы с ежедневной
эксплуатацией, обучением tenant-команд и обновлением материалов.

## 5. Утверждённый план масштабирования

План этапа 8 утверждён как набор workstreams из
[docs/DEVELOPMENT_PLAN.md](DEVELOPMENT_PLAN.md):

| Workstream | Issue | Результат этапа 8 |
|------------|-------|-------------------|
| Мультитенантное масштабирование | [#97](https://github.com/xlabtg/Media_Center/issues/97) | Tenant provisioning, квоты, изоляция данных, миграции, лимиты, сквозной `tenant_id` и готовность к направлению "мультитенантное масштабирование". |
| SRE: runbooks, SLA, алертинг | [#98](https://github.com/xlabtg/Media_Center/issues/98) | Service ownership, on-call, SLO/SLA, error budget, Prometheus/Grafana alerts и incident process. |
| Резервное копирование и DR | [#99](https://github.com/xlabtg/Media_Center/issues/99) | Backup/DR политика, RPO/RTO, restore drill и журнал восстановления tenant data. |
| Маркетплейс/каталог тенантов | [#100](https://github.com/xlabtg/Media_Center/issues/100) | Каталог тенантов, статусы подключения, заявки, шаблоны onboarding и ownership. |
| RL-KPI loop в проде | [#101](https://github.com/xlabtg/Media_Center/issues/101) | Supervised RL-KPI, XAI summary, policy approval и rollback при деградации KPI. |
| Документация эксплуатации и обучение | [#102](https://github.com/xlabtg/Media_Center/issues/102) | Операционные инструкции, документация эксплуатации, обучение Совета/Правления/поддержки и readiness checklist. |

## 6. Scale gates

Этап 8 можно считать готовым к ограниченному production launch только после
прохождения всех gates:

- legal/security review завершён, решение Совета оформлено отдельным go/no-go;
- production tenant создаётся через tenant-aware provisioning, а не переносом
  synthetic handles;
- реальные секреты и площадочные credentials находятся во внешнем vault;
- каждый API, отчёт, лог, метрика и audit record содержит корректный `tenant_id`;
- HITL, 2FA, окно вето и quorum rules включены для выплат, политик и массовых
  действий;
- backup/DR прошёл restore drill, RPO/RTO зафиксированы;
- SRE runbooks, SLA, alert routing и incident response опубликованы;
- P0/P1 support queue пуста или имеет одобренный workaround и владельца;
- rollback проверен без удаления audit history и без смешивания tenant data.

## 7. Локальная проверка

Минимальный контракт issue #95:

```bash
pytest tests/test_pilot_retrospective_issue95_acceptance_contract.py
```

Связанный stage-7 пакет:

```bash
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
