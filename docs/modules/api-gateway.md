# API Gateway

**Статус:** 🟢 shared gateway-core baseline · **Этап:** Этап 1 — Базовая инфраструктура и мультитенантность · **Компонент:** `component:api-gateway`

Tenant-aware точка входа: маршрутизация, проверка JWT и tenant_id, ограничение частоты запросов.

## Зона ответственности
- Единая точка входа для клиентов и сервисов
- Выдача и проверка JWT (HS256), refresh-token rotation
- Проверка 2FA/TOTP для чувствительных операций
- Извлечение `tenant_id` из access-token
- Tenant-aware маршрутизация к микросервисам
- Ограничение частоты запросов (rate limiting)

## Основные интерфейсы
- `POST /auth/login` — выдать `access_token` и `refresh_token` после проверки
  credentials будущего identity provider
- `POST /auth/refresh` — повернуть refresh-токен, отозвать старый и выдать
  новую пару токенов
- `POST /auth/logout` — отозвать refresh-токен текущей сессии
- `POST /auth/2fa/totp/setup` — подготовить TOTP secret/provisioning URI через
  защищённый secret-контур
- `POST /auth/2fa/totp/verify` — подтвердить TOTP для операции
  `payout.confirm` или другой чувствительной команды
- Проксирование `/<service>/...` с проверкой токена и тенанта
- Ответ `403 tenant_isolation_violation` при попытке кросс-тенант доступа

## Зависимости
- `libs/shared.AuthTokenService` и `TOTPService`, Redis/БД для production-store
  refresh-токенов, все микросервисы

## Безопасность и мультитенантность
- `tenant_id` берётся из JWT и пробрасывается во все запросы
- Любой доступ к чужому тенанту → `403`
- Access JWT содержит `typ=access`, `jti`, `tenant_id`, `sub`, `roles`, `iss`,
  `aud`, `iat`, `nbf`, `exp`; refresh-токены хранятся только как SHA256-хэши
- TLS 1.3+, защита от перебора через rate limiting

## Baseline реализации
- `TenantContextASGIMiddleware` проверяет JWT HS256, извлекает `tenant_id` и
  запрещает подмену `X-Tenant-Id` внешним клиентом.
- `RBACASGIMiddleware` применяется перед проксированием для endpoint-level
  authorization.
- `APIGatewayASGIMiddleware` маршрутизирует `/<service>/...` по `GatewayRoute`,
  передаёт downstream path без service prefix и выставляет trusted headers:
  `X-Tenant-Id`, `X-Subject-Id`, `X-Actor-Roles`, `X-Correlation-Id`,
  `X-Service-Name`, `X-Forwarded-Prefix`, `X-Original-Path`.
- `InMemoryRateLimiter` реализует fixed-window лимиты для unit/local wiring.
  Production Gateway должен заменить store на Redis-backed limiter, сохранив
  error envelope `429 rate_limited`.

## Связанные задачи (issue)
- [#17](https://github.com/xlabtg/Media_Center/issues/17) — Сервис аутентификации (JWT HS256, refresh, 2FA) (`type:feature`)
- [#19](https://github.com/xlabtg/Media_Center/issues/19) — API Gateway: tenant-aware маршрутизация и rate limiting (`type:feature`)

## Связанные документы
- [SECURITY.md](../SECURITY.md)
- [ARCHITECTURE.md](../ARCHITECTURE.md)
- [Детальный план разработки](../DEVELOPMENT_PLAN.md)

---
<sub>Черновик спецификации. Детализируется на этапе проектирования соответствующего модуля. Сгенерировано `experiments/gen_module_docs.py`.</sub>
