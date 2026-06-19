# Analytics Engine

Сервис рассчитывает KPI пилота и агрегаты активности, контента,
вовлечённости и действий. Контракт реализован для #61 и ориентирован на
дашборды и контуры обратной связи.

## Интерфейсы

- `create_analytics_engine_app(config)` собирает FastAPI-приложение Analytics
  Engine.
- `POST /analytics/events` записывает нормализованное KPI-событие текущего
  tenant.
- `GET /analytics/kpi?period=<YYYY-MM|YYYY-Www>` возвращает KPI за период.
- `GET /analytics/aggregates?period=<YYYY-MM|YYYY-Www>` возвращает агрегаты по
  категориям.

## События

`InMemoryAnalyticsRepository` хранит события в памяти и фильтрует их через
tenant-aware guard из `libs.shared`. Это контрактная реализация для локальных
тестов и ранней интеграции; production-хранилище может заменить репозиторий без
изменения HTTP API.

Поддерживаемые типы:

| Тип | Категория | Назначение |
| --- | --- | --- |
| `member_active` | `participation` | Уникальные активные участники |
| `member_joined` | `participation` | Новые участники |
| `material_published` | `content` | Количество материалов |
| `content_viewed` | `content` | Просмотры |
| `reading_time_recorded` | `content` | Суммарное время чтения, секунды |
| `comment_created` | `engagement` | Комментарии |
| `task_completed` | `actions` | Завершённые задачи |
| `initiative_created` | `actions` | Инициативы |

Для `reading_time_recorded` поле `value` хранит суммарные секунды чтения, а
`sample_count` — количество сессий. KPI `avg_reading_minutes` считается как
`value / sample_count / 60`.

## Безопасность

- Все запросы проходят через JWT tenant context и `X-Tenant-Id`.
- Чтение KPI доступно ролям `council`, `presidium`, `board`.
- Запись событий доступна ролям `council`, `board`, `member_full`,
  `member_assoc`.
- Сырой `member_id` не публикуется в event payload; сервис сохраняет и отдаёт
  только `member_hash`.
- Tenant-isolation контракт #61: запрос с подменой `X-Tenant-Id` получает
  `403 tenant_isolation_violation`, а данные другого tenant не попадают в KPI и
  агрегаты.
