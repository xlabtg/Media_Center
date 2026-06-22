# 05. План решения «Этап 9»: эпики, задачи, зависимости, трассируемость

Документ — основной выход case-study (REQ-M8). Он превращает требования (`01-requirements.md`) и gap-анализ (`02-gap-analysis.md`) в исполнимый план GitHub-issues: **6 эпиков → ~33 задачи**, нативные sub-issues #213, со связями `blocked_by`, метками, milestone «Этап 9».

## 0. Принципы плана

- **Один родитель в нативной иерархии GitHub.** Sub-issue имеет одного родителя. Поэтому дерево: `#213 → эпики`, `эпик → задачи`. Все эпики — прямые sub-issues #213; задачи — sub-issues своего эпика. Так #213 остаётся корнем, а иерархия видна в UI.
- **Зависимости отдельно от иерархии.** Реальные «blocked_by» проставляются через Issue Dependencies API между конкретными issue (эпик↔эпик и задача↔задача), чтобы порядок работ был виден.
- **Каждая задача самодостаточна (REQ-M11):** контекст, затрагиваемые файлы, эскиз решения, критерии приёмки, тесты, Definition of Done, ссылки на источники/документы case-study.
- **Идемпотентность:** генератор создаёт issue по точному заголовку (повторный запуск не плодит дубликаты), пишет `issue_map_stage9.json`.

## 1. Карта эпиков

| Эпик | Название | Цель | Закрывает REQ | Blocked by |
| --- | --- | --- | --- | --- |
| **A** | Эталонный runtime-контракт (`libs/shared`) | Единый программный контракт сервиса: порт, health/ready, info, metrics, логи, log-level | REQ-3,4,5,6,7,8 | — (фундамент) |
| **B** | Эталонный Docker-образ | Multi-stage, non-root+hardening, HEALTHCHECK, build_info, единый путь, entrypoint | REQ-1,2,11,12; REQ-N1,N2 | A |
| **C** | CI/CD и публикация в GHCR | Semver из тегов, multi-arch, SBOM, cosign, SLSA, скан, reusable workflow | REQ-5,9; REQ-N4 | B |
| **D** | Service-to-service авторизация | Fallback chain k8s SA→RSA→secret, защита `/admin/*`, безопасный HMAC/replay | REQ-10 | A |
| **E** | Оркестрация и раскатка | docker-compose с приложениями, k8s/Helm, раскатка контракта на все 14 сервисов | REQ-3,4,12 (применение) | A, B, D |
| **F** | Операционное превосходство | DORA-дашборд, бюджеты размера/cold-start, матрица метрик, SLO | REQ-N1,N2,N3,N5; REQ-M4 | B, C, E |

### Граф зависимостей (blocked_by)

```
A (фундамент)
├── B  (blocked_by A)
│   └── C  (blocked_by B)
├── D  (blocked_by A)
└── E  (blocked_by A, B, D)
F  (blocked_by B, C, E)
```

## 2. Эпик A — Эталонный runtime-контракт

> Файлы: `libs/shared/server.py` (новый), `libs/shared/service_template.py`, `libs/shared/config.py`, `libs/shared/logging_config.py` (новый).

| ID | Задача | Суть | REQ | Blocked by |
| --- | --- | --- | --- | --- |
| A1 | `create_base_app()` и порт 7700 | Фабрика базового FastAPI-приложения c единым контрактом; интеграция со `service_template` | REQ-3,4,6 | — |
| A2 | Разделение `/health` (liveness) и `/ready` (readiness) | `/health`=процесс жив (200); `/ready`=зависимости готовы (БД/Redis) | REQ-4 | A1 |
| A3 | Endpoint `/info` + `build_info.json` | build date, python-версия, commit, имя, semver из тега; чтение `build_info.json` с fallback | REQ-5 | A1 |
| A4 | Унификация `/metrics` | Единый Prometheus-endpoint во всех сервисах через базовое приложение | REQ-6 | A1 |
| A5 | `logging_config.py` — JSON в stdout | Централизованная настройка, JSON-формат, access-лог выключен по умолчанию | REQ-7,8.3 | A1 |
| A6 | `PUT /admin/log-level` (runtime) | Смена уровня без рестарта; default INFO; защита через S2S (зависит от D) | REQ-8.1,8.2,8.4 | A5, D3 |
| A7 | Конфиг: `app_port=7700`, уровни логов | `app_port` default 7700; добавить `CRITICAL` в `LOG_LEVELS`; env-override | REQ-3,8.4 | A1 |
| A8 | ASGI entrypoint `python -m <service>.main` | Единый способ старта (uvicorn на 7700) для всех сервисов | REQ-2,3 | A1, A7 |

**Критерии приёмки эпика A:** базовое приложение отдаёт `/health`,`/ready`,`/info`,`/metrics`; логи — JSON в stdout; `PUT /admin/log-level` меняет уровень; порт 7700; есть модульные тесты на каждый endpoint.

## 3. Эпик B — Эталонный Docker-образ

> Файлы: `infra/docker/service.Dockerfile`, `docker/entrypoint.sh` (новый), `.dockerignore`, ADR в `docs/adr/`.

| ID | Задача | Суть | REQ | Blocked by |
| --- | --- | --- | --- | --- |
| B1 | Multi-stage builder→runtime | Сборка зависимостей/кода в builder; в runtime только venv+код, без компиляторов | REQ-1 | A8 |
| B2 | Hardening рантайма | non-root (uid 1000), read-only fs, no-new-privileges (через runtime), drop caps, tini как PID 1 | REQ-12 | B1 |
| B3 | `HEALTHCHECK` | Встроенный healthcheck на `/health` (порт 7700) | REQ-4 | B1, A2 |
| B4 | `build_info.json` + OCI-метки | Генерация на сборке через build-args (BUILD_DATE/GIT_COMMIT/GIT_TAG/SERVICE_VERSION) | REQ-5 | B1, A3 |
| B5 | Единый путь артефакта `/app/service` + `/app/config` | Стандартная структура во всех образах | REQ-11 | B1 |
| B6 | `entrypoint.sh` | Готовый entrypoint: запускает приложение на 7700 без ручных шагов | REQ-2 | B1, A8 |
| B7 | ADR оптимизации размера (бюджет) | Решение slim vs distroless; бюджет размера; `.dockerignore` | REQ-1; REQ-N1 | B1 |

**Критерии приёмки эпика B:** `docker build` даёт работающий образ; `docker run` поднимает сервис на 7700; `/health` отвечает; контейнер non-root и проходит проверки hardening; размер в пределах бюджета; `/info` показывает корректные build-данные.

## 4. Эпик C — CI/CD и публикация в GHCR

> Файлы: `.github/workflows/ci.yml`, `.github/workflows/build-service.yml` (новый, reusable).

| ID | Задача | Суть | REQ | Blocked by |
| --- | --- | --- | --- | --- |
| C1 | Semver из git-тегов | `docker/metadata-action` + `git describe`; формат версий из тегов | REQ-5.5,9 | B4 |
| C2 | Публикация в GHCR (semver+sha+latest) | Теги образа: semver, major.minor, sha, latest; префикс `media-center-` | REQ-9 | C1 |
| C3 | Multi-arch (amd64+arm64) | `setup-qemu` + `buildx`, `platforms: linux/amd64,linux/arm64`, gha-cache | REQ-9; REQ-N1 | C2 |
| C4 | SBOM (Syft) | Генерация SBOM на образ, публикация как артефакт/attestation | REQ-N4 | C2 |
| C5 | Подпись cosign + SLSA provenance | Keyless OIDC-подпись образа; build-provenance attestation | REQ-N4 | C2, C4 |
| C6 | Trivy image-scan gate | Скан образа (не только fs); гейт «0 critical/high» | REQ-N4 | C2 |
| C7 | Reusable workflow | Вынести сборку сервиса в переиспользуемый workflow; матрица всех сервисов | REQ-9 | C2 |

**Критерии приёмки эпика C:** при пуше/теге собираются multi-arch образы, публикуются в GHCR с semver+sha+latest, имеют SBOM, подпись cosign и provenance; Trivy-гейт блокирует critical; матрица покрывает все сервисы.

## 5. Эпик D — Service-to-service авторизация

> Файлы: `libs/shared/s2s_auth.py` (новый), `libs/shared/config.py`, `libs/shared/auth.py`, `libs/shared/gateway.py`, тесты, `docs/`.

| ID | Задача | Суть | REQ | Blocked by |
| --- | --- | --- | --- | --- |
| D1 | `s2s_auth.py` — fallback chain | `detect_auth_method` + k8s SA→RSA→shared secret; **исправить дефекты источника**: полноразмерный HMAC, `hmac.compare_digest`, защита от replay (timestamp+nonce), серверная валидация k8s-токена (TokenReview/OIDC, audience) | REQ-10 | A1 |
| D2 | Конфигурация S2S | `S2SConfig` в settings: метод, пути ключей/токена, issuer/audience, TTL, окно replay | REQ-10 | D1 |
| D3 | Защита `/admin/*` (middleware/dependency) | Применение S2S-проверки к служебным endpoint (в т.ч. `/admin/log-level`) | REQ-8,10 | D1, D2 |
| D4 | Документация + тесты S2S | Threat-model, тесты на каждый метод и на replay/timing; ADR о пути к SPIFFE/mTLS | REQ-10 | D1, D2, D3 |

**Критерии приёмки эпика D:** межсервисный вызов аутентифицируется по цепочке; `/admin/*` недоступен без валидной S2S-подписи; тесты покрывают k8s/RSA/secret, replay и timing; задокументирована модель угроз и план перехода на SPIFFE.

## 6. Эпик E — Оркестрация и раскатка

> Файлы: `infra/local/docker-compose.yml`, `infra/k8s/` или `deploy/helm/` (новое), все `services/*/.../main.py`.

| ID | Задача | Суть | REQ | Blocked by |
| --- | --- | --- | --- | --- |
| E1 | docker-compose с приложениями | Добавить app-сервисы (порт 7700, healthcheck, read_only+tmpfs, no-new-privileges, cap_drop) | REQ-3,4,12 | B6 |
| E2 | k8s/Helm-манифесты | Deployment с liveness/readiness probes, securityContext (runAsNonRoot, readOnlyRootFilesystem), ServiceAccount, Service на 7700 | REQ-3,4,10,12 | B6, D3 |
| E3 | Раскатка контракта на все 14 сервисов | Перевести каждый сервис на `create_base_app`+entrypoint+Dockerfile; чек-лист по сервисам | REQ-1..12 | A8, B6 |

**Критерии приёмки эпика E:** `docker compose up` поднимает приложения на 7700 с hardening; k8s-манифесты проходят probes и securityContext; все 14 сервисов используют единый контракт и образ.

## 7. Эпик F — Операционное превосходство (метрики)

> Файлы: `infra/local/grafana/`, `.github/workflows/`, `docs/case-studies/issue-213/metrics/` или `docs/slo/`.

| ID | Задача | Суть | REQ | Blocked by |
| --- | --- | --- | --- | --- |
| F1 | DORA-дашборд | Grafana-дашборд: deployment freq, lead time, CFR, MTTR | REQ-N3 | E3 |
| F2 | Бюджеты размера/cold-start + CI-гейты | Замер размера образа и времени до `/ready` в CI; fail при превышении бюджета | REQ-N1,N2 | C3, E1 |
| F3 | Матрица конкурентных метрик | Живой документ с осями из `04-competitive-analysis.md`, обновление по релизам | REQ-M4 | F1, F2 |
| F4 | SLO/error budget | Определить SLO ключевых сервисов, алерты на выгорание бюджета | REQ-N5 | E3 |

**Критерии приёмки эпика F:** дашборд DORA отображает 4 метрики; CI падает при превышении бюджета размера/cold-start; матрица метрик заполнена целевыми и текущими значениями; заданы SLO и алерты.

## 8. Матрица трассируемости REQ → эпик/задача → критерий

| REQ | Описание | Эпик/задача | Критерий приёмки |
| --- | --- | --- | --- |
| REQ-1 | Multi-stage + минимальный runtime | B1, B7 | В runtime нет компиляторов; размер в бюджете |
| REQ-2 | Готовый entrypoint | B6, A8 | `docker run` поднимает сервис без ручных шагов |
| REQ-3 | Единый порт 7700 | A1, A7, E1, E2 | Все сервисы слушают 7700 |
| REQ-4 | `/health` | A1, A2, B3 | `/health` 200 у всех; есть HEALTHCHECK |
| REQ-5 | `/info` build metadata | A3, B4, C1 | `/info` содержит date/python/commit/name/semver |
| REQ-6 | `/metrics` | A1, A4 | Единый Prometheus-endpoint |
| REQ-7 | Логи в stdout | A5 | JSON-логи в консоль |
| REQ-8 | Runtime log-level | A6, A7, D3 | `PUT /admin/log-level` работает, default INFO, access-лог off |
| REQ-9 | Публикация в GHCR | C1–C7 | Образы в GHCR с semver+sha+latest |
| REQ-10 | S2S auth | D1–D4, E2 | Цепочка k8s→RSA→secret; `/admin/*` защищён |
| REQ-11 | Единый путь артефакта | B5 | `/app/service`+`/app/config` во всех образах |
| REQ-12 | Non-root + hardening | B2, E1, E2 | non-root, read-only, no-new-priv, caps drop, tini |
| REQ-N1 | Малый размер образа | B7, F2 | Бюджет < 250 МБ, гейт в CI |
| REQ-N2 | Быстрый cold-start | F2 | Бюджет < 3 c до `/ready`, гейт в CI |
| REQ-N3 | DORA | F1 | Дашборд с 4 метриками |
| REQ-N4 | Supply-chain | C4, C5, C6 | SBOM+cosign+SLSA, 0 critical |
| REQ-N5 | SLO | F4 | SLO+error budget+алерты |
| REQ-M1..M12 | Мета-процесс | этот case-study + генератор + sub-issues | См. `01-requirements.md` |

## 9. Сводка объёма

- **Эпиков:** 6 (A–F).
- **Задач:** 33 (A:8, B:7, C:7, D:4, E:3, F:4).
- **Всего новых issue:** 39 + связи (sub-issues #213, blocked_by по графу §1).
- **Milestone:** «Этап 9 — Production-grade контейнеризация».
- **Новая метка:** `stage:9-prod-containerization` (+ существующая таксономия type/priority/area/component).

## 10. Как создаются issues (генератор)

`experiments/plan2_data.py` (данные узлов) + `experiments/create_stage9_issues.py` (исполнитель):
1. Создаёт milestone «Этап 9» (если нет) и метку `stage:9-*`.
2. Создаёт эпики A–F (идемпотентно по заголовку), затем задачи.
3. Линкует: эпики → sub-issue #213; задачи → sub-issue своего эпика (Sub-issues API).
4. Проставляет `blocked_by` по графу (Dependencies API).
5. Пишет `experiments/issue_map_stage9.json` (key→number/REST id).
6. Поддерживает `DRY=1` для предпросмотра без записи.
