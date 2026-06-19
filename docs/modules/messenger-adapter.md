# Unified Messenger Adapter

**Статус:** 🟢 реализовано · **Этап:** Этап 2 — Ключевые микросервисы · **Компонент:** `component:messenger-adapter`

Единый интерфейс публикации в мессенджеры и соцсети РФ с ретраями, шифрованием токенов и трансформацией контента под площадку.

## Зона ответственности
- Единый интерфейс публикации поверх разных площадок (`base_adapter`)
- Адаптеры Telegram, VK, Dzen, OK и др. (top-10 РФ)
- Трансформация и обрезка контента под ограничения площадки
- Реестр площадок (Platform Registry) и инъекция реферальных ссылок

## Основные интерфейсы
- **POST** `/publish` — опубликовать контент на площадку(и)
- **GET** `/platforms` — список и статусы площадок тенанта

## Модель данных (черновик)
- **platform_registry** — `tenant_id`, `platform`, `limits`, `priority`, `status`
- **platform_tokens** — `tenant_id`, `platform`, `token_encrypted` (AES-256)

## Platform Registry
- Реестр хранит tenant-scoped записи площадок: `platform`, лимиты контента,
  приоритет, статус (`active`, `paused`, `disabled`) и декларативные параметры
  интеграции.
- `UnifiedMessengerAdapter` принимает `PublicationBatchRequest`, строит
  batch-публикацию по активным записям реестра в порядке приоритета, использует
  `parameters.default_target_id` как fallback для цели публикации и возвращает
  единый результат с receipts и failures по площадкам.
- Публикация через `BasePlatformAdapter` может быть привязана к реестру: запись
  должна существовать и иметь статус `active`; иначе адаптер возвращает
  неретраемый `PlatformPublicationError` до обращения к токенам и внешним API.
- Лимиты из активной записи реестра используются как источник для трансформации
  контента перед отправкой в площадочный publisher.

## Инъекция реферальных ссылок
- `ReferralLinkInjector` принимает route payload в
  `PublicationRequest.metadata["referral_route"]`.
- Payload совместим с CGLR `link_rotator`: `admin_link`, `author_link`,
  `l3_candidates`, опциональные `content_id`, `rotation_seed` и
  `l3_min_contribution_weight`.
- Если `content_id` не передан в route payload, используется
  `metadata["content_id"]` или `publication_id`.
- В текст публикации добавляется блок `Реферальные ссылки` с L1/L2/L3 URL, а в
  metadata сохраняется компактный список уровней, владельцев и reward share.

## Зависимости
- CGLR (реферальные ссылки), Contribution Ledger
- Telethon (Telegram), VK API, политики ретраев и резервные разрешенные каналы

## Безопасность и мультитенантность
- Токены площадок шифруются (AES-256) и изолированы по `tenant_id`
- Сбои публикации повторяются по политике ретраев с экспоненциальной задержкой
- Acceptance-контракт issue #48 покрывает единый batch-вызов через registry,
  инъекцию CGLR-ссылок, трансформацию контента, публикацию минимум в Telegram и
  VK и отсутствие raw platform token в публичных моделях результата.

## Связанные задачи (issue)
- [#44](https://github.com/xlabtg/Media_Center/issues/44) — base_adapter: интерфейс, ретраи, шифрование токенов (`type:feature`)
- [#45](https://github.com/xlabtg/Media_Center/issues/45) — Адаптеры Telegram и VK (`type:feature`)
- [#46](https://github.com/xlabtg/Media_Center/issues/46) — Адаптеры Dzen, OK + трансформация и обрезка контента (`type:feature`)
- [#47](https://github.com/xlabtg/Media_Center/issues/47) — Platform Registry + инъекция реферальных ссылок + тесты (`type:feature`)
- [#48](https://github.com/xlabtg/Media_Center/issues/48) — 📤 Unified Messenger Adapter (`type:epic`)
- [#71](https://github.com/xlabtg/Media_Center/issues/71) — Telegram-клиент (шифрование, устойчивость доставки) (`type:feature`)
- [#75](https://github.com/xlabtg/Media_Center/issues/75) — Интеграция Telegram (Telethon) (`type:feature`)
- [#76](https://github.com/xlabtg/Media_Center/issues/76) — Интеграция VK API (`type:feature`)
- [#77](https://github.com/xlabtg/Media_Center/issues/77) — Интеграции Dzen, OK и др. (top-10 РФ) (`type:feature`)
- [#80](https://github.com/xlabtg/Media_Center/issues/80) — Реестр 102 площадок и приоритизация (`type:feature`)

## Связанные документы
- [COMPLIANCE.md](../COMPLIANCE.md)
- [SECURITY.md](../SECURITY.md)
- [ARCHITECTURE.md](../ARCHITECTURE.md)
- [Детальный план разработки](../DEVELOPMENT_PLAN.md)

---
<sub>Спецификация синхронизирована с реализацией Unified Messenger Adapter для issue #48.</sub>
