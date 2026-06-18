#!/usr/bin/env python3
"""Генератор спецификаций модулей docs/modules/*.md.

Для каждого модуля формирует единообразную профессиональную спецификацию и
автоматически подтягивает связанные GitHub-issue по метке component из
дерева плана (plan_data.PLAN) и карты issue (issue_map.json).

Запуск из корня репозитория:
  python3 experiments/gen_module_docs.py
"""
import json
import os

from plan_data import PLAN

REPO = "xlabtg/Media_Center"
ISSUE_BASE = f"https://github.com/{REPO}/issues"

with open("experiments/issue_map.json", encoding="utf-8") as f:
    IMAP = json.load(f)

STAGE_NAME = {
    1: "Этап 1 — Базовая инфраструктура и мультитенантность",
    2: "Этап 2 — Ключевые микросервисы",
    3: "Этап 3 — Расширенные модули",
}

# ----------------------------------------------------------------------------
# Сбор связанных issue по компоненту
# ----------------------------------------------------------------------------
_index = []  # список (key, title, type, comps, areas)


def _walk(n):
    _index.append((n["key"], n["title"], n["type"], n.get("comps", []), n.get("areas", [])))
    for c in n.get("children", []):
        _walk(c)


_walk(PLAN)


def issues_for_component(comp):
    rows = []
    for key, title, typ, comps, _areas in _index:
        if comp in comps:
            num = IMAP.get(key)
            if num:
                rows.append((num, title, typ))
    return sorted(rows)


# ----------------------------------------------------------------------------
# Данные модулей
# ----------------------------------------------------------------------------
MODULES = [
    {
        "slug": "contribution-ledger",
        "title": "Contribution Ledger & Weight Engine",
        "comp": "contribution-ledger", "stage": 2,
        "summary": "Учёт вклада участников в баллах, расчёт коэффициента влияния Кв "
                   "с потолком и формирование долей распределения с неизменяемым аудитом.",
        "resp": [
            "Приём и фиксация событий вклада (контент, действия, вовлечение)",
            "Расчёт баллов вклада по утверждённой формуле",
            "Расчёт коэффициента влияния Кв с ограничением сверху (анти-монополия)",
            "Формирование долей распределения для HITL Payout Gateway",
            "Генерация аудит-хэша каждого события и публикация в блокчейн-аудит",
        ],
        "formulas": [
            "`final_points = round(base × platform_mult × reach_mult × amp_mult, 2)`",
            "`Кв = min(баллы / среднее_по_Совету; 0.10)` — потолок `COUNCIL_CAP_KV = 0.10`",
            "`payout_share = kv_capped / Σ kv_capped`",
        ],
        "api": [
            "**POST** `/contributions` — зарегистрировать вклад (возвращает баллы и audit_hash)",
            "**GET** `/weights?period=` — веса Кв (raw/capped) по участникам тенанта",
            "**GET** `/payout-distribution?period=` — доли распределения для выплат",
        ],
        "data": [
            "**contributions** — `tenant_id`, `member_id`, `type`, `points`, `metadata`, `audit_hash`, `created_at`",
            "**tenant_weights** — `tenant_id`, `member_id`, `kv_raw`, `kv_capped`, `period`",
        ],
        "deps": [
            "Общая библиотека `shared` (модели, `audit_logger`, утилиты тенанта)",
            "Private Blockchain Auditor (фиксация хэшей)",
            "RabbitMQ (события вклада), PostgreSQL",
        ],
        "security": [
            "Все запросы и записи изолированы по `tenant_id`",
            "`audit_hash = SHA256(json.dumps({event_type, tenant_id, points, metadata, timestamp}, sort_keys=True))`",
            "В аудит и блокчейн не попадают денежные суммы и ПДн",
        ],
        "docs": ["ECONOMICS.md", "SECURITY.md", "ARCHITECTURE.md"],
    },
    {
        "slug": "cglr",
        "title": "Content Generator & Link Router (CGLR)",
        "comp": "cglr", "stage": 2,
        "summary": "Генерация публикуемого контента по шаблонам и маршрутизация "
                   "многоуровневых реферальных ссылок (L1/L2/L3) с учётом вклада.",
        "resp": [
            "Рендеринг контента по шаблонам Jinja2 (sandboxed) и его валидация",
            "Генерация и ротация реферальных ссылок уровней L1/L2/L3",
            "Применение реферальной политики распределения",
            "Логирование факта генерации как вклада в Contribution Ledger",
        ],
        "formulas": [
            "Реферальные уровни: **L1 = 20 %**, **L2 = 10 %**, **L3 = 5 %**",
        ],
        "api": [
            "**POST** `/generate` — сгенерировать контент по шаблону и данным",
            "**GET** `/content/{id}` — получить готовый контент с встроенными ссылками",
        ],
        "data": [
            "**templates** — `tenant_id`, `name`, `body`, `version`",
            "**generated_content** — `tenant_id`, `template_id`, `payload`, `links`, `created_at`",
        ],
        "deps": [
            "Contribution Ledger & Weight Engine (логирование вклада)",
            "Unified Messenger Adapter (инъекция ссылок при публикации)",
            "Jinja2, PostgreSQL",
        ],
        "security": [
            "Шаблоны исполняются в песочнице Jinja2 (защита от инъекций)",
            "Реферальные ссылки и шаблоны изолированы по `tenant_id`",
        ],
        "docs": ["ECONOMICS.md", "ARCHITECTURE.md"],
    },
    {
        "slug": "hitl-payout-gateway",
        "title": "HITL Payout Gateway",
        "comp": "hitl-payout", "stage": 2,
        "summary": "Шлюз выплат с обязательным контролем человека: очередь, окно вето "
                   "Совета и подтверждение через 2FA. AI исполняет — Совет контролирует.",
        "resp": [
            "Постановка рассчитанных выплат в очередь со статусами",
            "Окно вето: Совет может отменить выплату до её исполнения",
            "Обязательное подтверждение выплаты через 2FA (TOTP)",
            "Исполнение через коннекторы и фиксация результата в аудит",
        ],
        "formulas": [
            "Окно вето: `VETO_WINDOW_HOURS` (по умолчанию **8 ч**)",
        ],
        "api": [
            "**POST** `/payouts/queue` — поставить выплату в очередь",
            "**POST** `/payouts/{id}/veto` — наложить вето (роль Совета)",
            "**POST** `/payouts/{id}/confirm` — подтвердить выплату (2FA)",
            "**GET** `/payouts?status=` — список выплат тенанта по статусу",
        ],
        "data": [
            "**payouts** — `tenant_id`, `member_id`, `share`, `status`, `veto_until`, `audit_hash`, `created_at`",
        ],
        "deps": [
            "Contribution Ledger (доли распределения)",
            "Сервис аутентификации (2FA/TOTP), RBAC",
            "Private Blockchain Auditor, Notification Gateway, платёжный шлюз",
        ],
        "security": [
            "Ни одна выплата не исполняется без истечения окна вето и подтверждения 2FA",
            "Право вето и подтверждения ограничено ролью Совета (RBAC)",
            "Все решения (вето/подтверждение/исполнение) фиксируются в аудите",
        ],
        "docs": ["GOVERNANCE.md", "SECURITY.md", "ECONOMICS.md"],
    },
    {
        "slug": "messenger-adapter",
        "title": "Unified Messenger Adapter",
        "comp": "messenger-adapter", "stage": 2,
        "summary": "Единый интерфейс публикации в мессенджеры и соцсети РФ с ретраями, "
                   "шифрованием токенов и трансформацией контента под площадку.",
        "resp": [
            "Единый интерфейс публикации поверх разных площадок (`base_adapter`)",
            "Адаптеры Telegram, VK, Dzen, OK и др. (top-10 РФ)",
            "Трансформация и обрезка контента под ограничения площадки",
            "Реестр площадок (Platform Registry) и инъекция реферальных ссылок",
        ],
        "api": [
            "**POST** `/publish` — опубликовать контент на площадку(и)",
            "**GET** `/platforms` — список и статусы площадок тенанта",
        ],
        "data": [
            "**platform_registry** — `tenant_id`, `platform`, `limits`, `priority`, `status`",
            "**platform_tokens** — `tenant_id`, `platform`, `token_encrypted` (AES-256)",
        ],
        "deps": [
            "CGLR (реферальные ссылки), Contribution Ledger",
            "Telethon (Telegram), VK API, прокси-ротация",
        ],
        "security": [
            "Токены площадок шифруются (AES-256) и изолированы по `tenant_id`",
            "Сбои публикации повторяются по политике ретраев с экспоненциальной задержкой",
        ],
        "docs": ["COMPLIANCE.md", "SECURITY.md", "ARCHITECTURE.md"],
    },
    {
        "slug": "blockchain-auditor",
        "title": "Private Blockchain Auditor",
        "comp": "blockchain-auditor", "stage": 2,
        "summary": "Неизменяемый аудит ключевых событий в приватной блокчейн-сети: "
                   "только SHA256-хэши и метаданные, доступ только для Совета.",
        "resp": [
            "Подключение к приватной сети (Besu/Quorum/TON) по gRPC",
            "Детерминированная генерация SHA256-хэшей событий",
            "Пакетная запись (batch) хэшей для эффективности",
            "Контроль доступа (только Совет) и верификация записей",
        ],
        "api": [
            "**POST** `/audit/record` — записать хэш события (batch-агрегация)",
            "**GET** `/audit/verify?hash=` — проверить соответствие события записи",
        ],
        "data": [
            "**audit_records** — `tenant_id`, `event_type`, `hash`, `metadata`, `block_ref`, `created_at`",
        ],
        "deps": [
            "Приватная блокчейн-сеть (`BLOCKCHAIN_AUDITOR_URL`), gRPC",
            "RBAC (роль Совета)",
        ],
        "security": [
            "В сеть пишутся **только** SHA256-хэши и метаданные — без сумм и ПДн",
            "Чтение и запись аудита доступны только роли Совета",
            "Хэш детерминирован (`sort_keys=True`) и верифицируем",
        ],
        "docs": ["SECURITY.md", "GOVERNANCE.md", "ARCHITECTURE.md"],
    },
    {
        "slug": "activity-command-center",
        "title": "Activity Command Center",
        "comp": "activity-center", "stage": 3,
        "summary": "Backend панели Совета и администратора: управление порогами, "
                   "очередями задач и контурами обратной связи.",
        "resp": [
            "Управление порогами и политиками Совета (через Policy Manager)",
            "Очереди задач для агентов и участников",
            "Три контура обратной связи: операционный (1–24 ч), стратегический "
            "(24–72 ч), адаптивный/RL (7–30 дн.)",
            "Агрегация состояния активности для панели Совета",
        ],
        "api": [
            "**GET** `/activity/overview` — сводка активности тенанта",
            "**POST** `/tasks` — создать задачу в очереди",
            "**GET/PUT** `/thresholds` — просмотр и изменение порогов Совета",
        ],
        "data": [
            "**tasks** — `tenant_id`, `type`, `payload`, `status`, `assignee`, `created_at`",
        ],
        "deps": [
            "Policy Manager (пороги и политики)",
            "Analytics Engine (метрики контуров), Notification Gateway",
        ],
        "security": [
            "Изменение порогов и политик доступно только роли Совета",
            "Все изменения порогов фиксируются в аудите",
        ],
        "docs": ["GOVERNANCE.md", "ARCHITECTURE.md"],
    },
    {
        "slug": "neuro-agent-orchestrator",
        "title": "Neuro-Agent Orchestrator",
        "comp": "neuro-agent", "stage": 3,
        "summary": "Оркестрация автономных ИИ-агентов под порогами Совета: работа с "
                   "аудиторией, вовлечение, контент-гигиена, аналитика, анонимность.",
        "resp": [
            "Подмодуль «Аудитория & Парсинг» — анализ аудитории по открытым данным",
            "Подмодуль «Вовлечение & Авто-ответы» — реакции по шаблонам",
            "Подмодуль «Контент & Гигиена» — проверки качества и безопасности",
            "Подмодуль «Аналитика & Оптимизация» — рекомендации (под контролем)",
            "Ротация прокси (HTTP/SOCKS5/MTProto) для устойчивости и анонимности",
        ],
        "api": [
            "**POST** `/agents/run` — запустить задачу агента в рамках порогов",
            "**GET** `/agents/status` — статус и результаты агентов",
        ],
        "deps": [
            "Policy Manager (пороги и этические правила)",
            "Agentic RAG/ChromaDB, прокси-инфраструктура",
        ],
        "security": [
            "Все автономные действия ограничены порогами Совета и логируются",
            "Соблюдение ToS площадок и ФЗ обязательно (см. COMPLIANCE)",
            "Решения AI сопровождаются объяснением (XAI) для проверки Советом",
        ],
        "docs": ["COMPLIANCE.md", "GOVERNANCE.md", "SECURITY.md"],
    },
    {
        "slug": "voice-to-chain",
        "title": "Voice-to-Chain Module",
        "comp": "voice-to-chain", "stage": 3,
        "summary": "Голос → локальная транскрипция (Whisper.cpp) → хэш транскрипта в "
                   "блокчейн; исходное аудио автоматически удаляется в пределах 24 ч.",
        "resp": [
            "Приём голосового ввода и локальная транскрипция через Whisper.cpp",
            "Фиксация SHA256-хэша результата в блокчейн-аудит",
            "Автоматическое удаление исходного аудио (≤ 24 ч)",
        ],
        "api": [
            "**POST** `/voice/transcribe` — отправить аудио, получить транскрипт и хэш",
        ],
        "deps": [
            "Whisper.cpp (локально), Private Blockchain Auditor",
            "Объектное хранилище (временное, с TTL)",
        ],
        "security": [
            "Транскрипция выполняется локально (данные не покидают периметр)",
            "Исходное аудио удаляется автоматически (минимизация ПДн, ФЗ-152)",
        ],
        "docs": ["COMPLIANCE.md", "SECURITY.md"],
    },
    {
        "slug": "wallet",
        "title": "Wallet Module",
        "comp": "wallet", "stage": 3,
        "summary": "Внутренний учёт метрических средств ценности (МСЦ) и операций "
                   "участника. МСЦ — внутренняя метрика, не криптовалюта.",
        "resp": [
            "Ведение баланса МСЦ и истории операций участника",
            "Связь с выплатами и долями распределения",
            "Изоляция по тенанту и аудит операций",
        ],
        "api": [
            "**GET** `/wallet/balance` — баланс МСЦ участника",
            "**GET** `/wallet/operations` — история операций",
        ],
        "data": [
            "**wallet_operations** — `tenant_id`, `member_id`, `amount_mcv`, `type`, `ref`, `created_at`",
        ],
        "deps": [
            "Contribution Ledger, HITL Payout Gateway",
        ],
        "security": [
            "Операции изолированы по `tenant_id` и аудируются",
            "МСЦ не является денежной суммой и не выводится в блокчейн как сумма",
        ],
        "docs": ["ECONOMICS.md", "GLOSSARY.md"],
    },
    {
        "slug": "analytics-engine",
        "title": "Analytics Engine",
        "comp": "analytics", "stage": 3,
        "summary": "Расчёт KPI и агрегатов активности, контента и вовлечённости для "
                   "дашбордов и контуров обратной связи.",
        "resp": [
            "Расчёт KPI пилота (участие, контент, вовлечённость, действия)",
            "Агрегации по тенанту и периодам",
            "Предоставление метрик для дашбордов и RL-KPI loop",
        ],
        "api": [
            "**GET** `/analytics/kpi?period=` — значения KPI за период",
            "**GET** `/analytics/aggregates` — агрегаты по категориям",
        ],
        "deps": [
            "PostgreSQL, источники событий (вклад, публикации, действия)",
        ],
        "security": [
            "Все агрегаты и выборки изолированы по `tenant_id`",
        ],
        "docs": ["ROADMAP.md", "ARCHITECTURE.md"],
    },
    {
        "slug": "notification-gateway",
        "title": "Notification Gateway",
        "comp": "notification", "stage": 3,
        "summary": "Единый шлюз уведомлений о ключевых событиях (вклад, выплаты, вето, "
                   "задачи) по нескольким каналам.",
        "resp": [
            "Подписка на события и шаблоны уведомлений",
            "Доставка по нескольким каналам (мессенджеры, e-mail и т. п.)",
            "Настройки получателя и изоляция по тенанту",
        ],
        "api": [
            "**POST** `/notify` — отправить уведомление по событию",
            "**GET/PUT** `/notify/preferences` — настройки доставки",
        ],
        "deps": [
            "RabbitMQ (события), Unified Messenger Adapter (каналы)",
        ],
        "security": [
            "Уведомления и настройки изолированы по `tenant_id`",
        ],
        "docs": ["ARCHITECTURE.md"],
    },
    {
        "slug": "policy-manager",
        "title": "Policy Manager",
        "comp": "activity-center", "stage": 3,
        "summary": "Централизованное управление политиками и порогами, применяемыми "
                   "всеми автоматизированными модулями и агентами.",
        "resp": [
            "Хранение и версионирование политик и порогов Совета",
            "Предоставление актуальных политик сервисам и агентам",
            "Аудит изменений политик",
            "Конфигурация RL-KPI и этических правил",
        ],
        "api": [
            "**GET** `/policies` — актуальные политики тенанта",
            "**PUT** `/policies/{key}` — изменить политику (роль Совета)",
            "**GET** `/policies/{key}/history` — история версий",
        ],
        "data": [
            "**policies** — `tenant_id`, `key`, `value`, `version`, `updated_by`, `updated_at`",
        ],
        "deps": [
            "Activity Command Center, Neuro-Agent Orchestrator (потребители)",
        ],
        "security": [
            "Изменение политик доступно только роли Совета",
            "Все изменения политик версионируются и аудируются",
        ],
        "docs": ["GOVERNANCE.md", "SECURITY.md"],
    },
    {
        "slug": "api-gateway",
        "title": "API Gateway",
        "comp": "api-gateway", "stage": 1,
        "summary": "Tenant-aware точка входа: маршрутизация, проверка JWT и tenant_id, "
                   "ограничение частоты запросов.",
        "resp": [
            "Единая точка входа для клиентов и сервисов",
            "Проверка JWT (HS256) и извлечение `tenant_id` из токена",
            "Tenant-aware маршрутизация к микросервисам",
            "Ограничение частоты запросов (rate limiting)",
        ],
        "api": [
            "Проксирование `/<service>/...` с проверкой токена и тенанта",
            "Ответ `403 tenant_isolation_violation` при попытке кросс-тенант доступа",
        ],
        "deps": [
            "Сервис аутентификации (JWT/2FA), Redis (лимиты), все микросервисы",
        ],
        "security": [
            "`tenant_id` берётся из JWT и пробрасывается во все запросы",
            "Любой доступ к чужому тенанту → `403`",
            "TLS 1.3+, защита от перебора через rate limiting",
        ],
        "docs": ["SECURITY.md", "ARCHITECTURE.md"],
    },
    {
        "slug": "tenant-isolation",
        "title": "Tenant Isolation Layer",
        "comp": "tenant-core", "stage": 1,
        "summary": "Сквозная изоляция тенантов на всех слоях: БД, кэш, очереди, "
                   "векторная БД, объектное хранилище и логи.",
        "resp": [
            "Единые утилиты контекста тенанта (`tenant_id`) для всех сервисов",
            "Изоляция на уровне БД (фильтры/политики), ChromaDB (коллекции/namespace), "
            "S3/MinIO (префиксы), Redis (ключи), RabbitMQ (маршрутизация)",
            "Гарантия отсутствия межтенантных утечек данных",
        ],
        "api": [
            "Библиотечный слой (middleware/зависимости FastAPI), не публичный REST",
            "Контракт: отсутствие `tenant_id` в контексте → отказ обработки",
        ],
        "deps": [
            "Общая библиотека `shared`, API Gateway, все слои хранения",
        ],
        "security": [
            "Любой межтенантный доступ → `403 tenant_isolation_violation`",
            "Тесты изоляции (cross-tenant → 403) обязательны на всех слоях",
            "0 межтенантных утечек — критерий приёмки этапа 6",
        ],
        "docs": ["SECURITY.md", "ARCHITECTURE.md", "COMPLIANCE.md"],
    },
]


def render(m):
    L = [f"# {m['title']}", ""]
    st = STAGE_NAME.get(m["stage"], "")
    badge = f"**Статус:** 🟡 планируется · **Этап:** {st}"
    if m.get("comp"):
        badge += f" · **Компонент:** `component:{m['comp']}`"
    L += [badge, "", m["summary"], ""]

    L += ["## Зона ответственности"] + [f"- {x}" for x in m["resp"]] + [""]

    if m.get("formulas"):
        L += ["## Ключевые правила и формулы"] + [f"- {x}" for x in m["formulas"]] + [""]

    L += ["## Основные интерфейсы"] + [f"- {x}" for x in m["api"]] + [""]

    if m.get("data"):
        L += ["## Модель данных (черновик)"] + [f"- {x}" for x in m["data"]] + [""]

    L += ["## Зависимости"] + [f"- {x}" for x in m["deps"]] + [""]

    L += ["## Безопасность и мультитенантность"] + [f"- {x}" for x in m["security"]] + [""]

    # связанные issue
    if m.get("comp"):
        rows = issues_for_component(m["comp"])
        if rows:
            L += ["## Связанные задачи (issue)"]
            for num, title, typ in rows:
                L.append(f"- [#{num}]({ISSUE_BASE}/{num}) — {title} (`type:{typ}`)")
            L.append("")

    # связанные документы
    L += ["## Связанные документы"]
    L += [f"- [{d}](../{d})" for d in m.get("docs", [])]
    L += [f"- [Детальный план разработки](../DEVELOPMENT_PLAN.md)"]
    L.append("")

    L += ["---", "<sub>Черновик спецификации. Детализируется на этапе проектирования "
          "соответствующего модуля. Сгенерировано `experiments/gen_module_docs.py`.</sub>", ""]
    return "\n".join(L)


def main():
    os.makedirs("docs/modules", exist_ok=True)
    for m in MODULES:
        path = f"docs/modules/{m['slug']}.md"
        with open(path, "w", encoding="utf-8") as f:
            f.write(render(m))
        print(f"Записано: {path}")
    print(f"\nВсего модулей: {len(MODULES)}")


if __name__ == "__main__":
    main()
