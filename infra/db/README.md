# DB Migrations

Alembic-конфигурация для базового реляционного слоя этапа 1.

```bash
DATABASE_URL=postgresql+asyncpg://nmc:nmc_dev_password@localhost:5432/nmc \
  alembic -c infra/db/alembic.ini upgrade head

DATABASE_URL=postgresql+asyncpg://nmc:nmc_dev_password@localhost:5432/nmc \
  alembic -c infra/db/alembic.ini downgrade base
```

Ревизия `0001_tenant_foundation` создаёт reversible baseline таблиц `tenants`
и `tenant_settings`.

Ревизия `0002_contribution_ledger` добавляет tenant-owned таблицы
`contributions` и `tenant_weights` для Contribution Ledger & Weight Engine:
обязательный `tenant_id`, FK на `tenants`, tenant-aware unique constraints,
индексы по tenant/time и check-ограничения для баллов и потолка `kv_capped`.

Сервисные ревизии должны добавляться отдельными цепочками или схемами,
сохраняя naming conventions и обязательный `tenant_id` для tenant-owned таблиц
из `docs/DATA_MODEL.md`.
