# ADR-0006: Технологический стек и версии

- **Статус:** Accepted
- **Дата:** 2026-06-18
- **Связанный issue:** [#6](https://github.com/xlabtg/Media_Center/issues/6)

## Контекст

В issue #6 нужно перевести черновой технологический стек из
[ARCHITECTURE.md](../ARCHITECTURE.md) в принятый baseline: выбрать версии
зависимостей для будущего кода, зафиксировать инфраструктурные образы и снять
открытый вопрос по приватной блокчейн-платформе.

Ограничения проекта:

- MVP должен запускаться локально через контейнеры и быть воспроизводимым в CI;
- все сервисы работают в tenant-aware режиме и используют общий Python backend
  stack;
- audit-chain хранит только SHA256-хэши и технические метаданные, без ПДн,
  денежных сумм, токенов и сырого контента;
- плавающие теги `latest` и незафиксированные major/minor версии запрещены для
  baseline;
- обновления безопасности допускаются отдельными PR, но breaking upgrades
  требуют ревизии ADR.

Версии ниже проверены 2026-06-18 по первичным реестрам PyPI, Docker Hub и
GitHub Releases. При появлении `pyproject.toml`, lock-файлов и
`docker-compose.yml` они должны использовать эту матрицу как исходный baseline.

## Решение

### Backend runtime

Принять Python 3.13.x как продуктовый runtime для микросервисов и CI.
Начальный контейнерный образ: `python:3.13.14-slim`.

Причины выбора:

- Python 3.11 уже находится в security-only фазе и даёт короткий запас до EOL
  для долгого MVP/пилота;
- Python 3.14 свежий и доступен, но для первого backend baseline безопаснее
  начать с более проверенной ветки 3.13.x;
- версия 3.13 поддерживается основными библиотеками FastAPI/Pydantic/SQLAlchemy
  и даёт достаточный срок сопровождения.

### Python-зависимости

| Область | Пакет | Версия baseline |
|---------|-------|-----------------|
| API | FastAPI | `0.137.2` |
| API server | Uvicorn | `0.49.0` |
| Валидация и настройки | Pydantic | `2.13.4` |
| Валидация и настройки | pydantic-settings | `2.14.1` |
| ORM | SQLAlchemy | `2.0.51` |
| Миграции | Alembic | `1.18.4` |
| PostgreSQL driver | asyncpg | `0.31.0` |
| Redis client | redis-py | `8.0.0` |
| RabbitMQ client | aio-pika | `9.6.2` |
| HTTP client | httpx | `0.28.1` |
| JWT | PyJWT | `2.13.0` |
| Криптография | cryptography | `49.0.0` |
| Шаблоны | Jinja2 | `3.1.6` |
| Векторная БД client | chromadb-client | `1.5.9` |
| S3 client | boto3 | `1.43.32` |
| gRPC | grpcio | `1.81.1` |
| Telegram | Telethon | `1.44.0` |
| VK | vk-api | `11.10.0` |
| Browser automation | Playwright | `1.60.0` |
| Multipart upload | python-multipart | `0.0.32` |
| Тесты | pytest | `9.1.0` |
| Async tests | pytest-asyncio | `1.4.0` |
| Integration tests | testcontainers | `4.14.2` |
| Lint/format | Ruff | `0.15.17` |
| Типизация | mypy | `2.1.0` |

Правило фиксации:

- application dependencies в первых `pyproject.toml`/lock-файлах фиксируются
  точными версиями из таблицы;
- если библиотека требует патч-обновления безопасности, PR обновляет lock-файл
  и указывает причину;
- major upgrades FastAPI, Pydantic, SQLAlchemy, ChromaDB, Playwright и Besu
  требуют проверки обратной совместимости и, при breaking changes, нового ADR.

### Инфраструктура и контейнеры

| Слой | Технология | Версия baseline / образ |
|------|------------|-------------------------|
| Реляционная БД | PostgreSQL | `postgres:17` |
| Кэш | Redis | `redis:7.4` |
| Очереди / события | RabbitMQ | `rabbitmq:4.1-management` |
| Векторная БД | ChromaDB | `chromadb/chroma:1.5.9` |
| S3-совместимое хранилище | MinIO | `minio/minio:RELEASE.2025-09-07T16-13-09Z` |
| Приватный audit-chain | Hyperledger Besu | `hyperledger/besu:26.6.1` |
| Метрики | Prometheus | `prom/prometheus:v3.5.4` |
| Дашборды | Grafana | `grafana/grafana:12.4.4` |
| Трейсинг / OTLP | OpenTelemetry Collector Contrib | `otel/opentelemetry-collector-contrib:0.154.0` |

Для локальной разработки и CI запрещено использовать `latest`; compose-файлы и
workflow должны ссылаться на явные tags из таблицы или на последующий PR,
который обновил baseline.

### Блокчейн-платформа

Выбрать **Hyperledger Besu 26.6.1 с консенсусом QBFT** как приватную
permissioned audit-chain платформу.

Архитектурные правила:

- Private Blockchain Auditor пишет в chain только SHA256-хэши и технические
  метаданные, определённые ADR-0004;
- сервис Blockchain Auditor остаётся границей интеграции: доменные сервисы не
  обращаются к Besu напрямую;
- внутренний connector сервиса может предоставлять gRPC API для остальной
  платформы, а к Besu обращаться через поддерживаемый Besu RPC-интерфейс;
- сеть стартует как приватная QBFT-сеть с валидаторами под контролем Совета или
  доверенного операционного контура;
- публичные сети, публичные токены и запись ПДн/сумм в блокчейн не входят в
  MVP.

Сравнение альтернатив:

| Вариант | Решение | Причина |
|---------|---------|---------|
| Hyperledger Besu + QBFT | Выбран | Подходит для permissioned Ethereum-compatible сети, имеет готовые контейнеры, PoA/QBFT finality и понятную операционную модель для audit-chain. |
| ConsenSys Quorum / GoQuorum | Не выбран | Даёт лишнюю Quorum-specific инфраструктуру и privacy transaction слой, который не нужен для хранения только хэшей и метаданных. |
| Приватный шард TON | Не выбран | TON-шардинг ориентирован на высокую публичную пропускную способность; для MVP-аудита важнее простая permissioned сеть, RBAC-периметр и зрелая контейнерная эксплуатация. |

### AI и голос

Для Voice-to-Chain принять **whisper.cpp v1.9.0** как локальный движок
транскрипции. Сырой звук хранится только временно и удаляется по требованиям
[REQUIREMENTS.md](../REQUIREMENTS.md) и [COMPLIANCE.md](../COMPLIANCE.md).

Agentic RAG, DeepResearch, Content Agent и RL-KPI loop остаются прикладными
паттернами поверх ChromaDB, Policy Manager и будущих AI-интеграций. Выбор
внешнего LLM-провайдера, модели и режима обработки ПДн требует отдельного ADR,
потому что влияет на compliance, стоимость и безопасность.

## Последствия

- Все будущие backend issue должны использовать Python 3.13.x и версии из этой
  матрицы, пока новый ADR или PR не обновит baseline.
- `README.md`, `ARCHITECTURE.md`, модуль Blockchain Auditor и план разработки
  больше не должны содержать открытый выбор `Besu/Quorum/TON` как baseline.
- Инфраструктурные PR обязаны использовать явные container tags, а не
  плавающий `latest`.
- Выбор Besu/QBFT упрощает MVP-аудит, но добавляет операционные задачи:
  управление валидаторами, резервное копирование chain data, мониторинг finality
  и регламент ротации ключей.
- При появлении исходного кода нужно добавить lock-файл и CI-проверку, которая
  предотвращает drift зависимостей от ADR.

## Связанные документы

- [ARCHITECTURE.md](../ARCHITECTURE.md)
- [README.md](../../README.md)
- [modules/blockchain-auditor.md](../modules/blockchain-auditor.md)
- [ADR-0004](0004-private-blockchain-audit.md)
- [SECURITY.md](../SECURITY.md)
- [COMPLIANCE.md](../COMPLIANCE.md)
