# API Gateway

**Статус:** 🟡 планируется · **Этап:** Этап 1 — Базовая инфраструктура и мультитенантность · **Компонент:** `component:api-gateway`

Tenant-aware точка входа: маршрутизация, проверка JWT и tenant_id, ограничение частоты запросов.

## Зона ответственности
- Единая точка входа для клиентов и сервисов
- Проверка JWT (HS256) и извлечение `tenant_id` из токена
- Tenant-aware маршрутизация к микросервисам
- Ограничение частоты запросов (rate limiting)

## Основные интерфейсы
- Проксирование `/<service>/...` с проверкой токена и тенанта
- Ответ `403 tenant_isolation_violation` при попытке кросс-тенант доступа

## Зависимости
- Сервис аутентификации (JWT/2FA), Redis (лимиты), все микросервисы

## Безопасность и мультитенантность
- `tenant_id` берётся из JWT и пробрасывается во все запросы
- Любой доступ к чужому тенанту → `403`
- TLS 1.3+, защита от перебора через rate limiting

## Связанные задачи (issue)
- [#17](https://github.com/xlabtg/Media_Center/issues/17) — Сервис аутентификации (JWT HS256, refresh, 2FA) (`type:feature`)
- [#19](https://github.com/xlabtg/Media_Center/issues/19) — API Gateway: tenant-aware маршрутизация и rate limiting (`type:feature`)

## Связанные документы
- [SECURITY.md](../SECURITY.md)
- [ARCHITECTURE.md](../ARCHITECTURE.md)
- [Детальный план разработки](../DEVELOPMENT_PLAN.md)

---
<sub>Черновик спецификации. Детализируется на этапе проектирования соответствующего модуля. Сгенерировано `experiments/gen_module_docs.py`.</sub>
