# Backup/DR infrastructure

`infra/backup` фиксирует проверяемый контракт issue #99 для резервного
копирования PostgreSQL, ChromaDB и S3/MinIO.

Источник истины:

- `backup-policy.json` - расписания, RTO/RPO, retention, restore validation и
  evidence последнего drill;
- `scripts/backup.sh` - локальный dry-run и команды снятия backup artifacts;
- `scripts/restore_drill.sh` - безопасный restore drill checklist для
  изолированного sandbox;
- `cron.d/nmc-backups.cron` - production-style расписание cron.

Локальная проверка без изменения volumes:

```bash
make backup-policy
make backup-local
make restore-drill
pytest tests/test_backup_dr_issue99_acceptance_contract.py
```

Полная процедура описана в
[docs/DISASTER_RECOVERY.md](../../docs/DISASTER_RECOVERY.md). Evidence и логи
следуют политике `no_pdn_no_secrets`: без ПДн, токенов, платежных сумм и
закрытого содержимого.
