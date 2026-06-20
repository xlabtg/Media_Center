# Analytics Engine

Сервис рассчитывает KPI пилота и агрегаты активности, контента,
вовлечённости и действий. Контракт реализован для #61 и расширен сбором KPI,
телеметрией пилота #92 и RL-KPI loop #101 для supervised continuous
improvement под контролем Совета.

## Интерфейсы

- `create_analytics_engine_app(config)` собирает FastAPI-приложение Analytics
  Engine.
- `POST /analytics/events` записывает нормализованное KPI-событие текущего
  tenant.
- `GET /analytics/kpi?period=<YYYY-MM|YYYY-Www>` возвращает KPI за период.
- `GET /analytics/aggregates?period=<YYYY-MM|YYYY-Www>` возвращает агрегаты по
  категориям.
- `POST /analytics/pilot/telemetry/collect` принимает batch пилотной
  телеметрии от tenant-scoped collector и автоматически записывает KPI-события,
  usage signals и incidents.
- `GET /analytics/pilot/reports?period=<YYYY-MM|YYYY-Www>` возвращает
  регулярный отчёт Совету: KPI, агрегаты, usage telemetry, incidents и
  feedback-loop статус.
- `POST /analytics/rl-kpi/iterations` создаёт RL-KPI итерацию за окно
  7-30 дней и формирует XAI-предложения оптимизаций.
- `GET /analytics/rl-kpi/iterations/{iteration_id}` возвращает сохранённую
  итерацию текущего tenant.
- `POST /analytics/rl-kpi/iterations/{iteration_id}/approval` фиксирует
  ручное approval Совета до изменения политик или запусков.
- `POST /analytics/rl-kpi/iterations/{iteration_id}/effect` измеряет эффект
  утверждённых изменений по следующему KPI-периоду и помечает rollback при
  деградации.
- `build_analytics_kpi_response` и `build_analytics_aggregates_response`
  собирают те же ответы из набора событий для клиентских projections, включая
  Web Cabinet дашборд #69.

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

## Сбор KPI и телеметрии пилота #92

`POST /analytics/pilot/telemetry/collect` агрегирует недельный или месячный
batch наблюдаемости пилота. Поле `kpi` превращается в обычные
`analytics.event_recorded` события, поэтому `GET /analytics/kpi`, агрегаты и
Web Cabinet dashboard видят те же значения без дублирования формул. Поля
`usage` и `incidents` сохраняются отдельно как telemetry projection для отчётов
Совету.

Каждый batch пишет hash-only audit trail:

- `analytics.event_recorded` для KPI-событий;
- `analytics.pilot_usage_recorded` для usage signals;
- `analytics.pilot_incident_recorded` для инцидентов;
- `analytics.pilot_batch_collected` для завершённого batch.

`GET /analytics/pilot/reports` доступен роли `council` и возвращает регулярный
отчёт с weekly/monthly frequency, recipients `council`, KPI summary,
категорийными агрегатами, usage summary, incidents summary и статусом
`feedback_loop`.

## RL-KPI loop #101

`POST /analytics/rl-kpi/iterations` строит supervised RL-KPI итерацию поверх
существующих KPI-событий Analytics Engine. Входное окно ограничено 7-30 дней:
это соответствует адаптивному контуру из `GOVERNANCE.md` и не позволяет
запускать слишком короткие или неконтролируемо длинные циклы обучения.

Итерация анализирует последние KPI выбранных периодов, создаёт предложения для
метрик вне target, добавляет XAI summary с фактическими значениями и оставляет
статус `awaiting_council_approval`. Предложения не исполняются автоматически:
`POST /analytics/rl-kpi/iterations/{iteration_id}/approval` доступен только
роли `council` и фиксирует ссылку на решение Совета.

После следующего периода `POST /analytics/rl-kpi/iterations/{iteration_id}/effect`
сравнивает baseline с evaluation KPI, возвращает `improved`, `stable` или
`degraded` и выставляет `rollback_required`, если эффект ухудшил целевую
метрику. Контур пишет hash-only события:

- `analytics.rl_kpi_iteration_created`;
- `analytics.rl_kpi_council_decision_recorded`;
- `analytics.rl_kpi_effect_measured`.

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
- Tenant-isolation контракт #92: pilot collector и council report фильтруют
  usage/incidents/KPI по `tenant_id`; данные другого tenant не попадают в отчёт
  Совету.
- Tenant-isolation контракт #101: RL-KPI итерации, approval и измерение эффекта
  хранятся tenant-scoped, а подмена `X-Tenant-Id` возвращает
  `403 tenant_isolation_violation`.
