# Service migrations

Каталог для Alembic-ревизий сервиса.

Правила:

- новые tenant-owned таблицы содержат `tenant_id`;
- constraints и индексы используют naming conventions из `libs.shared.db`;
- ревизии кладутся в `migrations/versions/`;
- `DATABASE_URL` берётся из окружения, fallback в `alembic.ini` нужен только для
  локального шаблонного запуска.

Пример команды:

```bash
DATABASE_URL=postgresql+asyncpg://nmc:nmc_dev_password@localhost:5432/nmc \
  alembic -c services/service-template/alembic.ini upgrade head
```
