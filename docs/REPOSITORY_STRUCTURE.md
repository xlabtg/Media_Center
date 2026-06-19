# Структура репозитория

Документ фиксирует базовую структуру монорепозитория НМЦ для issue #8. Каркас
создан до появления продуктового кода, чтобы следующие задачи добавляли
реализацию в заранее согласованные границы.

## Корневые каталоги

| Каталог | Назначение |
|---------|------------|
| `services/` | Микросервисы и gateway. Каждый сервис развивается независимо, но следует общим правилам tenant-изоляции, ошибок, аудита и наблюдаемости. |
| `libs/shared/` | Общая Python-библиотека для моделей, tenant context, error envelope, audit utilities и будущего шаблона сервиса. |
| `infra/` | Инфраструктурный каркас: локальный docker-compose, deploy-артефакты, observability и операционные настройки. |
| `.github/workflows/` | CI/CD проверки качества, security scan и сборка сервисных Docker-образов. |
| `docs/` | Требования, архитектура, ADR, контракты и спецификации модулей. |
| `experiments/` | Скрипты этапа 0, генераторы документации и проверки критериев приёмки. |

## Сервисы

| Каталог | Владелец области | Спецификация |
|---------|------------------|--------------|
| `services/api-gateway/` | Tenant-aware маршрутизация, authz, rate limit, единая внешняя точка входа | [docs/modules/api-gateway.md](modules/api-gateway.md) |
| `services/service-template/` | Эталонный FastAPI-шаблон для новых сервисов: healthcheck, `/metrics`, tenant middleware, DB settings, Alembic-структура и smoke-test | [services/service-template/README.md](../services/service-template/README.md) |
| `services/contribution-ledger/` | Учёт вклада, баллы, Кв, экспорт долей и аудит | [docs/modules/contribution-ledger.md](modules/contribution-ledger.md) |
| `services/cglr/` | Генерация контента, шаблоны, маршрутизация ссылок L1/L2/L3 | [docs/modules/cglr.md](modules/cglr.md) |
| `services/hitl-payout-gateway/` | Очередь выплат, окно вето, 2FA, коннекторы исполнения | [docs/modules/hitl-payout-gateway.md](modules/hitl-payout-gateway.md) |
| `services/activity-command-center/` | Пороги Совета, tenant-scoped очереди задач и контуры обратной связи | [docs/modules/activity-command-center.md](modules/activity-command-center.md) |
| `services/messenger-adapter/` | Единый интерфейс публикации на площадках и реестр площадок | [docs/modules/messenger-adapter.md](modules/messenger-adapter.md) |
| `services/blockchain-auditor/` | Запись и проверка SHA256-хэшей в приватной audit-chain | [docs/modules/blockchain-auditor.md](modules/blockchain-auditor.md) |
| `services/voice-to-chain/` | Локальная транскрипция голоса, hash-only фиксация transcript и TTL-удаление исходного аудио | [docs/modules/voice-to-chain.md](modules/voice-to-chain.md) |
| `services/wallet/` | Внутренний учёт МСЦ, балансов и операций участника | [docs/modules/wallet.md](modules/wallet.md) |

## Правила размещения кода

1. Код сервиса живёт только в каталоге соответствующего `services/<service>/`.
2. Общие правила, модели и helpers сначала проверяются на применимость ко всем
   сервисам и только потом попадают в `libs/shared/`.
3. Инфраструктурные файлы не должны смешиваться с кодом сервисов: compose,
   deployment и observability-артефакты размещаются в `infra/`.
4. Контракты межсервисного взаимодействия обновляются в `docs/contracts/` до
   или вместе с кодом, который меняет поведение.
5. Все новые tenant-owned данные и API обязаны ссылаться на принципы из
   [DATA_MODEL.md](DATA_MODEL.md) и [SECURITY.md](SECURITY.md).

## Текущий статус

Каркас каталогов готов. Базовый `pyproject.toml`, локальные dev-зависимости,
CI/CD workflow и общий Dockerfile для сборки сервисных образов добавлены в issue
#9. Исполняемый продуктовый код, lock-файлы и docker-compose добавляются
отдельными задачами этапов 0-1. Для новых backend-сервисов добавлен
`services/service-template/`, который фиксирует единый FastAPI scaffolding без
расширения границ существующих продуктовых сервисов.
