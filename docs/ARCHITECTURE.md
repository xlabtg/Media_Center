# Архитектура системы НМЦ

Документ описывает целевую архитектуру платформы: контекст, микросервисы, вспомогательные модули, технологический стек, потоки данных, мультитенантность и кросс-функциональные слои.

> Архитектура — целевая (to-be). Конкретные технологические решения фиксируются через ADR (Architecture Decision Records) на этапе 0 (см. [ROADMAP.md](ROADMAP.md)).

---

## 1. Принципы

1. **Мультитенантность по умолчанию.** `tenant_id` присутствует во всех записях БД, векторных коллекциях, логах, событиях и выплатах. Источник истины — JWT. Межтенантный доступ запрещён и возвращает `403 tenant_isolation_violation`.
2. **Human-in-the-Loop.** Чувствительные операции проходят через окна вето и пороги, заданные Советом.
3. **Микросервисы со слабой связанностью.** Сервисы общаются через API Gateway (синхронно) и RabbitMQ (асинхронно, события).
4. **Проверяемость.** Ключевые операции фиксируются хэшами в приватном блокчейне.
5. **Безопасность и приватность по дизайну.** Шифрование, минимизация данных, хранение чувствительных данных на стороне клиента.

---

## 2. Контекст системы (C4 — Level 1)

```mermaid
flowchart TB
    subgraph Users["Пользователи"]
        Council["Совет / Президиум / Правление"]
        Member["Пайщики (авторы, эксперты)"]
        Audience["Аудитория"]
    end

    subgraph NMC["Платформа НМЦ (мультитенантная)"]
        Gateway["API Gateway (tenant-aware)"]
        Core["Ядро: 5 ключевых микросервисов"]
        Ext["Расширенные модули (AI, голос, автоматизация)"]
    end

    subgraph External["Внешние системы"]
        Platforms["Площадки: Telegram, VK, Dzen, OK, …"]
        Pay["Платёжные шлюзы РФ"]
        Chain["Приватный блокчейн (Besu/Quorum/TON)"]
    end

    Council --> Gateway
    Member --> Gateway
    Audience --> Platforms
    Gateway --> Core
    Gateway --> Ext
    Core --> Chain
    Ext --> Platforms
    Core --> Pay
```

---

## 3. Контейнеры (C4 — Level 2)

```mermaid
flowchart LR
    Client["Клиентские приложения\n(веб-кабинет, панель Совета,\nTelegram-бот, голос. ассистент)"]
    GW["API Gateway\n(tenant routing, rate limit, authz)"]

    subgraph CoreServices["Ключевые микросервисы"]
        CL["Contribution Ledger\n& Weight Engine"]
        CGLR["Content Generator\n& Link Router"]
        HITL["HITL Payout Gateway"]
        UMA["Unified Messenger Adapter"]
        BCA["Private Blockchain Auditor"]
    end

    subgraph Data["Данные и инфраструктура"]
        PG[("PostgreSQL")]
        REDIS[("Redis")]
        MQ[["RabbitMQ"]]
        VDB[("ChromaDB")]
        S3[("S3 / MinIO")]
    end

    Client --> GW --> CoreServices
    CL --- PG
    CL --- REDIS
    CGLR --- VDB
    UMA --- S3
    CoreServices --- MQ
    HITL --> BCA
    CL --> BCA
```

---

## 4. Ключевые микросервисы (5)

### 4.1. Contribution Ledger & Weight Engine
Учёт вклада участников и расчёт весов.
- **Назначение:** приём событий вклада, начисление баллов, расчёт Кв, экспорт выплат, журнал аудита.
- **Ключевые компоненты:** `points_calculator.py`, `weight_engine.py`, `payout_exporter.py`, `utils/audit_logger.py`, `models/contributions.py`.
- **Формулы:** `final_points = round(base × platform_mult × reach_mult × amp_mult, 2)`; `Кв = min(баллы / avg_по_Совету; 0.10)`; `payout_share = kv_capped / Σ kv_capped`.
- **Стек:** FastAPI, SQLAlchemy async, PostgreSQL, Redis.
- Спецификация: [modules/contribution-ledger.md](modules/contribution-ledger.md).

### 4.2. Content Generator & Link Router (CGLR)
Генерация контента и маршрутизация ссылок.
- **Назначение:** генерация контента по шаблонам Jinja2, ротация ссылок L1/L2/L3, валидация под площадку, логирование вклада.
- **Ключевые компоненты:** `template_engine.py`, `link_rotator.py`, `platform_validator.py`, `contribution_logger.py`.
- **Стек:** FastAPI, Jinja2, ChromaDB (контекст/память), Redis.
- Спецификация: [modules/cglr.md](modules/cglr.md).

### 4.3. HITL Payout Gateway
Шлюз выплат с контролем человека.
- **Назначение:** очередь выплат, окно вето (4–12 ч, по умолчанию 8), 2FA-подтверждение, интеграция с платёжными шлюзами РФ, запись хэша в блокчейн.
- **Ключевые компоненты:** `queue_manager.py`, `veto_manager.py` (критический), `notification_adapter.py`, `blockchain_writer.py`, `wallet_connector.py`.
- **Стек:** FastAPI, RabbitMQ, Redis, PostgreSQL.
- Спецификация: [modules/hitl-payout-gateway.md](modules/hitl-payout-gateway.md).

### 4.4. Unified Messenger Adapter
Единый адаптер к площадкам.
- **Назначение:** публикация на десятках площадок через единый интерфейс, трансформация контента под площадку, инъекция ссылок, умная обрезка, ретраи, шифрование токенов на стороне клиента.
- **Ключевые компоненты:** `base_adapter.py`, `telegram_adapter.py`, `vk_adapter.py`, `dzen_adapter.py`, `ok_adapter.py`, `content_transformer.py`, `link_injector.py`, `smart_truncate.py`.
- **Стек:** FastAPI, Telethon/VK API, Playwright, RabbitMQ.
- Спецификация: [modules/messenger-adapter.md](modules/messenger-adapter.md).

### 4.5. Private Blockchain Auditor
Аудит операций в приватном блокчейне.
- **Назначение:** запись SHA256-хэшей операций и метаданных, проверка целостности, доступ только для Совета, пакетная запись.
- **Ключевые компоненты:** `blockchain_connector.py`, `hash_generator.py`, `access_controller.py`, `batch_writer.py`.
- **Стек:** FastAPI, gRPC, Hyperledger Besu/Quorum или приватный шард TON.
- **Важно:** в блокчейн записываются только хэши и метаданные — **без сумм и персональных данных**.
- Спецификация: [modules/blockchain-auditor.md](modules/blockchain-auditor.md).

---

## 5. Вспомогательные модули

| Модуль | Назначение | Спецификация |
|--------|------------|--------------|
| **Activity Command Center** | Backend панели Совета/администратора: мониторинг активности, управление порогами и вето. | [modules/activity-command-center.md](modules/activity-command-center.md) |
| **Neuro-Agent Orchestrator** | Оркестрация ИИ-агентов автоматизации: 4 подмодуля — Аудитория&Парсинг, Вовлечение&Авто-ответы, Контент&Гигиена, Аналитика&Оптимизация. | [modules/neuro-agent-orchestrator.md](modules/neuro-agent-orchestrator.md) |
| **Voice-to-Chain** | Голос → Whisper.cpp (локально) → хэш транскрипта в блокчейн; авто-удаление сырого звука за 24 ч. | [modules/voice-to-chain.md](modules/voice-to-chain.md) |
| **Wallet Module** | Учёт МСЦ и балансов пайщиков. | [modules/wallet.md](modules/wallet.md) |
| **Analytics Engine** | Расчёт KPI, коэффициента вовлечённости, аналитика контента. | [modules/analytics-engine.md](modules/analytics-engine.md) |
| **Notification Gateway** | Единая отправка уведомлений (вето, выплаты, события). | [modules/notification-gateway.md](modules/notification-gateway.md) |
| **Policy Manager** | Управление порогами, этическими правилами, конфигурацией RL-KPI. | [modules/policy-manager.md](modules/policy-manager.md) |
| **API Gateway** | Tenant-aware маршрутизация, лимиты, авторизация. | [modules/api-gateway.md](modules/api-gateway.md) |
| **Tenant Isolation Layer** | Сквозная изоляция тенантов на всех слоях. | [modules/tenant-isolation.md](modules/tenant-isolation.md) |

---

## 6. Технологический стек

| Слой | Технологии |
|------|------------|
| **Язык / фреймворк** | Python 3.11+, FastAPI, Pydantic v2 |
| **ORM / миграции** | SQLAlchemy (async, asyncpg), Alembic |
| **Реляционная БД** | PostgreSQL |
| **Кэш** | Redis |
| **Очереди / события** | RabbitMQ |
| **Векторная БД** | ChromaDB |
| **Объектное хранилище** | S3-совместимое (MinIO) |
| **Блокчейн** | Приватный: Hyperledger Besu / Quorum или приватный шард TON; gRPC |
| **AI / голос** | Whisper.cpp, Agentic RAG, DeepResearch, Content Agent (CUA), RL-KPI loop, XAI |
| **Автоматизация** | Telethon, VK API, Playwright, ротация прокси (HTTP/SOCKS5/MTProto) |
| **Шаблоны** | Jinja2 |
| **Безопасность** | JWT (HS256), AES-256, TLS 1.3+, SHA256, 2FA, RBAC |
| **Контейнеризация** | Docker, docker-compose |
| **Наблюдаемость** | Prometheus, Grafana, структурные логи, трейсинг |
| **Тестирование** | pytest |

---

## 7. Потоки данных

### 7.1. Учёт вклада → выплата

```mermaid
sequenceDiagram
    participant M as Пайщик
    participant CL as Contribution Ledger
    participant BCA as Blockchain Auditor
    participant HITL as HITL Payout Gateway
    participant C as Совет
    participant Pay as Платёжный шлюз

    M->>CL: Событие вклада (event_type, metadata)
    CL->>CL: final_points = base × mult × reach × amp
    CL->>BCA: audit_hash события
    CL->>CL: пересчёт Кв (cap 0.10)
    CL->>HITL: запрос на выплату (payout_share)
    HITL->>C: открыто окно вето (4–12 ч)
    C-->>HITL: подтверждение (2FA) / вето
    HITL->>Pay: выплата (если нет вето)
    HITL->>BCA: хэш операции выплаты
```

### 7.2. Генерация и публикация контента

```mermaid
sequenceDiagram
    participant C as Совет
    participant CGLR as CGLR
    participant UMA as Unified Messenger Adapter
    participant P as Площадки

    C->>CGLR: тема / шаблон утверждены
    CGLR->>CGLR: генерация (Jinja2) + ротация L1/L2/L3
    CGLR->>UMA: готовый контент + ссылки
    UMA->>UMA: трансформация под площадку, обрезка, инъекция ссылок
    UMA->>P: публикация (ретраи, > 99 % успеха)
    UMA-->>CGLR: статусы публикаций → лог вклада
```

---

## 8. Мультитенантность

- **Идентификация:** `tenant_id` извлекается из JWT при каждом запросе через API Gateway.
- **Данные:** все таблицы содержат `tenant_id` (индексируется); запросы фильтруются по тенанту на уровне репозиториев.
- **Векторы:** коллекции ChromaDB разделяются по тенанту (имя коллекции / метаданные).
- **Хранилище:** объекты S3 разделяются по префиксу `tenant_id/`.
- **Логи и метрики:** содержат `tenant_id` как обязательный label.
- **Контроль:** middleware проверяет соответствие `tenant_id` запроса и ресурса; нарушение → `403 tenant_isolation_violation`.

Подробнее — [modules/tenant-isolation.md](modules/tenant-isolation.md) и [SECURITY.md](SECURITY.md).

---

## 9. Кросс-функциональные слои

- **Аутентификация/авторизация:** JWT + RBAC (роли: Совет, Президиум, Правление, действительный/ассоциативный пайщик).
- **Аудит:** единый `audit_logger`, хэширование событий, запись в блокчейн.
- **Наблюдаемость:** метрики, логи, трейсинг с `tenant_id`.
- **Конфигурация:** `.env` + менеджер секретов; пороги — через Policy Manager.
- **Общая библиотека (shared):** Pydantic-модели, ошибки, аудит-логгер, утилиты тенантов, базовый scaffolding микросервиса.

---

## 10. Модель данных (фрагмент)

**`contributions`**
| Поле | Тип | Примечание |
|------|-----|-----------|
| `id` | UUID | PK |
| `tenant_id` | String(36) | индексируется |
| `event_type` | String | тип события вклада |
| `points_awarded` | Float | начисленные баллы |
| `metadata` | JSON | контекст события |
| `created_at` | DateTime | |
| `audit_hash` | String(64) | SHA256 |

Индексы: `idx_tenant_event (tenant_id, event_type)`, `idx_tenant_date (tenant_id, created_at)`.

**`tenant_weights`**
| Поле | Тип | Примечание |
|------|-----|-----------|
| `tenant_id` | String(36) | PK |
| `period` | String(7) | `YYYY-MM` |
| `total_points` | Float | |
| `avg_points_council` | Float | среднее по Совету |
| `kv_raw` | Float | до ограничения |
| `kv_capped` | Float | после ограничения 0.10 |
| `updated_at` | DateTime | |

Полная модель данных проектируется на этапе 0 (см. issue по проектированию модели данных).

---

## 11. Развёртывание

- **Локально:** docker-compose (PostgreSQL, Redis, RabbitMQ, ChromaDB, MinIO, сервисы).
- **CI/CD:** lint → тесты → сборка образов → security scan → деплой.
- **Прод:** контейнеры с горизонтальным масштабированием; приватная блокчейн-сеть отдельным контуром; доступ к блокчейну — только Совет.

Конфигурация окружения — см. `.env.example` и [SECURITY.md](SECURITY.md).
