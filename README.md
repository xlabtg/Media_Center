# Народный Медиацентр (НМЦ)

> **Кибернетическая мультитенантная мультиагентная платформа** для распределённого медиа-кооператива: ИИ выполняет рутину, человек (Совет) задаёт цели, этику и пороги, контролирует и накладывает вето (Human-in-the-Loop).

[![Stage](https://img.shields.io/badge/stage-planning-blue)]()
[![License](https://img.shields.io/badge/license-AGPL--3.0--only-blue)](LICENSE)
[![CI](https://github.com/xlabtg/Media_Center/actions/workflows/ci.yml/badge.svg)](https://github.com/xlabtg/Media_Center/actions/workflows/ci.yml)

---

## 📌 Что это

**Народный Медиацентр (НМЦ)** — программно-организационная платформа для добровольного объединения независимых авторов, экспертов и активистов в распределённый медиа-кооператив. Платформа автоматизирует учёт вклада участников, генерацию и распространение контента по десяткам площадок, прозрачное распределение ценности (паёв) и аудит всех операций в приватном блокчейне — при сохранении решающей роли человека (принцип **Human-in-the-Loop**).

Проект имеет двойную природу:

1. **Организационную** — Регламент кооператива (ценности, роли, органы управления, паевая система, фонды).
2. **Техническую** — мультитенантная микросервисная архитектура из 5 ключевых сервисов и набора вспомогательных модулей.

> ⚠️ **Текущий статус: планирование.** Этот репозиторий содержит **полный детальный план разработки** продукта: документацию, дорожную карту и набор связанных GitHub-issue с метками и этапами (milestones), по которым команда сможет начать полноценную разработку. Исходный код продукта будет добавляться по мере выполнения задач из плана.

---

## 🧭 Навигация по документации

| Документ | Назначение |
|----------|------------|
| [docs/VISION.md](docs/VISION.md) | Видение продукта, миссия, ценности, целевая аудитория |
| [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md) | Требования FR/NFR, границы MVP и KPI пилота |
| [docs/UX_RESEARCH.md](docs/UX_RESEARCH.md) | UX-сценарии, wireframes и дизайн-система v0 |
| [docs/GLOSSARY.md](docs/GLOSSARY.md) | Единый глоссарий терминов (НМЦ, пай, МСЦ, Кв, HITL, …) |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Архитектура системы, C4-диаграммы, микросервисы, технологический стек |
| [docs/DATA_MODEL.md](docs/DATA_MODEL.md) | ER-модель, индексы, tenant-aware стратегия хранения и план миграций |
| [docs/adr/](docs/adr/) | ADR-журнал ключевых архитектурных решений |
| [docs/contracts/](docs/contracts/) | Контракты REST/gRPC и RabbitMQ-событий между сервисами |
| [docs/REPOSITORY_STRUCTURE.md](docs/REPOSITORY_STRUCTURE.md) | Структура монорепозитория, сервисов, shared-библиотеки и infra |
| [docs/ROADMAP.md](docs/ROADMAP.md) | Дорожная карта, этапы (milestones), пилот за 6–8 недель |
| [docs/DEVELOPMENT_PLAN.md](docs/DEVELOPMENT_PLAN.md) | Мастер-план: эпики, задачи, связь с метками и этапами |
| [docs/STAGE_0_ACCEPTANCE.md](docs/STAGE_0_ACCEPTANCE.md) | Итоговая фиксация готовности этапа 0 и gate перед реализацией |
| [docs/STAGE_1_ACCEPTANCE.md](docs/STAGE_1_ACCEPTANCE.md) | Итоговая фиксация готовности базовой инфраструктуры и мультитенантности |
| [docs/STAGE_2_ACCEPTANCE.md](docs/STAGE_2_ACCEPTANCE.md) | Итоговая фиксация готовности ключевых микросервисов и сквозных сценариев |
| [docs/ECONOMICS.md](docs/ECONOMICS.md) | Экономическая модель: баллы, веса (Кв), паи, фонды, выплаты |
| [docs/GOVERNANCE.md](docs/GOVERNANCE.md) | Органы управления, статусы пайщиков, голосования, HITL |
| [docs/COMPLIANCE.md](docs/COMPLIANCE.md) | Правовое соответствие (ФЗ-152, ФЗ-3085-1, ФЗ-149/436) |
| [docs/RISK_REGISTER.md](docs/RISK_REGISTER.md) | Реестр рисков ToS, ПДн, финансов, контента, владельцы и меры митигирования |
| [docs/SECURITY.md](docs/SECURITY.md) | Модель безопасности, мультитенантная изоляция, угрозы |
| [docs/CODE_STYLE.md](docs/CODE_STYLE.md) | Гайд по стилю кода, pre-commit и локальные проверки |
| [docs/modules/](docs/modules/) | Технические спецификации модулей (по одному файлу на модуль) |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Как участвовать в разработке, стандарты кода и процесс |
| [LICENSE](LICENSE) | Лицензия проекта: GNU AGPL-3.0-only |

---

## 🏗 Архитектура в одном абзаце

Мультитенантное (`tenant_id` во всех данных, векторах, логах и выплатах) ядро на **Python 3.13.x / FastAPI 0.137.2 / PostgreSQL 17 / Redis 7.4 / RabbitMQ 4.1 / ChromaDB 1.5.9**, состоящее из 5 ключевых микросервисов:

1. **Contribution Ledger & Weight Engine** — учёт баллов, расчёт веса вклада (Кв), экспорт выплат, аудит.
2. **Content Generator & Link Router (CGLR)** — генерация контента (Jinja2) и ротация ссылок L1/L2/L3.
3. **HITL Payout Gateway** — очередь выплат, окно вето (4–12 ч), 2FA, платёжные шлюзы РФ.
4. **Unified Messenger Adapter** — единый адаптер к десяткам площадок (Telegram, VK, Dzen, OK, …).
5. **Private Blockchain Auditor** — запись и проверка SHA256-хэшей операций в приватном блокчейне.

Подробнее — в [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

---

## 🛠 Технологический стек (baseline ADR-0006)

- **Backend:** Python 3.13.x (`python:3.13.14-slim`), FastAPI 0.137.2, Pydantic 2.13.4, SQLAlchemy 2.0.51, Alembic 1.18.4, asyncpg 0.31.0
- **Хранилища:** PostgreSQL 17, Redis 7.4, RabbitMQ 4.1, ChromaDB 1.5.9, S3-совместимое объектное хранилище MinIO RELEASE.2025-09-07T16-13-09Z
- **Блокчейн:** Hyperledger Besu 26.6.1 + QBFT в приватной permissioned audit-chain (только хэши + метаданные)
- **AI / Голос:** whisper.cpp v1.9.0 (локальная транскрипция), Agentic RAG, DeepResearch, Content Agent (CUA), RL-KPI loop
- **Автоматизация:** Telethon 1.44.0 / vk-api 11.10.0, Playwright 1.60.0, ротация прокси (HTTP / SOCKS5 / MTProto)
- **Безопасность:** JWT (HS256), AES-256, TLS 1.3+, SHA256, 2FA, RBAC
- **Инфраструктура:** Docker, docker-compose, Prometheus v3.5.4, Grafana 12.4.4, OpenTelemetry Collector Contrib 0.154.0, pytest 9.1.0

Полная матрица версий и правила обновления зафиксированы в
[ADR-0006](docs/adr/0006-technology-stack-and-versions.md).

---

## ✅ CI/CD

Базовый workflow [`.github/workflows/ci.yml`](.github/workflows/ci.yml)
запускается на PR и push в `main`: `ruff`, `mypy`, `pytest`, SCA через
`pip-audit`, secret scan через `gitleaks`, Trivy scan репозитория и сборку
Docker-образов для всех сервисных каталогов. На push в `main` сервисные образы
публикуются в GHCR.

Локальная проверка базового CI и стандартов качества:

```bash
python -m pip install -r requirements-dev.txt
pre-commit install
ruff check .
ruff format --check .
black --check .
mypy .
pytest
bash experiments/validate_issue9_ci.sh
```

## 🧰 Локальная разработка

Для issue #10 добавлена воспроизводимая docker-compose среда:
PostgreSQL, Redis, RabbitMQ, ChromaDB, MinIO, Prometheus, Grafana и
OpenTelemetry Collector. Стек запускается из корня репозитория:

```bash
make up
make migrate
make test
make down
```

Команды используют `infra/local/.env.local.example`, применяют dev-миграцию и
идемпотентные сиды без ПДн, токенов и денежных сумм. Подробности по портам,
фикстурам и переопределению env-файла — в
[infra/local/README.md](infra/local/README.md).

Observability baseline для issue #24 находится в
[infra/observability/](infra/observability/): Prometheus собирает метрики
`nmc_service_operations_total` и
`nmc_service_operation_duration_seconds` с обязательным `tenant_id`, Grafana
провиженит tenant dashboard, а OpenTelemetry Collector принимает traces/logs по
OTLP без ПДн, токенов, сырого содержимого и сумм выплат.

---

## 📁 Структура монорепозитория

Репозиторий подготовлен как монорепо сервисов и общей библиотеки. Текущие
каталоги являются каркасом: продуктовый код добавляется в задачах следующих
этапов без изменения зафиксированных границ сервисов.

```text
.github/workflows/          # CI/CD: качество, security scan, сборка образов
services/
  api-gateway/              # единая tenant-aware точка входа
  contribution-ledger/      # учёт вклада и расчёт весов
  cglr/                     # генерация контента и маршрутизация ссылок
  hitl-payout-gateway/      # выплаты под контролем человека
  messenger-adapter/        # публикация на площадках
  blockchain-auditor/       # audit-chain только для SHA256-хэшей
libs/shared/                # общие модели, ошибки, tenant/audit utilities
infra/                      # локальная, deploy и observability-инфраструктура
docs/                       # требования, архитектура, ADR, контракты
experiments/                # вспомогательные скрипты и проверки этапа 0
pyproject.toml              # базовые настройки ruff, mypy, pytest
requirements-dev.txt        # закреплённые версии инструментов локального CI
```

Подробные правила владения каталогами описаны в
[docs/REPOSITORY_STRUCTURE.md](docs/REPOSITORY_STRUCTURE.md).

---

## 🗺 Дорожная карта (кратко)

| Этап | Название | Цель |
|------|----------|------|
| 0 | Discovery и фундамент | Требования, архитектура, CI/CD, среда разработки |
| 1 | Базовая инфраструктура | Мультитенантность, аутентификация, API Gateway, БД |
| 2 | Ключевые микросервисы | 5 основных сервисов платформы |
| 3 | Расширенные модули | AI-агенты, голос, автоматизация, аналитика |
| 4 | Клиентские приложения | Веб-кабинет, панель Совета, дашборды, онбординг |
| 5 | Интеграции | Площадки, платёжные шлюзы, блокчейн, anti-blocking |
| 6 | QA, безопасность, нагрузка | Тестирование изоляции, pentest, нагрузочные тесты |
| 7 | Пилотный запуск | Один tenant (15–25 человек), сбор KPI |
| 8 | Масштабирование и эксплуатация | N tenant'ов, SRE, непрерывное улучшение |

Полная дорожная карта — в [docs/ROADMAP.md](docs/ROADMAP.md). План задач — в [docs/DEVELOPMENT_PLAN.md](docs/DEVELOPMENT_PLAN.md).

---

## 🤝 Участие в разработке

См. [CONTRIBUTING.md](CONTRIBUTING.md). Работа ведётся через issue с метками `type:*`, `stage:*`, `area:*`, `component:*`, `priority:*` и привязкой к этапам (milestones). Шаблоны issue — в [`.github/ISSUE_TEMPLATE/`](.github/ISSUE_TEMPLATE/).

---

## 📄 Происхождение

Документация подготовлена на основе материалов **«Народный Медиацентр. Регламент»** и обсуждения кибернетической архитектуры продукта. План декомпозирует исходные материалы в полный цикл разработки (SDLC).

## 📜 Лицензия

Код и документация проекта распространяются на условиях
[GNU Affero General Public License v3.0 only](LICENSE). Выбор AGPL-3.0-only
фиксирует copyleft-условия для серверной сетевой платформы: производные версии,
эксплуатируемые через сеть, должны предоставлять пользователям соответствующий
исходный код.
