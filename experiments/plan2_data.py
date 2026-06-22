# -*- coding: utf-8 -*-
"""Декларативное описание «Плана 2» (Этап 9) — issue #213.

Дерево issue для production-grade контейнеризации НМЦ: 6 эпиков (A–F) и 33 задачи.
Все эпики становятся нативными sub-issues #213; задачи — sub-issues своего эпика.
Зависимости (`bdeps` — список ключей-блокеров) проставляются через Dependencies API.

Каждый узел:
  key, title, type ("epic"|"task"|"docs"|"research"), prio, stage=9,
  areas[], comps[], desc, goal,
  task: scope[], acc[], tech?[], deps?[], bdeps?[], docs?[]
  epic: exit[], scope?[], children[], bdeps?[], docs?[]
"""

PARENT_ISSUE = 213
MILESTONE_TITLE = "Этап 9 — Production-grade контейнеризация"
MILESTONE_DESC = (
    "План 2 (issue #213): доведение контейнеризации НМЦ до production-grade — "
    "единый runtime-контракт, эталонный Docker-образ, CI/CD c GHCR/SBOM/подписью, "
    "service-to-service авторизация, оркестрация и операционное превосходство."
)
STAGE9_LABEL = "stage:9-prod-containerization"
STAGE9_LABEL_COLOR = "5319e7"
STAGE9_LABEL_DESC = "Этап 9 — Production-grade контейнеризация (План 2, #213)"

# Документы case-study (резолвятся на main после мерджа PR)
CS = "docs/case-studies/issue-213"
PLAN_DOC = f"{CS}/05-solution-plan.md"
GAP = f"{CS}/02-gap-analysis.md"
RESEARCH = f"{CS}/03-research-and-libraries.md"
COMPETE = f"{CS}/04-competitive-analysis.md"
SRC = f"{CS}/sources/qwen-chat-transcript.md"

# ============================================================================
# ЭПИК A — Эталонный runtime-контракт сервиса (libs/shared)
# ============================================================================
EPIC_A = {
    "key": "A", "type": "epic", "prio": "critical", "stage": 9,
    "title": "🧱 Этап 9 · Эпик A — Эталонный runtime-контракт сервиса (libs/shared)",
    "areas": ["backend"], "comps": ["infra"], "bdeps": [],
    "desc": "Единый программный контракт для всех микросервисов НМЦ: общая фабрика приложения, единый порт, стандартные endpoint'ы (/health, /ready, /info, /metrics), централизованное JSON-логирование и runtime-управление уровнем логов. Фундамент Этапа 9 — на него опираются образ (B), S2S (D) и раскатка (E).",
    "goal": "Получить переиспользуемую фабрику create_base_app() в libs/shared, дающую любому сервису единый сетевой и операционный контракт.",
    "scope": [
        "Спроектировать контракт endpoint'ов и сигнатуру create_base_app()",
        "Реализовать /health, /ready, /info, /metrics, /admin/log-level",
        "Централизовать конфигурацию порта (7700) и JSON-логирования",
    ],
    "exit": [
        "libs/shared/server.py с create_base_app() реализован и покрыт тестами",
        "Сервис на базе create_base_app отвечает на /health, /ready, /info, /metrics на порту 7700",
        "Логи — структурированный JSON в stdout; access-лог выключен по умолчанию",
        "PUT /admin/log-level меняет уровень логирования без рестарта",
        "app_port по умолчанию = 7700; уровень логов задаётся переменной окружения",
    ],
    "docs": [PLAN_DOC, GAP],
    "children": [
        {
            "key": "A1", "type": "task", "prio": "critical", "stage": 9,
            "title": "Этап 9 · A1 — Базовое приложение create_base_app() и порт 7700",
            "areas": ["backend"], "comps": ["infra"], "bdeps": [],
            "desc": "Создать фабрику базового FastAPI-приложения libs/shared/server.py, собирающую единый контракт сервиса (как create_base_app в источнике Qwen) поверх существующего libs/shared/service_template.py. Сейчас образ ничего не запускает, единого входа нет (gap REQ-1/2/3).",
            "goal": "Иметь create_base_app(config) -> FastAPI с зарегистрированными системными роутерами, слушающее 7700.",
            "scope": [
                "Создать libs/shared/server.py с функцией create_base_app(config) -> FastAPI",
                "Переиспользовать create_service_app из libs/shared/service_template.py (middleware, метрики, tenant-контекст)",
                "Зарегистрировать системный роутер (/health, /ready, /info, /metrics, /admin/log-level) единообразно",
                "Прокинуть имя сервиса, версию и build-метаданные в приложение",
            ],
            "acc": [
                "Функция create_base_app(config) возвращает FastAPI-приложение",
                "Приложение поднимается локально и слушает 7700",
                "Системные endpoint'ы зарегистрированы из одного места",
                "Существующая логика service_template переиспользована, не продублирована",
                "Есть unit-тест, проверяющий регистрацию роутеров",
            ],
            "tech": [
                "Базироваться на libs/shared/service_template.py:create_service_app (уже даёт /health, /metrics, middleware)",
                "Сигнатуру взять из источника: см. sources/qwen-chat-transcript.md (Часть 2, server.py)",
                "Системные маршруты через FastAPI APIRouter в libs/shared/server.py",
                "docs_url/redoc_url сделать конфигурируемыми (не хардкодить None)",
            ],
            "deps": ["Нет (фундамент эпика A)"],
            "docs": [PLAN_DOC, SRC],
        },
        {
            "key": "A2", "type": "task", "prio": "high", "stage": 9,
            "title": "Этап 9 · A2 — Разделение /health (liveness) и /ready (readiness)",
            "areas": ["backend"], "comps": ["infra"], "bdeps": ["A1"],
            "desc": "Разделить liveness и readiness. Сейчас в service_template совмещённый /health (REQ-4 частично). По индустрии (03-research) liveness = процесс жив, readiness = зависимости готовы.",
            "goal": "/health всегда 200 пока процесс жив; /ready 200 только когда зависимости (БД/Redis/брокер) доступны.",
            "scope": [
                "Реализовать /health как liveness (без проверки зависимостей, быстрый 200)",
                "Реализовать /ready как readiness с проверками БД/Redis/брокера",
                "Единый формат ответа {service, version, status, checks{...}}",
                "Возврат 503 из /ready при недоступной зависимости",
            ],
            "acc": [
                "GET /health возвращает 200, пока процесс жив",
                "GET /ready возвращает 200 только при готовых зависимостях, иначе 503",
                "Проверки зависимостей вынесены в расширяемый реестр",
                "Тесты на оба сценария (готов/не готов)",
            ],
            "tech": [
                "Опереться на существующие checks в service_template.create_service_app (database, metrics)",
                "Рассмотреть fastapi-healthchecks как образец модульных проверок (03-research)",
                "k8s: /health → livenessProbe, /ready → readinessProbe (эпик E)",
            ],
            "deps": ["create_base_app (A1)"],
            "docs": [PLAN_DOC, RESEARCH],
        },
        {
            "key": "A3", "type": "task", "prio": "high", "stage": 9,
            "title": "Этап 9 · A3 — Endpoint /info и build_info.json",
            "areas": ["backend"], "comps": ["infra"], "bdeps": ["A1"],
            "desc": "Endpoint /info с метаданными сборки: дата/время, версия Python, commit, имя сервиса, реальная версия из git-тега (REQ-5). Сейчас версия захардкожена '0.1.0'.",
            "goal": "/info отдаёт build date, python version, git commit, service name, semver из тега, читая build_info.json с безопасным fallback.",
            "scope": [
                "Endpoint GET /info",
                "Чтение build_info.json (генерируется на сборке образа, B4) из /app/config",
                "Fallback на значения из окружения/по умолчанию при отсутствии файла (локальный запуск)",
                "Версия из git-тега формата вида 01.04.15 (как в источнике)",
            ],
            "acc": [
                "GET /info возвращает build_date, python_version, git_commit, service, version",
                "При отсутствии build_info.json endpoint не падает (fallback)",
                "Версия берётся из тега/файла, а не хардкодится",
                "Тест на наличие всех полей и на fallback",
            ],
            "tech": [
                "Поля по источнику (qwen-chat-transcript.md, /info)",
                "build_info.json генерируется build-args в B4 (BUILD_DATE, GIT_COMMIT, GIT_TAG, SERVICE_VERSION)",
                "python_version через platform.python_version()",
            ],
            "deps": ["create_base_app (A1)", "потребляет build_info.json из B4"],
            "docs": [PLAN_DOC, SRC],
        },
        {
            "key": "A4", "type": "task", "prio": "medium", "stage": 9,
            "title": "Этап 9 · A4 — Унификация endpoint /metrics",
            "areas": ["backend"], "comps": ["infra"], "bdeps": ["A1"],
            "desc": "Гарантировать единый Prometheus-/metrics во всех сервисах через базовое приложение (REQ-6; в service_template есть, но не унифицирован).",
            "goal": "Один и тот же /metrics-контракт у всех сервисов, экспортируется из create_base_app.",
            "scope": [
                "Включить /metrics в create_base_app по умолчанию",
                "Переиспользовать TenantMetricRegistry/DEFAULT_METRICS_PATH из libs/shared/observability.py",
                "Базовые метрики процесса/HTTP (latency, requests, errors)",
            ],
            "acc": [
                "GET /metrics отдаёт Prometheus-экспозицию во всех сервисах на базе create_base_app",
                "Путь /metrics единый и задокументирован",
                "Базовые HTTP-метрики присутствуют",
                "Тест на доступность и формат",
            ],
            "tech": [
                "libs/shared/observability.py (реестр Prometheus), prometheus-client",
                "Совместимость с Prometheus v3.5.4 / OTel Collector из стека",
            ],
            "deps": ["create_base_app (A1)"],
            "docs": [PLAN_DOC],
        },
        {
            "key": "A5", "type": "task", "prio": "high", "stage": 9,
            "title": "Этап 9 · A5 — logging_config.py: JSON-логи в stdout",
            "areas": ["backend"], "comps": ["infra"], "bdeps": ["A1"],
            "desc": "Создать libs/shared/logging_config.py — централизованная настройка логирования: JSON-формат в stdout, access-лог выключен по умолчанию (REQ-7, REQ-8.3). Сейчас общего конфига нет.",
            "goal": "Единая функция настройки логирования, дающая структурированные JSON-логи в консоль для всех сервисов.",
            "scope": [
                "libs/shared/logging_config.py с setup_logging(level)",
                "JSON-форматтер (python-json-logger или structlog)",
                "Вывод только в stdout",
                "Отключить uvicorn access-лог по умолчанию (REQ-8.3)",
            ],
            "acc": [
                "Логи выводятся в stdout в формате JSON",
                "Access-лог выключен по умолчанию",
                "Уровень задаётся параметром/окружением",
                "Тест: запись лога парсится как JSON",
            ],
            "tech": [
                "python-json-logger (как в источнике) или structlog (богаче, 03-research)",
                "Интеграция с uvicorn log config (disable access logger)",
                "libs/shared/logging_config.py",
            ],
            "deps": ["Используется create_base_app (A1)"],
            "docs": [PLAN_DOC, RESEARCH],
        },
        {
            "key": "A6", "type": "task", "prio": "medium", "stage": 9,
            "title": "Этап 9 · A6 — Runtime-смена уровня логов PUT /admin/log-level",
            "areas": ["backend"], "comps": ["infra"], "bdeps": ["A5", "D3"],
            "desc": "Endpoint смены уровня логирования в runtime без рестарта (REQ-8.1), default INFO (REQ-8.2). Защищается S2S-проверкой (D3).",
            "goal": "PUT /admin/log-level меняет уровень корневого логгера на лету; доступен только при валидной S2S-аутентификации.",
            "scope": [
                "Endpoint PUT /admin/log-level принимает {level}",
                "Валидация уровня по списку (DEBUG/INFO/WARNING/ERROR/CRITICAL)",
                "Немедленное применение к корневому логгеру",
                "Защита через S2S dependency (D3)",
            ],
            "acc": [
                "PUT /admin/log-level меняет уровень без рестарта",
                "Недопустимый уровень → 422",
                "Endpoint защищён S2S (401/403 без валидной подписи)",
                "Тест на смену уровня и на защиту",
            ],
            "tech": [
                "logging.getLogger().setLevel(...)",
                "Список уровней синхронизировать с config.LOG_LEVELS (добавить CRITICAL, A7)",
                "Защита — dependency из эпика D (libs/shared/s2s_auth.py)",
            ],
            "deps": ["logging_config (A5)", "S2S-защита (D3)"],
            "docs": [PLAN_DOC],
        },
        {
            "key": "A7", "type": "task", "prio": "high", "stage": 9,
            "title": "Этап 9 · A7 — Конфиг: app_port=7700 и полный набор уровней логов",
            "areas": ["backend"], "comps": ["infra"], "bdeps": ["A1"],
            "desc": "Изменить libs/shared/config.py: app_port по умолчанию 7700 (сейчас 8000), добавить CRITICAL в LOG_LEVELS, обеспечить override через окружение (REQ-3, REQ-8.4).",
            "goal": "Единый дефолтный порт 7700 и полный набор уровней логов, настраиваемые через env.",
            "scope": [
                "AppSettings.app_port: 8000 → 7700",
                "LOG_LEVELS += CRITICAL",
                "Проверить чтение APP_PORT/LOG_LEVEL из окружения",
                "Обновить зависимые места/документацию",
            ],
            "acc": [
                "app_port по умолчанию 7700",
                "CRITICAL присутствует в допустимых уровнях",
                "APP_PORT и LOG_LEVEL переопределяются через env",
                "Существующие тесты конфигурации обновлены/проходят",
            ],
            "tech": [
                "libs/shared/config.py (AppSettings)",
                "Pydantic Settings env-override",
            ],
            "deps": ["create_base_app использует порт (A1)"],
            "docs": [PLAN_DOC, GAP],
        },
        {
            "key": "A8", "type": "task", "prio": "high", "stage": 9,
            "title": "Этап 9 · A8 — Единый ASGI-entrypoint python -m <service>.main",
            "areas": ["backend"], "comps": ["infra"], "bdeps": ["A1", "A7"],
            "desc": "Ввести единый способ старта: python -m <service>.main, поднимающий uvicorn на 7700 через create_base_app. Сейчас main.py определяет app, но раннера нет (gap REQ-2).",
            "goal": "У каждого сервиса единый исполняемый entrypoint, запускающий ASGI-сервер на 7700.",
            "scope": [
                "Шаблон __main__/main с uvicorn.run(app, host, port=7700)",
                "Использовать create_base_app для построения app",
                "Документировать соглашение для всех сервисов",
                "Пример на одном сервисе (contribution-ledger)",
            ],
            "acc": [
                "python -m <service>.main стартует сервис на 7700",
                "Используется create_base_app",
                "Соглашение задокументировано",
                "На примере contribution-ledger показан рабочий запуск",
            ],
            "tech": [
                "services/contribution-ledger/contribution_ledger_app/main.py (есть app=build_app(), добавить runner)",
                "uvicorn programmatic run; host/port из config (A7)",
            ],
            "deps": ["create_base_app (A1)", "config-порт (A7)"],
            "docs": [PLAN_DOC],
        },
    ],
}

# ============================================================================
# ЭПИК B — Эталонный Docker-образ
# ============================================================================
EPIC_B = {
    "key": "B", "type": "epic", "prio": "high", "stage": 9,
    "title": "📦 Этап 9 · Эпик B — Эталонный Docker-образ",
    "areas": ["devops"], "comps": ["infra"], "bdeps": ["A"],
    "desc": "Привести infra/docker/service.Dockerfile из заглушки к production-grade multi-stage образу: минимальный runtime, non-root + hardening, HEALTHCHECK, build_info.json + OCI-метки, единый путь артефакта, готовый entrypoint.",
    "goal": "Единый эталонный образ для всех сервисов: маленький, безопасный, самозапускающийся, с метаданными сборки.",
    "scope": [
        "Multi-stage builder→runtime, минимальный финал",
        "Hardening: non-root, read-only-совместимость, tini, drop caps",
        "HEALTHCHECK, build_info.json, единый путь артефакта, entrypoint",
    ],
    "exit": [
        "docker build даёт работающий образ, docker run поднимает сервис на 7700",
        "В runtime нет компиляторов/build-инструментов; размер в пределах бюджета",
        "Контейнер non-root, read-only-совместим, no-new-privileges, drop caps, tini как PID 1",
        "/info показывает корректные build-данные; HEALTHCHECK проходит",
    ],
    "docs": [PLAN_DOC, GAP],
    "children": [
        {
            "key": "B1", "type": "task", "prio": "critical", "stage": 9,
            "title": "Этап 9 · B1 — Multi-stage Dockerfile (builder→runtime)",
            "areas": ["devops"], "comps": ["infra"], "bdeps": ["A8"],
            "desc": "Переписать infra/docker/service.Dockerfile как multi-stage: builder ставит зависимости/код, runtime содержит только venv+код без компиляторов (REQ-1). Сейчас одностадийный stub, копирующий только README.",
            "goal": "Multi-stage Dockerfile, дающий минимальный runtime без build-инструментов.",
            "scope": [
                "Стадия builder: установка зависимостей в venv, копирование исходников",
                "Стадия runtime: python:3.13-slim, перенос venv и кода из builder",
                "Исключить компиляторы/dev-зависимости из финала",
                ".dockerignore для контекста сборки",
            ],
            "acc": [
                "Dockerfile содержит стадии builder и runtime",
                "В финальном образе нет gcc/build-essential/pip-кэша",
                "docker build проходит для сервиса",
                "Образ запускает приложение (совместно с B6)",
            ],
            "tech": [
                "Базовый образ python:3.13-slim (как в текущем CI и источнике)",
                "COPY --from=builder venv и /app/service",
                "Источник: docker/Dockerfile.service (qwen-chat-transcript.md)",
                "03-research: multi-stage снижает размер ~70%",
            ],
            "deps": ["entrypoint-соглашение (A8)"],
            "docs": [PLAN_DOC, RESEARCH],
        },
        {
            "key": "B2", "type": "task", "prio": "high", "stage": 9,
            "title": "Этап 9 · B2 — Hardening рантайма (non-root, tini, read-only-готовность)",
            "areas": ["devops", "security"], "comps": ["infra"], "bdeps": ["B1"],
            "desc": "Закалить образ/запуск: non-root (uid 1000), tini как PID 1, готовность к read-only rootfs, no-new-privileges и drop caps (REQ-12). Флаги рантайма применяются в compose/k8s (E1/E2).",
            "goal": "Образ безопасен по умолчанию: non-root, корректная обработка сигналов, совместим с read-only fs.",
            "scope": [
                "USER 1000 (non-root), пользователь без shell",
                "Установить и использовать tini как init (PID 1)",
                "Не писать в ФС вне /tmp и /app/logs (готовность к read_only)",
                "Документировать runtime-флаги (no-new-privileges, cap_drop)",
            ],
            "acc": [
                "Контейнер работает от non-root (uid 1000)",
                "tini — PID 1, сигналы (SIGTERM) корректно проксируются",
                "Приложение работает при read-only rootfs + tmpfs для /tmp",
                "Документированы флаги hardening для compose/k8s",
            ],
            "tech": [
                "tini (03-research, источник), ENTRYPOINT [\"/usr/bin/tini\",\"--\"]",
                "writable пути через tmpfs (compose/k8s)",
                "no-new-privileges, cap_drop: ALL — в E1/E2",
            ],
            "deps": ["multi-stage образ (B1)"],
            "docs": [PLAN_DOC, RESEARCH],
        },
        {
            "key": "B3", "type": "task", "prio": "medium", "stage": 9,
            "title": "Этап 9 · B3 — HEALTHCHECK в образе",
            "areas": ["devops"], "comps": ["infra"], "bdeps": ["B1", "A2"],
            "desc": "Добавить HEALTHCHECK, обращающийся к /health на 7700 (REQ-4). В минимальном образе может не быть curl — использовать python.",
            "goal": "Образ декларирует HEALTHCHECK, отражающий liveness сервиса.",
            "scope": [
                "HEALTHCHECK CMD к http://localhost:7700/health",
                "Реализация без внешних утилит (python -c urllib) при отсутствии curl",
                "Разумные interval/timeout/retries/start-period",
            ],
            "acc": [
                "HEALTHCHECK присутствует в Dockerfile",
                "docker inspect показывает healthy при работающем сервисе",
                "Проверка не зависит от наличия curl",
            ],
            "tech": [
                "/health из A2 (liveness)",
                "python -c \"import urllib.request,sys; ...\" как healthcheck",
            ],
            "deps": ["образ (B1)", "/health (A2)"],
            "docs": [PLAN_DOC],
        },
        {
            "key": "B4", "type": "task", "prio": "high", "stage": 9,
            "title": "Этап 9 · B4 — build_info.json и OCI-метки на сборке",
            "areas": ["devops"], "comps": ["infra"], "bdeps": ["B1", "A3"],
            "desc": "Генерировать build_info.json (BUILD_DATE, GIT_COMMIT, GIT_TAG/SERVICE_VERSION, python) через build-args в /app/config; дополнить OCI-метки (REQ-5). Метки частично уже есть.",
            "goal": "Каждый образ несёт машиночитаемые build-метаданные, которые читает /info (A3).",
            "scope": [
                "ARG BUILD_DATE/GIT_COMMIT/GIT_TAG/SERVICE_VERSION",
                "Записать build_info.json в /app/config",
                "Заполнить org.opencontainers.image.* из тех же args",
                "Согласовать поля с /info (A3)",
            ],
            "acc": [
                "build_info.json создаётся на сборке и лежит в /app/config",
                "OCI-метки заполнены (version, revision, created, source)",
                "Поля совпадают с тем, что отдаёт /info",
                "Значения приходят из build-args (через CI, эпик C)",
            ],
            "tech": [
                "Существующие OCI-метки в infra/docker/service.Dockerfile переиспользовать",
                "build-args заполняются в CI (C1)",
            ],
            "deps": ["образ (B1)", "потребляется /info (A3)", "значения из CI (C1)"],
            "docs": [PLAN_DOC],
        },
        {
            "key": "B5", "type": "task", "prio": "medium", "stage": 9,
            "title": "Этап 9 · B5 — Единый путь артефакта /app/service + /app/config",
            "areas": ["devops"], "comps": ["infra"], "bdeps": ["B1"],
            "desc": "Зафиксировать единую структуру артефакта во всех образах: код в /app/service, конфиги/файлы в /app/config (REQ-11). Сейчас образ копирует только README.",
            "goal": "Любой образ имеет предсказуемый путь к артефакту и конфигурации.",
            "scope": [
                "Размещать код сервиса в /app/service",
                "Размещать необходимые файлы/конфиги в /app/config (включая build_info.json)",
                "WORKDIR /app, единый PYTHONPATH",
                "Документировать структуру",
            ],
            "acc": [
                "Код всех сервисов лежит в /app/service",
                "Конфиги/файлы — в /app/config",
                "Структура одинакова для всех сервисов",
                "Документировано в docs",
            ],
            "tech": [
                "Согласовать с источником (container structure tree, qwen-chat-transcript.md)",
                "COPY в Dockerfile, WORKDIR /app",
            ],
            "deps": ["образ (B1)"],
            "docs": [PLAN_DOC, SRC],
        },
        {
            "key": "B6", "type": "task", "prio": "high", "stage": 9,
            "title": "Этап 9 · B6 — Готовый entrypoint.sh",
            "areas": ["devops"], "comps": ["infra"], "bdeps": ["B1", "A8"],
            "desc": "Добавить docker/entrypoint.sh — готовый entrypoint, запускающий приложение на 7700 без ручных шагов (REQ-2). Сейчас CMD печатает 'image is ready'.",
            "goal": "Контейнер самозапускается: entrypoint поднимает сервис.",
            "scope": [
                "docker/entrypoint.sh: запуск python -m <service>.main (или uvicorn) на 7700",
                "Передача сигналов (через tini, B2)",
                "Возможность переопределить команду",
                "ENTRYPOINT/CMD в Dockerfile",
            ],
            "acc": [
                "docker run без аргументов поднимает сервис на 7700",
                "entrypoint корректно завершает работу по SIGTERM",
                "Скрипт исполняемый и задокументирован",
            ],
            "tech": [
                "docker/entrypoint.sh (см. источник entrypoint.sh)",
                "Совместно с A8 (python -m <service>.main) и B2 (tini)",
            ],
            "deps": ["образ (B1)", "entrypoint-соглашение (A8)"],
            "docs": [PLAN_DOC, SRC],
        },
        {
            "key": "B7", "type": "docs", "prio": "medium", "stage": 9,
            "title": "Этап 9 · B7 — ADR оптимизации размера образа и бюджет",
            "areas": ["devops"], "comps": ["infra"], "bdeps": ["B1"],
            "desc": "Принять ADR по оптимизации размера (slim vs distroless), задать числовой бюджет размера (REQ-1, REQ-N1), настроить .dockerignore. Эталон для гейтов F2.",
            "goal": "Зафиксированное решение и числовой бюджет размера образа.",
            "scope": [
                "ADR: slim (по умолчанию) vs distroless (опц. для чувствительных сервисов)",
                "Бюджет размера на сервис (< 250 МБ, stretch < 200 МБ)",
                ".dockerignore для минимизации контекста",
                "Замер базового размера эталонного образа",
            ],
            "acc": [
                "ADR принят и лежит в docs/adr/",
                "Числовой бюджет размера зафиксирован",
                "Базовый размер измерен и задокументирован",
                ".dockerignore присутствует",
            ],
            "tech": [
                "03-research: slim ~75МБ база, distroless-плюсы/минусы (нет shell)",
                "04-competitive: цель < 250 МБ; гейт в CI — F2",
            ],
            "deps": ["образ (B1)"],
            "docs": [PLAN_DOC, COMPETE],
        },
    ],
}

# ============================================================================
# ЭПИК C — CI/CD и публикация в GHCR
# ============================================================================
EPIC_C = {
    "key": "C", "type": "epic", "prio": "high", "stage": 9,
    "title": "🚀 Этап 9 · Эпик C — CI/CD и публикация в GHCR",
    "areas": ["devops"], "comps": ["infra"], "bdeps": ["B"],
    "desc": "Расширить .github/workflows/ci.yml до полноценного релизного пайплайна образов: semver из git-тегов, multi-arch, SBOM, подпись cosign, SLSA-provenance, скан образа, reusable workflow. Сейчас job images пушит sha/latest без версионирования и supply-chain.",
    "goal": "Каждый коммит/тег даёт воспроизводимые, версионированные, подписанные multi-arch образы в GHCR с SBOM и provenance.",
    "scope": [
        "Semver из git-тегов + build-args",
        "Multi-arch (amd64+arm64), кэш слоёв",
        "SBOM + cosign + SLSA provenance + Trivy image-gate, reusable workflow",
    ],
    "exit": [
        "Образы публикуются в GHCR с тегами semver+major.minor+sha+latest",
        "Сборка multi-arch (amd64+arm64)",
        "На каждый образ есть SBOM, подпись cosign и SLSA-provenance",
        "Trivy-скан образа блокирует critical/high",
        "Сборка вынесена в reusable workflow и покрывает все сервисы",
    ],
    "docs": [PLAN_DOC, RESEARCH],
    "children": [
        {
            "key": "C1", "type": "task", "prio": "high", "stage": 9,
            "title": "Этап 9 · C1 — Semver-версия из git-тегов",
            "areas": ["devops"], "comps": ["infra"], "bdeps": ["B4"],
            "desc": "Вычислять версию из git-тегов (git describe / docker/metadata-action) и прокидывать в build-args (REQ-5.5, REQ-9). Сейчас semver-тегов нет.",
            "goal": "Версия образа и build_info берутся из git-тега автоматически.",
            "scope": [
                "docker/metadata-action для тегов semver",
                "git describe --tags для SERVICE_VERSION/GIT_TAG",
                "fetch-depth: 0 в checkout",
                "Передача BUILD_DATE/GIT_COMMIT/GIT_TAG/SERVICE_VERSION в build-args (B4)",
            ],
            "acc": [
                "Версия вычисляется из git-тега",
                "build-args заполняются и попадают в build_info.json",
                "Формат тега соответствует REQ-5.5",
                "Шаг проверен на тестовом теге",
            ],
            "tech": [
                "docker/metadata-action (03-research)",
                "actions/checkout fetch-depth: 0",
                ".github/workflows/ci.yml job images",
            ],
            "deps": ["build_info.json (B4)"],
            "docs": [PLAN_DOC],
        },
        {
            "key": "C2", "type": "task", "prio": "high", "stage": 9,
            "title": "Этап 9 · C2 — Публикация в GHCR (semver+sha+latest)",
            "areas": ["devops"], "comps": ["infra"], "bdeps": ["C1"],
            "desc": "Публиковать образы в GHCR с тегами semver, major.minor, sha и latest, префикс media-center- (REQ-9). Сейчас только sha и latest.",
            "goal": "Образы в ghcr.io с полным набором тегов и корректными правами.",
            "scope": [
                "Теги: {version}, {major.minor}, {sha}, latest",
                "Префикс имени media-center-<service> (сохранить, не nmc-)",
                "permissions: packages: write, docker/login-action с GITHUB_TOKEN",
                "Пуш для main и тегов",
            ],
            "acc": [
                "Образ доступен в GHCR со всеми тегами",
                "Имя использует префикс media-center-",
                "Логин через GITHUB_TOKEN без доп. секретов",
                "latest указывает на последний релиз",
            ],
            "tech": [
                "ghcr.io/${owner}/media-center-${service}",
                "docker/login-action@v3, docker/build-push-action",
                "ADR о префиксе media-center- vs источниковый nmc-",
            ],
            "deps": ["semver (C1)"],
            "docs": [PLAN_DOC, GAP],
        },
        {
            "key": "C3", "type": "task", "prio": "medium", "stage": 9,
            "title": "Этап 9 · C3 — Multi-arch сборка (amd64+arm64)",
            "areas": ["devops"], "comps": ["infra"], "bdeps": ["C2"],
            "desc": "Собирать multi-arch образы (REQ-9, помогает REQ-N1) через buildx+QEMU с gha-кэшем. Сейчас одна архитектура.",
            "goal": "Образы доступны для linux/amd64 и linux/arm64.",
            "scope": [
                "docker/setup-qemu-action + setup-buildx-action",
                "platforms: linux/amd64,linux/arm64",
                "cache-from/to: type=gha",
            ],
            "acc": [
                "Манифест образа содержит amd64 и arm64",
                "Сборка использует кэш слоёв",
                "Время сборки приемлемо",
            ],
            "tech": [
                "03-research (multi-arch buildx)",
                ".github/workflows/ci.yml",
            ],
            "deps": ["GHCR-публикация (C2)"],
            "docs": [PLAN_DOC],
        },
        {
            "key": "C4", "type": "task", "prio": "medium", "stage": 9,
            "title": "Этап 9 · C4 — Генерация SBOM (Syft)",
            "areas": ["devops", "security"], "comps": ["infra"], "bdeps": ["C2"],
            "desc": "Генерировать SBOM на каждый образ (Syft/anchore sbom-action) и публиковать как артефакт/attestation (REQ-N4).",
            "goal": "Каждый образ сопровождается SBOM.",
            "scope": [
                "Шаг генерации SBOM (Syft) на собранный образ",
                "Публикация SBOM (артефакт workflow и/или attestation)",
                "Формат SPDX/CycloneDX",
            ],
            "acc": [
                "SBOM генерируется для каждого образа",
                "SBOM доступен как артефакт/attestation",
                "Формат стандартный (SPDX или CycloneDX)",
            ],
            "tech": [
                "anchore/sbom-action (Syft), 03-research supply-chain",
            ],
            "deps": ["GHCR-публикация (C2)"],
            "docs": [PLAN_DOC, RESEARCH],
        },
        {
            "key": "C5", "type": "task", "prio": "medium", "stage": 9,
            "title": "Этап 9 · C5 — Подпись cosign + SLSA provenance",
            "areas": ["devops", "security"], "comps": ["infra"], "bdeps": ["C2", "C4"],
            "desc": "Подписывать образы cosign (keyless OIDC) и прикреплять SLSA build-provenance (REQ-N4).",
            "goal": "Образы подписаны и имеют верифицируемое происхождение.",
            "scope": [
                "cosign keyless sign (Fulcio/Rekor) по digest",
                "SLSA provenance attestation",
                "Документировать верификацию (cosign verify)",
            ],
            "acc": [
                "Образ подписан cosign (keyless)",
                "Provenance-attestation прикреплён",
                "Инструкция верификации в docs",
            ],
            "tech": [
                "sigstore/cosign, GitHub OIDC (03-research)",
                "SLSA generator / build-provenance",
            ],
            "deps": ["GHCR (C2)", "SBOM (C4)"],
            "docs": [PLAN_DOC, RESEARCH],
        },
        {
            "key": "C6", "type": "task", "prio": "high", "stage": 9,
            "title": "Этап 9 · C6 — Trivy image-scan как гейт",
            "areas": ["devops", "security"], "comps": ["infra"], "bdeps": ["C2"],
            "desc": "Сканировать собранный образ Trivy (сейчас только trivy fs) и блокировать релиз при critical/high (REQ-N4).",
            "goal": "Релиз образа блокируется при критичных уязвимостях.",
            "scope": [
                "aquasecurity/trivy-action в режиме image",
                "Гейт: fail при CRITICAL/HIGH",
                "Отчёт как артефакт",
            ],
            "acc": [
                "Trivy сканирует образ (не только fs)",
                "CI падает при CRITICAL/HIGH",
                "Отчёт сохраняется",
            ],
            "tech": [
                "Уже есть trivy fs в ci.yml — добавить image-скан",
            ],
            "deps": ["GHCR (C2)"],
            "docs": [PLAN_DOC],
        },
        {
            "key": "C7", "type": "task", "prio": "medium", "stage": 9,
            "title": "Этап 9 · C7 — Reusable workflow сборки сервиса",
            "areas": ["devops"], "comps": ["infra"], "bdeps": ["C2"],
            "desc": "Вынести сборку сервиса в переиспользуемый workflow (.github/workflows/build-service.yml) и применять матрицей ко всем сервисам (REQ-9).",
            "goal": "Единый reusable workflow сборки, вызываемый для каждого сервиса.",
            "scope": [
                "build-service.yml как reusable (workflow_call) с inputs (service)",
                "Матрица всех сервисов вызывает reusable",
                "Объединить semver/multi-arch/SBOM/sign/scan",
            ],
            "acc": [
                "build-service.yml вызывается через workflow_call",
                "Матрица покрывает все сервисы",
                "Дублирование шагов устранено",
            ],
            "tech": [
                "Источник: .github/workflows/build-service.yml (qwen-chat-transcript.md)",
                "ci.yml job images → матрица + reusable",
            ],
            "deps": ["GHCR (C2)"],
            "docs": [PLAN_DOC, SRC],
        },
    ],
}

# ============================================================================
# ЭПИК D — Service-to-service авторизация
# ============================================================================
EPIC_D = {
    "key": "D", "type": "epic", "prio": "high", "stage": 9,
    "title": "🔐 Этап 9 · Эпик D — Service-to-service авторизация",
    "areas": ["security", "backend"], "comps": ["infra"], "bdeps": ["A"],
    "desc": "Реализовать межсервисную авторизацию с цепочкой fallback: Kubernetes Service Account → RSA-ключ → общий секрет (REQ-10). Исправить дефекты безопасности из reference-кода источника. Защитить служебные endpoint'ы (/admin/*).",
    "goal": "Безопасная, многоуровневая S2S-аутентификация в libs/shared, защищающая внутренние вызовы и админ-эндпоинты.",
    "scope": [
        "libs/shared/s2s_auth.py: цепочка k8s SA → RSA → secret",
        "Исправление дефектов источника (HMAC, compare_digest, replay, валидация k8s-токена)",
        "Защита /admin/*, документация и тесты",
    ],
    "exit": [
        "libs/shared/s2s_auth.py с цепочкой k8s SA → RSA → secret",
        "Дефекты источника исправлены (полноразмерный HMAC, compare_digest, защита от replay, серверная валидация k8s-токена)",
        "/admin/* защищён S2S; тесты на все методы, replay и timing",
        "Задокументирована модель угроз и план перехода на SPIFFE/mTLS",
    ],
    "docs": [PLAN_DOC, RESEARCH],
    "children": [
        {
            "key": "D1", "type": "task", "prio": "critical", "stage": 9,
            "title": "Этап 9 · D1 — s2s_auth.py: цепочка fallback и фиксы безопасности",
            "areas": ["security", "backend"], "comps": ["infra"], "bdeps": ["A1"],
            "desc": "Создать libs/shared/s2s_auth.py: detect_auth_method + реализации K8s/RSA/SharedSecret. Исправить дефекты reference-кода источника: усечённый 16-hex HMAC → полноразмерный, == → hmac.compare_digest, добавить защиту от replay (timestamp+nonce), серверную валидацию k8s-токена (TokenReview/OIDC, audience). Сейчас S2S нет вовсе.",
            "goal": "Рабочая, безопасная цепочка S2S-аутентификации.",
            "scope": [
                "AuthMethod enum, S2SConfig, detect_auth_method",
                "K8sS2SAuth (projected SA token + серверная валидация audience/issuer)",
                "RSAS2SAuth (RS256, ключ по пути из env/конфига)",
                "SharedSecretS2SAuth (полноразмерный HMAC, compare_digest)",
                "Защита от replay (timestamp + nonce окно)",
                "get_s2s_auth(config) фабрика",
            ],
            "acc": [
                "Реализованы три метода и автоопределение",
                "HMAC полноразмерный, сравнение через hmac.compare_digest",
                "k8s-токен валидируется на стороне сервера (audience/issuer)",
                "Есть защита от replay (timestamp+nonce)",
                "Unit-тесты на каждый метод",
            ],
            "tech": [
                "Исправить дефекты из qwen-chat-transcript.md (раздел s2s_auth.py + «Важные оговорки»)",
                "pyjwt + cryptography для RSA (03-research)",
                "k8s projected SA token, TokenReview/OIDC (03-research, SPIFFE-раздел)",
            ],
            "deps": ["create_base_app/контекст (A1)"],
            "docs": [PLAN_DOC, SRC],
        },
        {
            "key": "D2", "type": "task", "prio": "high", "stage": 9,
            "title": "Этап 9 · D2 — Конфигурация S2S (S2SConfig)",
            "areas": ["security", "backend"], "comps": ["infra"], "bdeps": ["D1"],
            "desc": "Добавить S2SConfig в libs/shared/config.py: метод, пути к ключам/токену, issuer/audience, TTL, окно replay (REQ-10.2 путь через env/конфиг).",
            "goal": "S2S полностью конфигурируется через окружение/конфиг.",
            "scope": [
                "S2SConfig в AppSettings (метод, key_path, token_path, issuer, audience, ttl, replay_window)",
                "Значения по умолчанию и override через env",
                "Интеграция с Vault (существующий VaultSecretProvider) для секрета",
            ],
            "acc": [
                "S2S-параметры читаются из окружения/конфига",
                "Путь к RSA-ключу/токену задаётся через env/файл",
                "Секрет может браться из Vault",
                "Тест конфигурации",
            ],
            "tech": [
                "libs/shared/config.py (AppSettings, VaultSettings)",
                "Pydantic Settings",
            ],
            "deps": ["s2s_auth (D1)"],
            "docs": [PLAN_DOC],
        },
        {
            "key": "D3", "type": "task", "prio": "high", "stage": 9,
            "title": "Этап 9 · D3 — Защита /admin/* (S2S middleware/dependency)",
            "areas": ["security", "backend"], "comps": ["infra"], "bdeps": ["D1", "D2"],
            "desc": "Применить S2S-проверку к служебным endpoint'ам, включая /admin/log-level (REQ-8, REQ-10).",
            "goal": "Внутренние/админ-эндпоинты доступны только при валидной S2S-аутентификации.",
            "scope": [
                "FastAPI dependency/middleware require_s2s",
                "Применить к /admin/* в create_base_app",
                "Корректные коды 401/403",
                "Точка для исходящей подписи в libs/shared/gateway.py",
            ],
            "acc": [
                "/admin/* отвергает запрос без валидной S2S (401/403)",
                "Валидный S2S-запрос проходит",
                "Исходящие межсервисные вызовы подписываются (gateway)",
                "Тесты на доступ/отказ",
            ],
            "tech": [
                "libs/shared/gateway.py (исходящие), libs/shared/auth.py",
                "Зависимость подключается в create_base_app (A1) для /admin/* (A6)",
            ],
            "deps": ["s2s_auth (D1)", "config (D2)"],
            "docs": [PLAN_DOC],
        },
        {
            "key": "D4", "type": "docs", "prio": "medium", "stage": 9,
            "title": "Этап 9 · D4 — Документация, тесты S2S и ADR о SPIFFE/mTLS",
            "areas": ["security"], "comps": ["infra"], "bdeps": ["D1", "D2", "D3"],
            "desc": "Описать модель угроз, добавить тесты на replay/timing и все методы, ADR о переходе на SPIFFE/SPIRE и mTLS (REQ-10).",
            "goal": "S2S задокументирован, протестирован и имеет план эволюции.",
            "scope": [
                "Threat-model и docs по S2S",
                "Тесты: k8s/RSA/secret, replay, timing-safe сравнение",
                "ADR: целевой переход на SPIFFE/SPIRE (JWT-SVID/X.509-SVID) и mTLS",
            ],
            "acc": [
                "Документация S2S с моделью угроз",
                "Тесты покрывают все методы + replay + timing",
                "ADR о SPIFFE/mTLS принят",
            ],
            "tech": [
                "03-research (SPIFFE/SPIRE, projected SA token)",
                "pytest",
            ],
            "deps": ["s2s_auth (D1)", "config (D2)", "защита (D3)"],
            "docs": [PLAN_DOC, RESEARCH],
        },
    ],
}

# ============================================================================
# ЭПИК E — Оркестрация и раскатка контракта
# ============================================================================
EPIC_E = {
    "key": "E", "type": "epic", "prio": "high", "stage": 9,
    "title": "🧩 Этап 9 · Эпик E — Оркестрация и раскатка контракта",
    "areas": ["devops"], "comps": ["infra"], "bdeps": ["A", "B", "D"],
    "desc": "Применить эталонный контракт и образ ко всей системе: docker-compose с приложенческими сервисами (7700 + hardening), k8s/Helm-манифесты (probes, securityContext, ServiceAccount), и раскатка create_base_app + Dockerfile на все 14 сервисов.",
    "goal": "Вся система работает на едином контракте/образе локально (compose) и в k8s.",
    "scope": [
        "docker-compose с приложениями (7700, healthcheck, hardening)",
        "k8s/Helm-манифесты (probes, securityContext, ServiceAccount)",
        "Раскатка единого контракта на все 14 сервисов",
    ],
    "exit": [
        "docker compose поднимает приложения на 7700 с hardening",
        "k8s/Helm-манифесты с liveness/readiness, securityContext, ServiceAccount, Service:7700",
        "Все 14 сервисов переведены на create_base_app + единый образ + entrypoint",
    ],
    "docs": [PLAN_DOC],
    "children": [
        {
            "key": "E1", "type": "task", "prio": "high", "stage": 9,
            "title": "Этап 9 · E1 — docker-compose с приложенческими сервисами",
            "areas": ["devops"], "comps": ["infra"], "bdeps": ["B6"],
            "desc": "Расширить infra/local/docker-compose.yml приложенческими сервисами (порт 7700, healthcheck, read_only+tmpfs, no-new-privileges, cap_drop). Сейчас compose поднимает только инфраструктуру.",
            "goal": "Локально поднимается полный стек приложений с hardening.",
            "scope": [
                "Добавить app-сервисы (image из эталона B)",
                "ports/expose 7700, healthcheck на /health",
                "read_only: true + tmpfs (/tmp, /app/logs)",
                "security_opt: no-new-privileges:true, cap_drop: ALL",
                "depends_on к инфраструктуре с условием healthy",
            ],
            "acc": [
                "docker compose up поднимает приложения на 7700",
                "healthcheck'и зелёные",
                "read_only + tmpfs работают",
                "no-new-privileges и cap_drop применены",
            ],
            "tech": [
                "infra/local/docker-compose.yml (уже есть инфра-сервисы)",
                "Источник: compose snippet (qwen-chat-transcript.md), read_only:true (не k8s-поле)",
            ],
            "deps": ["entrypoint-образ (B6)"],
            "docs": [PLAN_DOC, SRC],
        },
        {
            "key": "E2", "type": "task", "prio": "high", "stage": 9,
            "title": "Этап 9 · E2 — k8s/Helm-манифесты (probes, securityContext, SA)",
            "areas": ["devops", "security"], "comps": ["infra"], "bdeps": ["B6", "D3"],
            "desc": "Создать k8s/Helm-манифесты: Deployment с liveness(/health)/readiness(/ready) probes, securityContext (runAsNonRoot, readOnlyRootFilesystem, allowPrivilegeEscalation:false, drop caps), ServiceAccount (для S2S), Service на 7700.",
            "goal": "Сервисы деплоятся в k8s с probes, hardening и identity для S2S.",
            "scope": [
                "Deployment template (probes, resources, securityContext)",
                "ServiceAccount + projected token (для D)",
                "Service на 7700",
                "Helm chart с values на сервис",
            ],
            "acc": [
                "Манифесты/Helm проходят валидацию (kubeconform/helm lint)",
                "Probes используют /health и /ready",
                "securityContext: non-root, read-only fs, no privilege escalation, drop caps",
                "ServiceAccount настроен для S2S",
            ],
            "tech": [
                "deploy/helm/ или infra/k8s/",
                "03-research (projected SA token, securityContext)",
                "liveness=/health, readiness=/ready (A2)",
            ],
            "deps": ["entrypoint-образ (B6)", "S2S-защита (D3)"],
            "docs": [PLAN_DOC, RESEARCH],
        },
        {
            "key": "E3", "type": "task", "prio": "high", "stage": 9,
            "title": "Этап 9 · E3 — Раскатка единого контракта на все 14 сервисов",
            "areas": ["devops", "backend"], "comps": ["infra"], "bdeps": ["A8", "B6"],
            "desc": "Перевести каждый сервис на create_base_app + единый Dockerfile + entrypoint (применение REQ-1..12). Сейчас единый контракт используют не все.",
            "goal": "Все 14 сервисов используют единый runtime-контракт и образ.",
            "scope": [
                "Чек-лист сервисов (contribution-ledger, cglr, hitl-payout, messenger-adapter, blockchain-auditor, voice-to-chain, neuro-agent, activity-center, wallet, api-gateway/web-cabinet, tenant-core, analytics, notification, infra-утилиты)",
                "Перевод каждого main.py на create_base_app + python -m <service>.main",
                "Сборка каждого эталонным Dockerfile",
                "Проверка /health,/ready,/info,/metrics на 7700",
            ],
            "acc": [
                "Все сервисы используют create_base_app",
                "Все собираются эталонным образом и стартуют на 7700",
                "У всех отвечают /health,/ready,/info,/metrics",
                "Чек-лист по сервисам закрыт",
            ],
            "tech": [
                "services/*/.../main.py",
                "create_base_app (A1), entrypoint (A8/B6)",
            ],
            "deps": ["contract (A8)", "образ (B6)"],
            "docs": [PLAN_DOC],
        },
    ],
}

# ============================================================================
# ЭПИК F — Операционное превосходство и метрики
# ============================================================================
EPIC_F = {
    "key": "F", "type": "epic", "prio": "medium", "stage": 9,
    "title": "📊 Этап 9 · Эпик F — Операционное превосходство и метрики",
    "areas": ["devops"], "comps": ["infra"], "bdeps": ["B", "C", "E"],
    "desc": "Закрыть нефункциональные требования «побить конкурентов по метрикам»: DORA-дашборд, бюджеты размера/cold-start с гейтами в CI, матрица конкурентных метрик, SLO/error budget.",
    "goal": "Измеримое операционное лидерство: метрики DORA, бюджеты, SLO — с автоматическим контролем.",
    "scope": [
        "DORA-дашборд (4 метрики)",
        "CI-гейты на размер образа и cold-start",
        "Матрица конкурентных метрик и SLO/error budget",
    ],
    "exit": [
        "DORA-дашборд (deployment freq, lead time, CFR, MTTR) в Grafana",
        "CI-гейты на размер образа и cold-start",
        "Матрица конкурентных метрик заполнена и поддерживается",
        "Определены SLO/error budget ключевых сервисов с алертами",
    ],
    "docs": [PLAN_DOC, COMPETE],
    "children": [
        {
            "key": "F1", "type": "task", "prio": "medium", "stage": 9,
            "title": "Этап 9 · F1 — DORA-дашборд в Grafana",
            "areas": ["devops"], "comps": ["infra"], "bdeps": ["E3"],
            "desc": "Построить Grafana-дашборд DORA: deployment frequency, lead time, change failure rate, MTTR (REQ-N3).",
            "goal": "Видимость поставочной зрелости по четырём метрикам DORA.",
            "scope": [
                "Источники данных (CI/CD события, инциденты)",
                "Панели для 4 метрик",
                "Провижининг дашборда в infra/local/grafana",
            ],
            "acc": [
                "Дашборд показывает 4 метрики DORA",
                "Дашборд провижинится автоматически",
                "Документированы источники данных",
            ],
            "tech": [
                "infra/local/grafana, Grafana 12.4.4",
                "04-competitive (целевые значения топ-15%)",
            ],
            "deps": ["раскатка (E3)"],
            "docs": [PLAN_DOC, COMPETE],
        },
        {
            "key": "F2", "type": "task", "prio": "medium", "stage": 9,
            "title": "Этап 9 · F2 — Бюджеты размера/cold-start и CI-гейты",
            "areas": ["devops"], "comps": ["infra"], "bdeps": ["C3", "E1"],
            "desc": "Замерять размер образа и время холодного старта до /ready в CI и блокировать превышение бюджета (REQ-N1, REQ-N2).",
            "goal": "Регрессии размера/скорости старта автоматически блокируются.",
            "scope": [
                "Шаг измерения размера образа в CI (бюджет из B7)",
                "Шаг измерения cold-start до /ready (бюджет < 3c)",
                "Гейт fail при превышении",
                "Отчёт/тренд",
            ],
            "acc": [
                "CI измеряет размер образа и падает при превышении бюджета",
                "CI измеряет cold-start до /ready и падает при превышении",
                "Бюджеты вынесены в конфиг/документ",
            ],
            "tech": [
                "Бюджет размера из ADR (B7), цель cold-start из 04-competitive",
                "docker images / запуск контейнера до /ready (A2)",
            ],
            "deps": ["multi-arch сборка (C3)", "compose (E1)"],
            "docs": [PLAN_DOC, COMPETE],
        },
        {
            "key": "F3", "type": "docs", "prio": "low", "stage": 9,
            "title": "Этап 9 · F3 — Матрица конкурентных метрик (живой документ)",
            "areas": ["devops"], "comps": ["infra"], "bdeps": ["F1", "F2"],
            "desc": "Поддерживать живой документ-матрицу осей сравнения из 04-competitive-analysis.md с текущими и целевыми значениями (REQ-M4).",
            "goal": "Прозрачная картина «где мы против индустрии» по релизам.",
            "scope": [
                "Документ-матрица (оси: размер, cold-start, DORA, supply-chain, SLO)",
                "Текущие vs целевые значения",
                "Процесс обновления по релизам",
            ],
            "acc": [
                "Матрица заполнена текущими и целевыми значениями",
                "Привязана к измерениям F1/F2",
                "Описан процесс обновления",
            ],
            "tech": [
                "docs/case-studies/issue-213/04-competitive-analysis.md как основа",
            ],
            "deps": ["DORA (F1)", "бюджеты (F2)"],
            "docs": [PLAN_DOC, COMPETE],
        },
        {
            "key": "F4", "type": "task", "prio": "medium", "stage": 9,
            "title": "Этап 9 · F4 — SLO и error budget ключевых сервисов",
            "areas": ["devops"], "comps": ["infra"], "bdeps": ["E3"],
            "desc": "Определить SLO ключевых сервисов (contribution-ledger, wallet, api-gateway) и error budget с алертами на выгорание (REQ-N5).",
            "goal": "Формализованные SLO и алертинг по error budget.",
            "scope": [
                "Определить SLI/SLO (доступность, latency) ключевых сервисов",
                "Error budget и алерты выгорания (Prometheus/Alertmanager)",
                "Документировать SLO",
            ],
            "acc": [
                "SLO определены для ключевых сервисов",
                "Настроены алерты на выгорание error budget",
                "SLO задокументированы",
            ],
            "tech": [
                "Prometheus/Alertmanager (есть в infra/local)",
                "/metrics (A4) как источник SLI",
            ],
            "deps": ["раскатка (E3)"],
            "docs": [PLAN_DOC],
        },
    ],
}

# Корневой список плана: эпики в порядке создания.
PLAN2 = [EPIC_A, EPIC_B, EPIC_C, EPIC_D, EPIC_E, EPIC_F]
