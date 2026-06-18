# DB Migrations

Alembic-конфигурация для базового реляционного слоя этапа 1.

```bash
DATABASE_URL=postgresql+asyncpg://nmc:nmc_dev_password@localhost:5432/nmc \
  alembic -c infra/db/alembic.ini upgrade head

DATABASE_URL=postgresql+asyncpg://nmc:nmc_dev_password@localhost:5432/nmc \
  alembic -c infra/db/alembic.ini downgrade base
```

Первая ревизия `0001_tenant_foundation` создаёт reversible baseline таблиц
`tenants` и `tenant_settings`. Сервисные ревизии должны добавляться отдельными
цепочками или схемами, сохраняя naming conventions и обязательный `tenant_id`
для tenant-owned таблиц из `docs/DATA_MODEL.md`.
