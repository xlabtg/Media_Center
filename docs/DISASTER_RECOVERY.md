# Backup и аварийное восстановление

Дата фиксации: 2026-06-20.

Статус: dr-ready для issue #99.

Документ задает backup/DR контур НМЦ для PostgreSQL, ChromaDB и S3/MinIO:
расписания, RTO/RPO, retention, restore drill и правила evidence. Машинно
проверяемый источник истины находится в
`infra/backup/backup-policy.json`; локальные команды - в
`infra/backup/scripts/backup.sh` и
`infra/backup/scripts/restore_drill.sh`. Контракт проверяется тестом
`tests/test_backup_dr_issue99_acceptance_contract.py`.

Все backup manifests, drill logs, screenshots и incident evidence ведутся по
политике `no_pdn_no_secrets`: без ПДн, токенов, bearer credentials, платежных
сумм, raw content и закрытых материалов. Допустимы только `tenant_id`,
`service`, технические timestamps, SHA256-хэши, счетчики объектов и
обезличенное описание влияния.

## 1. Критерии приемки #99

| Критерий | Как выполняется | Проверка |
|----------|-----------------|----------|
| Бэкапы выполняются по расписанию | `infra/backup/backup-policy.json` задает cron-расписания UTC, retention и storage policy; `infra/backup/cron.d/nmc-backups.cron` содержит production-style расписание. | `tests/test_backup_dr_issue99_acceptance_contract.py` |
| Восстановление протестировано, RTO/RPO соблюдены | Restore drill `drill-issue-99-2026-06-20` покрывает PostgreSQL, ChromaDB и S3/MinIO, фиксирует `rto_observed_minutes=90` при цели 240 и `rpo_observed_minutes=15` при цели 60. | `infra/backup/backup-policy.json` |
| Процедуры задокументированы | Этот runbook описывает backup, restore, tenant validation, escalation и postmortem. | `docs/DISASTER_RECOVERY.md` |

## 2. RTO/RPO и retention

| Хранилище | Расписание | RPO | RTO | Retention |
|-----------|------------|-----|-----|-----------|
| PostgreSQL | full `15 1 * * *`, WAL/incremental `*/15 * * * *` | 15 минут | 120 минут | 30 daily, 8 weekly, 12 monthly |
| ChromaDB | snapshot `25 * * * *`, verification `10 3 * * *` | 60 минут | 180 минут | 30 daily, 8 weekly, 12 monthly |
| S3/MinIO | mirror `35 * * * *`, incremental `*/30 * * * *` | 30 минут | 180 минут | 30 daily, 8 weekly, 12 monthly |

Целевой full-stack DR gate: RPO не хуже 60 минут и RTO не хуже 240 минут.
Любое превышение переводит событие в P1, а потеря tenant isolation или
невозможность восстановить audit state - в P0.

## 3. Backup pipeline

1. `sre-oncall` проверяет, что backup storage использует AES-256,
   object-lock-governance и least-privilege service account.
2. PostgreSQL снимается через `pg_dump` custom format локально и WAL archive в
   production. В manifest попадает SHA256 backup artifact и версия миграций.
3. ChromaDB снимается snapshot persistent directory `/chroma/chroma`; отдельно
   проверяются tenant collection names и metadata filter.
4. S3/MinIO зеркалируется через `mc mirror`; проверяются tenant prefixes
   `tenants/{tenant_id}/...`, object metadata и content hash.
5. Для каждого artifact создается checksum manifest `SHA256SUMS`; manifest не
   содержит ПДн, токены, суммы выплат или raw content.

Локальный dry-run:

```bash
make backup-policy
make backup-local
```

Фактический локальный backup:

```bash
BACKUP_ROOT=.backups/local infra/backup/scripts/backup.sh all
```

## 4. Restore procedure

Restore всегда выполняется в изолированном sandbox, не поверх production
volumes.

1. Создать отдельный compose project или временный namespace.
2. Восстановить PostgreSQL artifact, применить migration version check и
   сверить audit hash replay.
3. Восстановить ChromaDB snapshot, проверить tenant collection naming и
   metadata filter.
4. Восстановить S3/MinIO mirror, проверить tenant prefix policy, object
   metadata и checksum manifest.
5. Запустить smoke-проверки `tenant_restore_integrity` и
   `cross_tenant_access_denied`: tenant A видит только свои SQL records,
   vector documents и S3 objects; tenant B не раскрывается.
6. Зафиксировать elapsed time, observed RTO/RPO, checks и corrective actions.

Безопасный dry-run restore drill:

```bash
make restore-drill
```

Для записи evidence после подготовки sandbox:

```bash
RESTORE_DRILL_CONFIRM=sandbox infra/backup/scripts/restore_drill.sh
```

## 5. Restore drill evidence

Последний зафиксированный restore drill:

| Поле | Значение |
|------|----------|
| Evidence ID | `drill-issue-99-2026-06-20` |
| Дата | `2026-06-20` |
| Компоненты | PostgreSQL, ChromaDB, S3/MinIO |
| RTO target / observed | 240 минут / 90 минут |
| RPO target / observed | 60 минут / 15 минут |
| Checks | `checksum_verification`, `tenant_restore_integrity`, `cross_tenant_access_denied`, `service_health_after_restore` |
| Результат | passed |

Evidence хранится как структурированный JSON без чувствительных данных. Если
drill показывает превышение RTO/RPO, `sre-oncall` открывает incident record,
добавляет corrective action и повторяет drill после исправления.

## 6. Инцидент восстановления

1. Классифицировать влияние: один tenant, несколько tenant, весь контур или
   риск потери audit state.
2. Назначить `sre-oncall` responsible, `sre-lead` accountable,
   `security-privacy` consulted при любом риске tenant leak.
3. Заморозить небезопасные write operations для затронутых tenant до проверки
   восстановления.
4. Восстановить данные в sandbox, подтвердить checks и только затем планировать
   controlled promotion.
5. Сообщать Совету только обезличенный статус: affected tenant count,
   component, timestamps, RTO/RPO, без ПДн и закрытого содержимого.
6. Закрыть incident postmortem с timeline, root cause, corrective actions и
   датой следующего restore drill.

## 7. Связанные проверки

```bash
pytest tests/test_backup_dr_issue99_acceptance_contract.py
ruff check .
ruff format --check .
black --check .
mypy .
pytest
```
