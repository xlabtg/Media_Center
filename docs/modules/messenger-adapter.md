# Unified Messenger Adapter

**Статус:** 🟡 планируется · **Этап:** Этап 2 — Ключевые микросервисы · **Компонент:** `component:messenger-adapter`

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

## Зависимости
- CGLR (реферальные ссылки), Contribution Ledger
- Telethon (Telegram), VK API, прокси-ротация

## Безопасность и мультитенантность
- Токены площадок шифруются (AES-256) и изолированы по `tenant_id`
- Сбои публикации повторяются по политике ретраев с экспоненциальной задержкой

## Связанные задачи (issue)
- [#44](https://github.com/xlabtg/Media_Center/issues/44) — base_adapter: интерфейс, ретраи, шифрование токенов (`type:feature`)
- [#45](https://github.com/xlabtg/Media_Center/issues/45) — Адаптеры Telegram и VK (`type:feature`)
- [#46](https://github.com/xlabtg/Media_Center/issues/46) — Адаптеры Dzen, OK + трансформация и обрезка контента (`type:feature`)
- [#47](https://github.com/xlabtg/Media_Center/issues/47) — Platform Registry + инъекция реферальных ссылок + тесты (`type:feature`)
- [#48](https://github.com/xlabtg/Media_Center/issues/48) — 📤 Unified Messenger Adapter (`type:epic`)
- [#71](https://github.com/xlabtg/Media_Center/issues/71) — Telegram-клиент (шифрование, прокси) (`type:feature`)
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
<sub>Черновик спецификации. Детализируется на этапе проектирования соответствующего модуля. Сгенерировано `experiments/gen_module_docs.py`.</sub>
