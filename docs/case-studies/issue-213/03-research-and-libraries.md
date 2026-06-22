# 03. Онлайн-исследование и анализ библиотек

Документ закрывает мета-требования REQ-M6 (поиск фактов онлайн) и REQ-M9 (известные компоненты/библиотеки). Все внешние утверждения снабжены ссылками.

## 1. Multi-stage build и минимальный runtime (REQ-1)

**Факты:**
- Multi-stage отделяет окружение сборки от рантайма: типовой Python-образ ужимается с 1 ГБ+ до < 100 МБ; в практических замерах — снижение размера ~71% и ускорение сборки ~58%.
- Размеры баз: `debian:bookworm-slim` ≈ 75 МБ; distroless base ≈ 23.5 МБ; `gcr.io/distroless/python3` ≈ 80 МБ.
- Объективный «пол» для Python: интерпретатор ≈ 75 МБ + virtualenv ≈ 55 МБ ⇒ минимум ~130 МБ; ультра-минимальный образ для Python недостижим из-за самого интерпретатора.
- Рекомендация 2025: начинать со `slim` для подтверждения совместимости (glibc, без проблем musl), при высоких требованиях к безопасности переходить на distroless, заранее готовя логирование/мониторинг (в distroless нет shell для отладки) и **встроенный healthcheck в приложении** (внешние healthcheck-скрипты недоступны).

**Вывод для плана:** базовый образ — `python:3.13-slim` (как в источнике и текущем CI), multi-stage builder→runtime, без компиляторов в финале. Distroless рассмотреть как опциональный ADR-вариант для особо чувствительных сервисов. Healthcheck — на уровне приложения (`/health`), что согласуется с REQ-4.

Источники: [Reducing Docker Image sizes (Data Build Company)](https://databuildcompany.com/reducing-docker-image-sizes-with-multi-stage-builds-and-distroless/), [Alpine/Distroless/Multi-Stage (OneUptime)](https://oneuptime.com/blog/post/2026-01-16-docker-reduce-image-size/view), [Distroless + uv (Josh Kasuboski)](https://www.joshkasuboski.com/posts/distroless-python-uv/), [Multi-Stage Builds (Cleanstart)](https://www.cleanstart.com/guide/multi-stage-build), [1GB→10MB (BetterLink)](https://eastondev.com/blog/en/posts/dev/20260419-docker-multistage-build/).

## 2. Hardening контейнера (REQ-12)

**Факты:**
- Запуск от non-root снижает риски минимум на ~80% (принцип наименьших привилегий) через директиву `USER` или `--user`.
- Read-only rootfs + `--tmpfs` для writable-путей — ключевая техника закалки.
- Drop всех capabilities и добавление только нужных (например, `NET_BIND_SERVICE`).
- `no_new_privs` — флаг ядра: процесс не получает новых привилегий через `execve()` (setuid/setgid/file caps перестают повышать права).
- Defense-in-depth: комбинация non-root + read-only fs + tmpfs + no-new-privileges + drop caps делает container breakout существенно труднее.

**Вывод:** эпик B/E закладывают полный набор: non-root (uid 1000), `read_only: true` + `tmpfs` для `/tmp` и `/app/logs`, `security_opt: no-new-privileges:true`, `cap_drop: ALL` (+`NET_BIND_SERVICE` только если нужно слушать <1024; порт 7700 этого **не** требует), tini как PID 1 для корректной обработки SIGTERM/зомби.

Источники: [Stop Running Containers as Root (BetterLink)](https://eastondev.com/blog/en/posts/dev/20251218-docker-security-nonroot/), [Docker Security Best Practices (OneUptime)](https://oneuptime.com/blog/post/2026-02-02-docker-security-best-practices/view), [No New Privileges (HackTricks)](https://hacktricks.wiki/en/linux-hardening/privilege-escalation/container-security/protections/no-new-privileges.html), [Docker capabilities & no-new-privs (raesene)](https://raesene.github.io/blog/2019/06/01/docker-capabilities-and-no-new-privs/), [Image Hardening checklist (Botmonster)](https://botmonster.com/self-hosting/harden-docker-images-container-security-checklist/).

## 3. Service-to-service аутентификация (REQ-10)

**Факты:**
- Классическая k8s-аутентификация — bearer service account tokens. Legacy secret-backed токены долгоживущие и трудно ротируемые; **projected SA tokens (bound tokens)** — короткоживущие JWT, существенное улучшение. Но это токены аутентификации, не identity-документы для mTLS.
- SPIFFE/SPIRE — открытый стандарт workload identity. SVID бывает двух форматов: **JWT-SVID** (для HTTP/REST, identity в заголовке Authorization) и **X.509-SVID** (SPIFFE ID в SAN, прямая интеграция с mTLS). Оба короткоживущие (обычно ~1 час, настраивается до минут) и авто-ротируются агентом.
- В k8s SPIRE-агент аттестуется через **k8s_psat** (projected SA token), валидируемый против k8s API.

**Вывод:** требование источника (k8s SA → RSA → shared secret) реализуемо и соответствует индустрии. План:
1. Уровень 1 — k8s **projected** SA token c явной `audience` и серверной валидацией (TokenReview / OIDC issuer), а не «доверие к наличию заголовка».
2. Уровень 2 — RSA (JWT RS256), ключ по пути из env/конфига; короткий TTL, `iss`/`aud`/`exp`/`nonce`.
3. Уровень 3 — shared secret через **HMAC** c `hmac.compare_digest`, полноразмерная подпись, защита от replay (timestamp + nonce окно).
4. SPIFFE/SPIRE и mTLS зафиксировать как **целевую эволюцию** (ADR) для production-mesh.

Источники: [SPIFFE/SPIRE workload identity (OneUptime)](https://oneuptime.com/blog/post/2026-02-09-spiffe-spire-workload-identity-kubernetes/view), [SPIRE Concepts (spiffe.io)](https://spiffe.io/docs/latest/spire-about/spire-concepts/), [SPIFFE/SPIRE on EKS (AWS)](https://aws.amazon.com/blogs/containers/implement-spiffe-spire-authorization-on-amazon-eks/), [What are SPIFFE/SPIRE (Red Hat)](https://www.redhat.com/en/topics/security/spiffe-and-spire), [Goodbye Service API Keys (debugg.ai)](https://debugg.ai/resources/goodbye-service-api-keys-spiffe-spire-workload-identity-zero-trust-mtls-kubernetes-multi-cloud-2025).

## 4. CI/CD: версионирование из git-тегов и GHCR (REQ-5, REQ-9)

**Факты:**
- `docker/metadata-action` авто-генерирует semver-теги из git: `type=semver,pattern={{version}}`, `type=semver,pattern={{major}}.{{minor}}`.
- Версию можно получить через `git describe --tags` (источник использует `--always --dirty`); требует `fetch-depth: 0` в checkout.
- Multi-arch: `docker/setup-qemu-action@v3` + `docker/setup-buildx-action@v3`, `platforms: linux/amd64,linux/arm64`.
- GHCR: `packages: write` permission, `docker/login-action@v3` с `secrets.GITHUB_TOKEN` (доп. секреты не нужны).
- Кэш слоёв: `cache-from/to: type=gha`.

**Вывод:** эпик C использует `docker/metadata-action` для тегов (semver + sha + latest), build-args для `build_info.json`, buildx+QEMU для multi-arch, gha-cache. Это закрывает REQ-9 и питает REQ-5.

Источники: [Semantic Versioned Docker → GH Packages (Jared Hatfield)](https://medium.com/@jaredhatfield/publishing-semantic-versioned-docker-images-to-github-packages-using-github-actions-ebe88fa74522), [Docker versioning/build/push/scan (DEV)](https://dev.to/msrabon/automating-docker-image-versioning-build-push-and-scanning-using-github-actions-388n), [Multiple images auto-versioning (M. van den Burg)](https://mischavandenburg.com/zet/articles/building-multiple-docker-images-using-automatic-versioning-using-github-actions/).

## 5. Supply-chain безопасность (REQ-N4)

**Факты:**
- Стандарты стабилизировались: SLSA 1.0, SPDX 3, Sigstore. `cosign` + Fulcio (CA) + Rekor (transparency log) — production-ready.
- Типовой пайплайн: build → OIDC-auth → подпись `cosign` → push в OCI-registry с подписью и provenance → потребитель верифицирует перед деплоем.
- Практика: SBOM на каждый образ (Syft), хранить как OCI-артефакт/attestation; in-toto SLSA provenance с builder identity, source commit, build-параметрами; `cosign` цепляет подписи/attestations к digest; OCI **referrers** делают их обнаружимыми рядом с образом.
- Зрелость: cosign, Syft, Kyverno, GUAC, GitHub OIDC ⇒ SLSA Level 2 достижим за недели; начинать с detached-подписей + SBOM, далее provenance и policy-gates.

**Вывод:** эпик C/F: Syft SBOM → attach; cosign keyless (OIDC) подпись; SLSA provenance attestation; Trivy-скан образа с гейтом «0 critical». Это даёт измеримое преимущество по REQ-N4.

Источники: [Beyond SBOMs: Sigstore/SLSA/Provenance (AquilaX)](https://aquilax.ai/blog/supply-chain-artifact-signing-slsa), [Signing with Sigstore/Cosign (Secure Pipelines)](https://secure-pipelines.com/ci-cd-security/signing-verifying-container-images-sigstore-cosign/), [SLSA L3 provenance (OneUptime)](https://oneuptime.com/blog/post/2026-02-09-slsa-level3-build-provenance/view), [Supply chain in CI (Nathan Berg)](https://nathanberg.io/posts/supply-chain-security-ci-sbom-slsa-sigstore/).

## 6. FastAPI: health/readiness, логирование, runtime log-level (REQ-4, REQ-7, REQ-8)

**Факты:**
- Разделяют **liveness** (`/health`, всегда 200 если процесс жив) и **readiness** (`/ready`, 200 только когда зависимости — БД/Redis — готовы; для k8s readiness probe).
- Production-наблюдаемость начинается с Request ID + структурированные (JSON) логи + liveness/readiness; JSON-логирование делают через `python-json-logger`/`structlog`/`loguru`.
- Готовые библиотеки: `fastapi-healthchecks` (модульные проверки БД/Redis/и т.п.).
- Прямого «стандартного» механизма смены log-level в runtime во фреймворке нет — реализуется самостоятельно (endpoint, меняющий уровень root-логгера), что и предлагает источник.

**Вывод:** эпик A разделяет `/health` и `/ready`, вводит `libs/shared/logging_config.py` (JSON в stdout, access-лог выключен по умолчанию), endpoint `PUT /admin/log-level` под защитой S2S.

Источники: [Health-check microservice FastAPI (DEV)](https://dev.to/lisan_al_gaib/building-a-health-check-microservice-with-fastapi-26jo), [fastapi-healthchecks (PyPI)](https://pypi.org/project/fastapi-healthchecks/), [Ops-friendly observability FastAPI (greeden)](https://blog.greeden.me/en/2025/10/07/operations-friendly-observability-a-fastapi-implementation-guide-for-logs-metrics-and-traces-request-id-json-logs-prometheus-opentelemetry-and-dashboard-design/), [Readiness vs Liveness for Python (Medium)](https://medium.com/@jtc.21.am/readiness-vs-liveness-and-startup-probes-a-python-developers-guide-to-healthy-services-91fff180f258).

## 7. DORA-метрики (REQ-N3) — см. также `04-competitive-analysis.md`

**Факты (2025):**
- DORA отказался от «Elite»-корзины в пользу перцентилей; верхние 15% — условный «elite».
- Бенчмарки топ-15%: деплой — несколько раз в день; lead time — < 1 дня; change failure rate — < 4–5%.
- Реальность индустрии: лишь 16.2% организаций деплоят on-demand; lead time < 1 часа достигают лишь 9.4% команд.

Источники: [DORA metrics (dora.dev)](https://dora.dev/guides/dora-metrics/), [2025 benchmarks (RDEL #115)](https://rdel.substack.com/p/rdel-115-what-are-the-2025-benchmarks), [Four Keys (Google Cloud)](https://cloud.google.com/blog/products/devops-sre/using-the-four-keys-to-measure-your-devops-performance).

## 8. Реестр библиотек и компонентов (REQ-M9)

### Уже есть в репозитории — переиспользуем
| Компонент | Файл | Роль в Этапе 9 |
| --- | --- | --- |
| Шаблон сервиса (`create_service_app`) | `libs/shared/service_template.py` | База для `create_base_app`: `/health`, `/metrics`, middleware |
| Настройки (Pydantic Settings) + Vault | `libs/shared/config.py` | Порт 7700, log-level, S2S-конфиг, секреты |
| Наблюдаемость / метрики | `libs/shared/observability.py` | Реестр Prometheus, `DEFAULT_METRICS_PATH` |
| Auth / tenant-контекст | `libs/shared/auth.py`, `tenant.py` | Точка интеграции S2S-проверки |
| Gateway (исходящие запросы) | `libs/shared/gateway.py` | Подпись исходящих S2S-запросов |
| Матричный CI + GHCR | `.github/workflows/ci.yml` | База для reusable build-workflow |
| OCI-метки, non-root | `infra/docker/service.Dockerfile` | База для multi-stage Dockerfile |
| Локальная инфраструктура | `infra/local/docker-compose.yml` | Расширяем приложенческими сервисами |

### Внешние библиотеки/инструменты — кандидаты
| Назначение | Кандидат | Примечание |
| --- | --- | --- |
| JSON-логи | `python-json-logger` (источник) или `structlog` | `structlog` богаче (контекст, processors) |
| Метрики | `prometheus-client` | Уже в стеке (Prometheus v3.5.4) |
| Init/PID 1 | `tini` | Минимальный, корректные сигналы (источник) |
| Healthchecks | `fastapi-healthchecks` | Опционально, либо свои проверки |
| JWT (RSA) | `pyjwt` + `cryptography` | Для RSA-fallback S2S |
| SBOM | Syft / `anchore/sbom-action` | Генерация SBOM в CI |
| Подпись | `cosign` (Sigstore) | Keyless OIDC-подпись |
| Provenance | SLSA generator / build-provenance attestation | in-toto attestation |
| Скан образа | Trivy (`aquasecurity/trivy-action`) | Уже есть `trivy fs`; добавить image-скан |
| Метаданные тегов | `docker/metadata-action` | Semver из git |
| Workload identity (целевое) | SPIFFE/SPIRE | ADR на будущее, mTLS-mesh |

## Итог

Исследование подтверждает: **все требования источника соответствуют индустриальному «золотому стандарту» и реализуемы существующими зрелыми инструментами**. Репозиторий уже имеет прочную основу (`service_template.py`, Vault, Prometheus/OTel, матричный CI), что снижает риск и объём. Ключевые улучшения относительно «наивной» реализации источника: разделение liveness/readiness, безопасный HMAC/replay в S2S, серверная валидация k8s-токенов, supply-chain (SBOM/cosign/SLSA) и измеримые бюджеты (размер/cold-start/DORA/SLO) для выполнения REQ-M4.
