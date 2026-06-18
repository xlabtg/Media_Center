# Content Generator & Link Router

**Статус:** базовый in-memory контур сервиса реализован для этапа 2.

## Назначение

Content Generator & Link Router (CGLR) генерирует контент по шаблонам,
маршрутизирует ссылки L1/L2/L3, сохраняет готовый результат и регистрирует
генерацию как вклад в Contribution Ledger-compatible контуре.

## Границы ответственности

- хранит и применяет шаблоны генерации контента;
- рендерит шаблоны через sandboxed Jinja2 и валидирует результат по длине и
  обязательным блокам;
- выбирает tenant-aware маршруты ссылок L1/L2/L3 через `link_rotator`;
- публикует событие `content.generated` с целевыми площадками без сырого текста
  материала;
- регистрирует вклад через Contribution Ledger, не владея расчётом баллов.

## Реализованные компоненты

- `template_engine` — sandboxed Jinja2-рендеринг с валидацией результата.
- `link_rotator` — генерация L1/L2/L3-ссылок, политика вознаграждений
  20/10/5, воспроизводимый взвешенный выбор L3-партнёра и in-memory трекинг
  переходов для unit/integration-контуров.
- `api` — tenant-aware FastAPI endpoint'ы `POST /generate` и
  `GET /content/{content_id}` с идемпотентностью, публикацией
  `content.generated` и записью вклада через Contribution Ledger-compatible
  logger.

## REST API

`POST /generate` принимает шаблон, контекст, правила валидации,
`link_routing` для L1/L2/L3 и блок `contribution`. Сервис рендерит контент,
строит ссылки, сохраняет результат, публикует событие `content.generated` без
сырого текста материала и создаёт запись вклада с `source_type=cglr_generation`.

`GET /content/{content_id}` возвращает ранее сгенерированный материал только в
рамках текущего `tenant_id`: исходный текст, `content_with_links`, L1/L2/L3,
reward distribution и связанную запись вклада.

Для командного endpoint'а обязателен `Idempotency-Key`; повтор с тем же payload
возвращает тот же `content_id`, повтор с другим payload возвращает
`409 idempotency_conflict`.

## Связанные документы

- [Спецификация модуля](../../docs/modules/cglr.md)
- [Контракты событий](../../docs/contracts/events.md)
- [Архитектура](../../docs/ARCHITECTURE.md)
