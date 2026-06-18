# Детальный план разработки НМЦ

Документ — единая декомпозиция работ платформы «Народный Медиацентр» (НМЦ) с прослеживаемостью **этап → эпик → задача**. Каждая строка ссылается на конкретный GitHub-issue с метками и milestone.

> 🗺 **Точка входа:** [#104](https://github.com/xlabtg/Media_Center/issues/104) — 🗺 Мастер-план разработки платформы НМЦ.
> Сводка этапов и сроки — в [ROADMAP.md](ROADMAP.md). Этот документ генерируется скриптом `experiments/gen_dev_plan.py` из дерева плана и карты issue.

## Сводка

- **Всего issue:** 102
- **Эпиков (этапы + сервисы):** 16
- **Задач:** 86
- **Меток:** type / priority / stage / area / component
- **Этапов (milestones):** 9 (0–8)

Условные обозначения приоритета: 🔴 critical · 🟠 high · 🟡 medium · 🟢 low.

---

## 🚩 Этап 0 — Discovery и фундамент проекта — [#15](https://github.com/xlabtg/Media_Center/issues/15)

> Подготовительный этап: фиксация требований, правовой анализ, проектирование архитектуры и модели данных, настройка инженерного фундамента (репозиторий, CI/CD, среда разработки, стандарты).

**Milestone:** [Этап 0 — Discovery и фундамент](https://github.com/xlabtg/Media_Center/milestone/1) · **Приоритет:** 🔴 `critical` · **Задач:** 12

| Issue | Задача | Тип | Область / Компонент |
|-------|--------|-----|---------------------|
| [#3](https://github.com/xlabtg/Media_Center/issues/3) | 🔴 Уточнение бизнес-требований и фиксация границ MVP | `type:research` | `area:product` |
| [#4](https://github.com/xlabtg/Media_Center/issues/4) | 🔴 Юридический анализ и модель соответствия (ФЗ-152, ФЗ-3085-1, ФЗ-149/436) | `type:research` | `area:compliance` |
| [#5](https://github.com/xlabtg/Media_Center/issues/5) | 🔴 Проектирование архитектуры системы (C4 + ADR) | `type:docs` | `area:backend` |
| [#6](https://github.com/xlabtg/Media_Center/issues/6) | 🟠 Выбор и фиксация технологического стека (ADR) | `type:docs` | `area:backend` |
| [#7](https://github.com/xlabtg/Media_Center/issues/7) | 🔴 Проектирование модели данных и стратегии мультитенантности | `type:docs` | `area:data` · `tenant-core` |
| [#8](https://github.com/xlabtg/Media_Center/issues/8) | 🟠 Настройка структуры репозитория, монорепо и лицензии | `type:chore` | `area:devops` |
| [#9](https://github.com/xlabtg/Media_Center/issues/9) | 🔴 Настройка CI/CD (lint, тесты, сборка, security scan) | `type:chore` | `area:devops` · `infra` |
| [#10](https://github.com/xlabtg/Media_Center/issues/10) | 🟠 Локальная среда разработки (docker-compose) | `type:chore` | `area:devops` · `infra` |
| [#11](https://github.com/xlabtg/Media_Center/issues/11) | 🟡 Стандарты кода, pre-commit и шаблоны Issue/PR | `type:chore` | `area:devops` |
| [#12](https://github.com/xlabtg/Media_Center/issues/12) | 🟠 Модель угроз (threat model) и план безопасности | `type:research` | `area:security` |
| [#13](https://github.com/xlabtg/Media_Center/issues/13) | 🟡 UX-исследование и прототипы (дизайн-система v0) | `type:research` | `area:design` |
| [#14](https://github.com/xlabtg/Media_Center/issues/14) | 🟠 Реестр рисков (ToS площадок, ПДн, финансы, контент) | `type:research` | `area:compliance` |

---

## 🧱 Этап 1 — Базовая инфраструктура и мультитенантность — [#28](https://github.com/xlabtg/Media_Center/issues/28)

> Общий технический фундамент для всех сервисов: мультитенантность, аутентификация/авторизация, API Gateway, слои хранения, наблюдаемость и общая библиотека.

**Milestone:** [Этап 1 — Базовая инфраструктура и мультитенантность](https://github.com/xlabtg/Media_Center/milestone/2) · **Приоритет:** 🔴 `critical` · **Задач:** 12

| Issue | Задача | Тип | Область / Компонент |
|-------|--------|-----|---------------------|
| [#16](https://github.com/xlabtg/Media_Center/issues/16) | 🔴 Tenant Isolation Layer: сквозная изоляция по tenant_id | `type:feature` | `area:backend` · `tenant-core` |
| [#17](https://github.com/xlabtg/Media_Center/issues/17) | 🔴 Сервис аутентификации (JWT HS256, refresh, 2FA) | `type:feature` | `area:security` · `api-gateway` |
| [#18](https://github.com/xlabtg/Media_Center/issues/18) | 🔴 RBAC и модель ролей (Совет/Президиум/Правление/пайщики) | `type:feature` | `area:security` |
| [#19](https://github.com/xlabtg/Media_Center/issues/19) | 🔴 API Gateway: tenant-aware маршрутизация и rate limiting | `type:feature` | `area:backend` · `api-gateway` |
| [#20](https://github.com/xlabtg/Media_Center/issues/20) | 🔴 Слой БД: PostgreSQL + SQLAlchemy async + Alembic | `type:feature` | `area:data` |
| [#21](https://github.com/xlabtg/Media_Center/issues/21) | 🟠 Кэш и очереди: Redis + RabbitMQ | `type:feature` | `area:backend` · `infra` |
| [#22](https://github.com/xlabtg/Media_Center/issues/22) | 🟠 Векторная БД (ChromaDB) с tenant-изоляцией | `type:feature` | `area:data` |
| [#23](https://github.com/xlabtg/Media_Center/issues/23) | 🟡 Объектное хранилище (S3/MinIO) с tenant-изоляцией | `type:feature` | `area:data` |
| [#24](https://github.com/xlabtg/Media_Center/issues/24) | 🟠 Наблюдаемость: Prometheus, Grafana, логи, трейсинг | `type:feature` | `area:devops` · `infra` |
| [#25](https://github.com/xlabtg/Media_Center/issues/25) | 🟠 Управление секретами и конфигурацией (.env, vault) | `type:chore` | `area:security` |
| [#26](https://github.com/xlabtg/Media_Center/issues/26) | 🟠 Общая библиотека (shared): модели, audit_logger, ошибки, утилиты тенанта | `type:feature` | `area:backend` |
| [#27](https://github.com/xlabtg/Media_Center/issues/27) | 🟡 Шаблон микросервиса (FastAPI scaffolding) | `type:chore` | `area:backend` |

---

## ⚙️ Этап 2 — Ключевые микросервисы — [#53](https://github.com/xlabtg/Media_Center/issues/53)

> Реализация пяти основных микросервисов платформы, образующих ядро ценностного контура: учёт вклада, генерация контента, выплаты под контролем человека, публикация и блокчейн-аудит.

**Milestone:** [Этап 2 — Ключевые микросервисы](https://github.com/xlabtg/Media_Center/milestone/3) · **Приоритет:** 🔴 `critical` · **Задач:** 19

### 📒 Contribution Ledger & Weight Engine — [#34](https://github.com/xlabtg/Media_Center/issues/34)

> Сервис учёта вкладов и расчёта весов: фиксация вклада в баллах, расчёт коэффициента влияния Кв с ограничением, экспорт распределения и аудит.

**Milestone:** [Этап 2 — Ключевые микросервисы](https://github.com/xlabtg/Media_Center/milestone/3) · **Приоритет:** 🔴 `critical` · **Задач:** 5

| Issue | Задача | Тип | Область / Компонент |
|-------|--------|-----|---------------------|
| [#29](https://github.com/xlabtg/Media_Center/issues/29) | 🔴 points_calculator: расчёт баллов вклада | `type:feature` | `area:backend` · `contribution-ledger` |
| [#30](https://github.com/xlabtg/Media_Center/issues/30) | 🔴 weight_engine: коэффициент влияния Кв с потолком 0.10 | `type:feature` | `area:backend` · `contribution-ledger` |
| [#31](https://github.com/xlabtg/Media_Center/issues/31) | 🔴 Модель данных contributions/tenant_weights + миграции | `type:feature` | `area:data` · `contribution-ledger` |
| [#32](https://github.com/xlabtg/Media_Center/issues/32) | 🟠 payout_exporter + аудит вклада (SHA256) | `type:feature` | `area:backend` · `contribution-ledger` |
| [#33](https://github.com/xlabtg/Media_Center/issues/33) | 🟠 REST API сервиса (Pydantic v2) + тесты | `type:feature` | `area:backend` · `contribution-ledger` |

### ✍️ Content Generator & Link Router (CGLR) — [#38](https://github.com/xlabtg/Media_Center/issues/38)

> Сервис генерации контента по шаблонам и маршрутизации реферальных ссылок (L1/L2/L3) с логированием вклада.

**Milestone:** [Этап 2 — Ключевые микросервисы](https://github.com/xlabtg/Media_Center/milestone/3) · **Приоритет:** 🟠 `high` · **Задач:** 3

| Issue | Задача | Тип | Область / Компонент |
|-------|--------|-----|---------------------|
| [#35](https://github.com/xlabtg/Media_Center/issues/35) | 🟠 template_engine: рендеринг и валидация (Jinja2) | `type:feature` | `area:backend` · `cglr` |
| [#36](https://github.com/xlabtg/Media_Center/issues/36) | 🔴 link_rotator: реферальные ссылки L1/L2/L3 | `type:feature` | `area:backend` · `cglr` |
| [#37](https://github.com/xlabtg/Media_Center/issues/37) | 🟠 API CGLR + contribution_logger + тесты | `type:feature` | `area:backend` · `cglr` |

### 💸 HITL Payout Gateway — [#43](https://github.com/xlabtg/Media_Center/issues/43)

> Шлюз выплат с обязательным контролем человека (Human-in-the-Loop): очередь выплат, окно вето Совета, подтверждение 2FA и коннекторы исполнения.

**Milestone:** [Этап 2 — Ключевые микросервисы](https://github.com/xlabtg/Media_Center/milestone/3) · **Приоритет:** 🔴 `critical` · **Задач:** 4

| Issue | Задача | Тип | Область / Компонент |
|-------|--------|-----|---------------------|
| [#39](https://github.com/xlabtg/Media_Center/issues/39) | 🔴 queue_manager + veto_manager (окно вето) | `type:feature` | `area:backend` · `hitl-payout` |
| [#40](https://github.com/xlabtg/Media_Center/issues/40) | 🔴 Поток подтверждения 2FA для выплат | `type:feature` | `area:security` · `hitl-payout` |
| [#41](https://github.com/xlabtg/Media_Center/issues/41) | 🟠 Коннекторы: платёжный, блокчейн-аудит, уведомления | `type:feature` | `area:backend` · `hitl-payout` |
| [#42](https://github.com/xlabtg/Media_Center/issues/42) | 🔴 REST API + E2E-тесты сценария вето | `type:feature` | `area:qa` · `hitl-payout` |

### 📤 Unified Messenger Adapter — [#48](https://github.com/xlabtg/Media_Center/issues/48)

> Унифицированный адаптер публикации в мессенджеры и соцсети РФ: единый интерфейс, ретраи, шифрование токенов, трансформация контента под площадку.

**Milestone:** [Этап 2 — Ключевые микросервисы](https://github.com/xlabtg/Media_Center/milestone/3) · **Приоритет:** 🟠 `high` · **Задач:** 4

| Issue | Задача | Тип | Область / Компонент |
|-------|--------|-----|---------------------|
| [#44](https://github.com/xlabtg/Media_Center/issues/44) | 🔴 base_adapter: интерфейс, ретраи, шифрование токенов | `type:feature` | `area:backend` · `messenger-adapter` |
| [#45](https://github.com/xlabtg/Media_Center/issues/45) | 🟠 Адаптеры Telegram и VK | `type:feature` | `area:backend` · `messenger-adapter` |
| [#46](https://github.com/xlabtg/Media_Center/issues/46) | 🟡 Адаптеры Dzen, OK + трансформация и обрезка контента | `type:feature` | `area:backend` · `messenger-adapter` |
| [#47](https://github.com/xlabtg/Media_Center/issues/47) | 🟡 Platform Registry + инъекция реферальных ссылок + тесты | `type:feature` | `area:backend` · `messenger-adapter` |

### 🔗 Private Blockchain Auditor — [#52](https://github.com/xlabtg/Media_Center/issues/52)

> Сервис неизменяемого аудита в приватной блокчейн-сети: запись только SHA256-хэшей и метаданных (без сумм и ПДн), доступ только для Совета, верификация.

**Milestone:** [Этап 2 — Ключевые микросервисы](https://github.com/xlabtg/Media_Center/milestone/3) · **Приоритет:** 🟠 `high` · **Задач:** 3

| Issue | Задача | Тип | Область / Компонент |
|-------|--------|-----|---------------------|
| [#49](https://github.com/xlabtg/Media_Center/issues/49) | 🔴 Коннектор сети (Hyperledger Besu/QBFT, gRPC) + hash_generator | `type:feature` | `area:backend` · `blockchain-auditor` |
| [#50](https://github.com/xlabtg/Media_Center/issues/50) | 🔴 access_controller (только Совет) + batch_writer | `type:feature` | `area:security` · `blockchain-auditor` |
| [#51](https://github.com/xlabtg/Media_Center/issues/51) | 🟠 API верификации записей + тесты | `type:feature` | `area:qa` · `blockchain-auditor` |

---

## 🤖 Этап 3 — Расширенные модули — [#66](https://github.com/xlabtg/Media_Center/issues/66)

> Расширение платформы AI-агентами, голосовым вводом, автоматизацией, кошельком, аналитикой, уведомлениями и управлением политиками.

**Milestone:** [Этап 3 — Расширенные модули](https://github.com/xlabtg/Media_Center/milestone/4) · **Приоритет:** 🟠 `high` · **Задач:** 11

| Issue | Задача | Тип | Область / Компонент |
|-------|--------|-----|---------------------|
| [#54](https://github.com/xlabtg/Media_Center/issues/54) | 🟠 Activity Command Center: пороги, очереди задач, контуры | `type:feature` | `area:backend` · `activity-center` |
| [#59](https://github.com/xlabtg/Media_Center/issues/59) | 🟠 Voice-to-Chain: Whisper.cpp + авто-удаление аудио (24 ч) | `type:feature` | `area:ai-ml` · `voice-to-chain` |
| [#60](https://github.com/xlabtg/Media_Center/issues/60) | 🟠 Wallet Module: учёт МСЦ и операций | `type:feature` | `area:backend` · `wallet` |
| [#61](https://github.com/xlabtg/Media_Center/issues/61) | 🟠 Analytics Engine: расчёт KPI и агрегаты | `type:feature` | `area:data` · `analytics` |
| [#62](https://github.com/xlabtg/Media_Center/issues/62) | 🟡 Notification Gateway: уведомления участников и Совета | `type:feature` | `area:backend` · `notification` |
| [#63](https://github.com/xlabtg/Media_Center/issues/63) | 🟠 Policy Manager: политики и пороги Совета | `type:feature` | `area:backend` · `activity-center` |
| [#64](https://github.com/xlabtg/Media_Center/issues/64) | 🟡 Agentic RAG / DeepResearch / Content Agent (CUA) | `type:feature` | `area:ai-ml` · `neuro-agent` |
| [#65](https://github.com/xlabtg/Media_Center/issues/65) | 🟡 XAI-аудит решений AI (объяснимость) | `type:feature` | `area:ai-ml` · `neuro-agent` |

### 🧠 Neuro-Agent Orchestrator — [#58](https://github.com/xlabtg/Media_Center/issues/58)

> Оркестратор автономных нейро-агентов под контролем порогов Совета: работа с аудиторией, вовлечение, контент-гигиена, аналитика и устойчивость доставки.

**Milestone:** [Этап 3 — Расширенные модули](https://github.com/xlabtg/Media_Center/milestone/4) · **Приоритет:** 🟡 `medium` · **Задач:** 3

| Issue | Задача | Тип | Область / Компонент |
|-------|--------|-----|---------------------|
| [#55](https://github.com/xlabtg/Media_Center/issues/55) | 🟡 Аудитория и парсинг + вовлечение и авто-ответы | `type:feature` | `area:ai-ml` · `neuro-agent` |
| [#56](https://github.com/xlabtg/Media_Center/issues/56) | 🟡 Контент и гигиена + аналитика и оптимизация | `type:feature` | `area:ai-ml` · `neuro-agent` |
| [#57](https://github.com/xlabtg/Media_Center/issues/57) | 🟠 Резервные каналы и политики ретраев | `type:feature` | `area:devops` · `neuro-agent` |

---

## 🖥 Этап 4 — Клиентские приложения и UX — [#74](https://github.com/xlabtg/Media_Center/issues/74)

> Пользовательские интерфейсы платформы: веб-кабинет пайщика, панель Совета (HITL), дашборды, онбординг, клиенты и дизайн-система.

**Milestone:** [Этап 4 — Клиентские приложения и UX](https://github.com/xlabtg/Media_Center/milestone/5) · **Приоритет:** 🟠 `high` · **Задач:** 7

| Issue | Задача | Тип | Область / Компонент |
|-------|--------|-----|---------------------|
| [#67](https://github.com/xlabtg/Media_Center/issues/67) | 🟠 Веб-кабинет пайщика (вклад, баланс, история) | `type:feature` | `area:frontend` · `web-cabinet` |
| [#68](https://github.com/xlabtg/Media_Center/issues/68) | 🔴 Панель Совета (HITL): вето, пороги, подтверждения | `type:feature` | `area:frontend` · `activity-center` |
| [#69](https://github.com/xlabtg/Media_Center/issues/69) | 🟡 Дашборды аналитики и KPI | `type:feature` | `area:frontend` · `analytics` |
| [#70](https://github.com/xlabtg/Media_Center/issues/70) | 🟠 Онбординг + AI-ассистент (12–36 ч) | `type:feature` | `area:ai-ml` · `web-cabinet` |
| [#71](https://github.com/xlabtg/Media_Center/issues/71) | 🟡 Telegram-клиент (шифрование, устойчивость доставки) | `type:feature` | `area:frontend` · `messenger-adapter` |
| [#72](https://github.com/xlabtg/Media_Center/issues/72) | 🟢 UI голосового ассистента | `type:feature` | `area:frontend` · `voice-to-chain` |
| [#73](https://github.com/xlabtg/Media_Center/issues/73) | 🟡 Дизайн-система и UI-кит | `type:feature` | `area:design` |

---

## 🔌 Этап 5 — Интеграции — [#82](https://github.com/xlabtg/Media_Center/issues/82)

> Подключение внешних систем: площадки РФ (Telegram, VK, Dzen, OK и др.), платёжные шлюзы, приватная блокчейн-сеть, реестр площадок и разрешенные резервные каналы.

**Milestone:** [Этап 5 — Интеграции](https://github.com/xlabtg/Media_Center/milestone/6) · **Приоритет:** 🟠 `high` · **Задач:** 7

| Issue | Задача | Тип | Область / Компонент |
|-------|--------|-----|---------------------|
| [#75](https://github.com/xlabtg/Media_Center/issues/75) | 🟠 Интеграция Telegram (Telethon) | `type:feature` | `area:backend` · `messenger-adapter` |
| [#76](https://github.com/xlabtg/Media_Center/issues/76) | 🟠 Интеграция VK API | `type:feature` | `area:backend` · `messenger-adapter` |
| [#77](https://github.com/xlabtg/Media_Center/issues/77) | 🟡 Интеграции Dzen, OK и др. (top-10 РФ) | `type:feature` | `area:backend` · `messenger-adapter` |
| [#78](https://github.com/xlabtg/Media_Center/issues/78) | 🟠 Платёжные шлюзы РФ | `type:feature` | `area:backend` · `wallet` |
| [#79](https://github.com/xlabtg/Media_Center/issues/79) | 🟠 Развёртывание приватной блокчейн-сети | `type:feature` | `area:devops` · `blockchain-auditor` |
| [#80](https://github.com/xlabtg/Media_Center/issues/80) | 🟡 Реестр 102 площадок и приоритизация | `type:feature` | `area:data` · `messenger-adapter` |
| [#81](https://github.com/xlabtg/Media_Center/issues/81) | 🟠 Устойчивость каналов: разрешенные fallback и ретраи | `type:feature` | `area:devops` |

---

## 🛡 Этап 6 — QA, безопасность, нагрузка — [#90](https://github.com/xlabtg/Media_Center/issues/90)

> Подтверждение качества, безопасности и производительности: стратегия тестирования, тесты изоляции, нагрузка, pentest, аудит ПДн, e2e HITL и отказоустойчивость.

**Milestone:** [Этап 6 — QA, безопасность, нагрузка](https://github.com/xlabtg/Media_Center/milestone/7) · **Приоритет:** 🔴 `critical` · **Задач:** 7

| Issue | Задача | Тип | Область / Компонент |
|-------|--------|-----|---------------------|
| [#83](https://github.com/xlabtg/Media_Center/issues/83) | 🟠 Стратегия тестирования (unit/integration/e2e) | `type:test` | `area:qa` |
| [#84](https://github.com/xlabtg/Media_Center/issues/84) | 🔴 Тесты мультитенантной изоляции (cross-tenant → 403) | `type:test` | `area:security` · `tenant-core` |
| [#85](https://github.com/xlabtg/Media_Center/issues/85) | 🟠 Нагрузочное тестирование (целевые показатели) | `type:test` | `area:qa` |
| [#86](https://github.com/xlabtg/Media_Center/issues/86) | 🔴 Pentest и аудит безопасности (OWASP) | `type:test` | `area:security` |
| [#87](https://github.com/xlabtg/Media_Center/issues/87) | 🟠 Аудит соответствия ФЗ-152 (ПДн) | `type:test` | `area:compliance` |
| [#88](https://github.com/xlabtg/Media_Center/issues/88) | 🟠 E2E-тесты HITL и выплат | `type:test` | `area:qa` · `hitl-payout` |
| [#89](https://github.com/xlabtg/Media_Center/issues/89) | 🟡 Отказоустойчивость и chaos-тесты | `type:test` | `area:qa` |

---

## 🚀 Этап 7 — Пилотный запуск — [#96](https://github.com/xlabtg/Media_Center/issues/96)

> Запуск пилота на одном тенанте (15–25 человек): подготовка, сбор KPI и телеметрии, документация участников, поддержка и ретроспектива.

**Milestone:** [Этап 7 — Пилотный запуск](https://github.com/xlabtg/Media_Center/milestone/8) · **Приоритет:** 🔴 `critical` · **Задач:** 5

| Issue | Задача | Тип | Область / Компонент |
|-------|--------|-----|---------------------|
| [#91](https://github.com/xlabtg/Media_Center/issues/91) | 🔴 Подготовка тенанта и онбординг (15–25 чел.) | `type:task` | `area:product` |
| [#92](https://github.com/xlabtg/Media_Center/issues/92) | 🟠 Сбор KPI и телеметрии пилота | `type:feature` | `area:data` · `analytics` |
| [#93](https://github.com/xlabtg/Media_Center/issues/93) | 🟠 Документация для участников и Совета | `type:docs` | `area:product` |
| [#94](https://github.com/xlabtg/Media_Center/issues/94) | 🟠 Поддержка пилота и баг-фикс | `type:task` | `area:qa` |
| [#95](https://github.com/xlabtg/Media_Center/issues/95) | 🟠 Ретроспектива и план масштабирования | `type:docs` | `area:product` |

---

## 📈 Этап 8 — Масштабирование и эксплуатация — [#103](https://github.com/xlabtg/Media_Center/issues/103)

> Переход от пилота к многотенантной эксплуатации: масштабирование, SRE-практики, резервное копирование и восстановление, маркетплейс тенантов и непрерывное улучшение.

**Milestone:** [Этап 8 — Масштабирование и эксплуатация](https://github.com/xlabtg/Media_Center/milestone/9) · **Приоритет:** 🟡 `medium` · **Задач:** 6

| Issue | Задача | Тип | Область / Компонент |
|-------|--------|-----|---------------------|
| [#97](https://github.com/xlabtg/Media_Center/issues/97) | 🟠 Мультитенантное масштабирование | `type:feature` | `area:devops` · `tenant-core` |
| [#98](https://github.com/xlabtg/Media_Center/issues/98) | 🟠 SRE: runbooks, SLA, алертинг | `type:docs` | `area:devops` · `infra` |
| [#99](https://github.com/xlabtg/Media_Center/issues/99) | 🟠 Резервное копирование и аварийное восстановление (DR) | `type:feature` | `area:devops` · `infra` |
| [#100](https://github.com/xlabtg/Media_Center/issues/100) | 🟢 Маркетплейс/каталог тенантов | `type:feature` | `area:backend` |
| [#101](https://github.com/xlabtg/Media_Center/issues/101) | 🟡 RL-KPI loop в проде (непрерывное улучшение) | `type:feature` | `area:ai-ml` |
| [#102](https://github.com/xlabtg/Media_Center/issues/102) | 🟡 Документация эксплуатации и обучение | `type:docs` | `area:product` |

---

<sub>Сгенерировано из `experiments/plan_data.py` и `experiments/issue_map.json`. Для обновления номеров issue перезапустите `experiments/gen_dev_plan.py`.</sub>
