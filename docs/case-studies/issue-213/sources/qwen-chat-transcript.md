# Источник: чат Qwen «Народный медиа центр: итоговый отчёт»

> **Провенанс**
> - **URL:** https://chat.qwen.ai/s/t_f8c3278d-fc7f-484e-8981-16bea7cd8ac9?fev=0.2.66
> - **Дата чата:** 21 июня 2026
> - **Извлечено:** 22 июня 2026 через Playwright (`document.body.innerText`), так как страница рендерится через JavaScript и недоступна обычным `fetch`.
> - **Скриншот:** [`../assets/qwen-chat-issue-213.png`](../assets/qwen-chat-issue-213.png)
> - **Сырой текст (как извлечён):** [`qwen-chat-raw-innertext.txt`](qwen-chat-raw-innertext.txt)
>
> Ниже — очищенная от артефактов нумерации строк версия. Код приведён в фрагментах кода в исходном виде. Это **внешний источник**, на который ссылается issue #213; он зафиксирован здесь для целей трассируемости и анализа, а не как готовое к копированию решение.

---

## Часть 1. Требования пользователя (дословно)

> Нужно чтобы код строился и готовая программа переносилась в чистый контейнер, где есть только необходимый минимум, без дебагиров, компиляторов и прочего… минимальный runtime
>
> У контейнера должен быть готовый entrypoiny, который при старте контейнера сам запустит апликацию…
>
> Все сервисы должны запаускаться но одинаковом порту, например 7700 (мне просто цифра понравилась 😊)
>
> Все сервисы должны отвечать на одинаковый endpoint для healthcheck проверок
>
> Все сервисы должны иметь информационный endpoint, содержат билд дату и время, компайлер версию, коммит hash и название сервиса, если у сервиса есть реальная весртя 01.04.15 она так же должна содержаться (я бы делал тэги в самом репо и использовал их для версий)
>
> Все сервисы должны иметь (по идее уже имеют) endpoint для метрик
>
> Все сервисы должны выводить логик в консоль
>
> Нужен endpoint для изменения log level в runtime, по умолчанию я бы поставил info, без request log… так же лог должен задаваться при старте
>
> У github есть docker registry, не знаю какая цена вопроса, но все готовые контейнеры стоит загружать туда…
>
> Так же я бы добавил авторизацию между сервисам через service account, но если api kubernetes не доступен, тогда secret, а еще лучше rsa key (путь к точке монтирования) должен быть передан в переменной окружения или в файле конфигурации
>
> Я бы клал артифакт сервиса во всех контейнерах по одному пути, а все необходимые файлы в папку конф рядом с ним
>
> Еще кое что, контенер и сервис должны запускаться от не привелигтрованного пользователя

---

## Часть 2. Ответ ассистента Qwen (предложенное решение)

> Дисклеймер (из источника): данный ответ сгенерирован искусственным интеллектом и может содержать неточности.

Ассистент квалифицировал требования как «золотой стандарт enterprise containerization» и предложил конкретные файлы и код.

### 🏗 1. Multi-stage build + минимальный runtime — `docker/Dockerfile.service`

```dockerfile
# ============ STAGE 1: Builder ============
FROM python:3.13-slim AS builder
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
# Системные зависимости для компиляции
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    && rm -rf /var/lib/apt/lists/*
# Копируем только requirements для кеширования слоёв
COPY requirements.txt requirements-prod.txt ./
RUN pip install --prefix=/install -r requirements-prod.txt
# Копируем код и собираем
COPY . /src
WORKDIR /src
# Инжектим build metadata на этапе сборки
ARG SERVICE_NAME
ARG SERVICE_VERSION=0.0.0-dev
ARG BUILD_DATE
ARG GIT_COMMIT=unknown
ARG GIT_TAG=
RUN echo "{\"service\":\"${SERVICE_NAME}\",\"version\":\"${SERVICE_VERSION}\",\"build_date\":\"${BUILD_DATE}\",\"git_commit\":\"${GIT_COMMIT}\",\"git_tag\":\"${GIT_TAG}\",\"python\":\"$(python -V 2>&1)\"}" \
    > /src/build_info.json

# ============ STAGE 2: Runtime (minimal) ============
FROM python:3.13-slim AS runtime
# Non-root пользователь
RUN groupadd -r app && useradd -r -g app -d /app -s /sbin/nologin app
# Только runtime-зависимости (без компиляторов)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    tini \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
# Копируем только установленные пакеты и код
COPY --from=builder /install /usr/local
COPY --from=builder /src/build_info.json /app/build_info.json
COPY --from=builder /src/libs /app/libs
COPY --from=builder /src/services/${SERVICE_NAME} /app/service
# Единая структура: артефакт + конфиги рядом
RUN mkdir -p /app/config /app/data /app/logs \
    && chown -R app:app /app
# Единый порт
ENV SERVICE_PORT=7700 \
    SERVICE_NAME=${SERVICE_NAME} \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
# Healthcheck на уровне Docker
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://127.0.0.1:7700/health || exit 1
# Tini как init-система (корректная обработка SIGTERM)
ENTRYPOINT ["/usr/bin/tini", "--"]
# Entrypoint-скрипт
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
USER app
EXPOSE 7700
CMD ["/entrypoint.sh"]
```

### `docker/entrypoint.sh`

```bash
#!/bin/sh
set -e
# Если есть миграции — накатываем
if [ -f "/app/service/alembic.ini" ] && [ "${RUN_MIGRATIONS:-false}" = "true" ]; then
    echo "[entrypoint] Running migrations..."
    cd /app/service && alembic upgrade head
    cd /app
fi
# Запуск приложения
echo "[entrypoint] Starting ${SERVICE_NAME} on port ${SERVICE_PORT}..."
exec python -m service.main
```

### 🔧 2. Единый порт 7700 + стандартные endpoints — `libs/shared/server.py`

```python
"""libs/shared/server.py"""
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    generate_latest,
    multiprocess,
)

from libs.shared.config import get_settings
from libs.shared.logging_config import setup_logging

# Build metadata (инжектится при сборке)
BUILD_INFO_PATH = Path("/app/build_info.json")
BUILD_INFO = json.loads(BUILD_INFO_PATH.read_text()) if BUILD_INFO_PATH.exists() else {
    "service": os.getenv("SERVICE_NAME", "unknown"),
    "version": "0.0.0-dev",
    "build_date": "unknown",
    "git_commit": "unknown",
    "git_tag": "",
    "python": "unknown",
}

# Runtime log level (меняется через API)
_log_level = os.getenv("LOG_LEVEL", "INFO").upper()
_logger = logging.getLogger(__name__)


def _get_registry() -> CollectorRegistry:
    if "prometheus_multiproc_dir" in os.environ:
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
        return registry
    from prometheus_client import REGISTRY
    return REGISTRY


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging(level=_log_level, service_name=BUILD_INFO["service"])
    _logger.info(
        "Service started",
        extra={
            "service": BUILD_INFO["service"],
            "version": BUILD_INFO["version"],
            "commit": BUILD_INFO["git_commit"],
            "build_date": BUILD_INFO["build_date"],
        },
    )
    yield
    _logger.info("Service shutting down")


def create_base_app(service_name: str | None = None) -> FastAPI:
    """Базовое FastAPI-приложение со всеми стандартными endpoints."""
    name = service_name or BUILD_INFO["service"]
    app = FastAPI(
        title=name,
        version=BUILD_INFO["version"],
        lifespan=lifespan,
        docs_url=None,       # Отключаем в production
        redoc_url=None,
        openapi_url=None,
    )

    # ============ /health ============
    @app.get("/health", tags=["system"])
    async def health() -> dict:
        """Liveness probe — сервис жив."""
        return {"status": "ok", "service": name, "ts": datetime.now(timezone.utc).isoformat()}

    # ============ /ready ============
    @app.get("/ready", tags=["system"])
    async def ready() -> tuple[Response, int]:
        """Readiness probe — сервис готов принимать трафик."""
        # Здесь можно добавить проверки БД, Redis, RabbitMQ
        try:
            # await check_database()
            # await check_redis()
            return JSONResponse({"status": "ready"}), 200
        except Exception as e:
            return JSONResponse({"status": "not_ready", "error": str(e)}), 503

    # ============ /info ============
    @app.get("/info", tags=["system"])
    async def info() -> dict:
        """Build metadata."""
        return BUILD_INFO

    # ============ /metrics ============
    @app.get("/metrics", tags=["system"])
    async def metrics() -> Response:
        """Prometheus metrics."""
        registry = _get_registry()
        return Response(
            content=generate_latest(registry),
            media_type=CONTENT_TYPE_LATEST,
        )

    # ============ /admin/log-level ============
    @app.get("/admin/log-level", tags=["admin"])
    async def get_log_level() -> dict:
        return {"level": _log_level}

    @app.put("/admin/log-level", tags=["admin"])
    async def set_log_level(level: str) -> dict:
        global _log_level
        level = level.upper()
        if level not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            return JSONResponse({"error": f"invalid level: {level}"}, status_code=400)
        _log_level = level
        logging.getLogger().setLevel(level)
        _logger.info("Log level changed", extra={"new_level": level})
        return {"level": level}

    return app
```

Каждый сервис создаётся в несколько строк:

```python
# services/contribution-ledger/main.py
from libs.shared.server import create_base_app
from services.contribution_ledger.routes import router

app = create_base_app("contribution-ledger")
app.include_router(router, prefix="/api/v1/contributions")
```

### 📊 3. Логи в консоль + без request log по умолчанию — `libs/shared/logging_config.py`

```python
import logging
import sys

from pythonjsonlogger import jsonlogger


def setup_logging(level: str = "INFO", service_name: str = "unknown"):
    root = logging.getLogger()
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    formatter = jsonlogger.JsonFormatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        rename_fields={
            "asctime": "ts",
            "levelname": "level",
            "name": "logger",
        },
    )
    handler.setFormatter(formatter)
    root.addHandler(handler)
    root.setLevel(level)

    # uvicorn access logs — отключены по умолчанию
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("uvicorn").setLevel(logging.INFO)
```

### 🐳 4. Build & Push в GHCR — `.github/workflows/build-service.yml`

```yaml
name: Build and Push Service
on:
  push:
    tags: ['v*.*.*']
    branches: [main]

permissions:
  contents: read
  packages: write

jobs:
  build:
    strategy:
      matrix:
        service:
          - api-gateway
          - contribution-ledger
          - cglr
          - hitl-payout-gateway
          - messenger-adapter
          - blockchain-auditor
          # ... все сервисы

    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0  # Для git describe

      - name: Metadata
        id: meta
        run: |
          SERVICE=${{ matrix.service }}
          VERSION=$(git describe --tags --always --dirty 2>/dev/null || echo "0.0.0-dev")
          TAG=${VERSION}
          # Если это тег релиза — используем его как версию
          if [[ "${GITHUB_REF}" == refs/tags/v* ]]; then
            TAG=${GITHUB_REF#refs/tags/v}
          fi
          echo "service=$SERVICE" >> $GITHUB_OUTPUT
          echo "version=$TAG" >> $GITHUB_OUTPUT
          echo "commit=$(git rev-parse HEAD)" >> $GITHUB_OUTPUT
          echo "build_date=$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> $GITHUB_OUTPUT
          echo "image=ghcr.io/${{ github.repository_owner }}/nmc-${SERVICE}:${TAG}" >> $GITHUB_OUTPUT

      - name: Login to GHCR
        uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Set up QEMU + Buildx
        uses: docker/setup-qemu-action@v3
      - uses: docker/setup-buildx-action@v3

      - name: Build & Push
        uses: docker/build-push-action@v5
        with:
          context: .
          file: docker/Dockerfile.service
          push: true
          platforms: linux/amd64,linux/arm64
          build-args: |
            SERVICE_NAME=${{ steps.meta.outputs.service }}
            SERVICE_VERSION=${{ steps.meta.outputs.version }}
            BUILD_DATE=${{ steps.meta.outputs.build_date }}
            GIT_COMMIT=${{ steps.meta.outputs.commit }}
            GIT_TAG=${{ github.ref_name }}
          tags: |
            ${{ steps.meta.outputs.image }}
            ghcr.io/${{ github.repository_owner }}/nmc-${{ steps.meta.outputs.service }}:latest
          cache-from: type=gha
          cache-to: type=gha,mode=max
```

#### 💰 Про цену GHCR (из источника)

- Public packages: бесплатно (для публичных репозиториев).
- Private packages: 500 MB storage + 1 GB egress/мес бесплатно (Free plan). Дальше ~$0.008/GB storage, ~$0.05/GB egress.
- Для проекта с 14 сервисами по ~100–200 MB каждый — вписывается в бесплатный лимит с запасом.

### 🔐 5. Service-to-service auth с fallback chain — `libs/shared/s2s_auth.py`

```python
"""Service-to-service authentication with fallback chain:
1. Kubernetes Service Account token (если доступен)
2. Shared RSA key (mounted file)
3. Shared secret (env var) — last resort
"""
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


class AuthMethod(Enum):
    K8S_SA = "kubernetes_sa"
    RSA_KEY = "rsa_key"
    SHARED_SECRET = "shared_secret"
    NONE = "none"


@dataclass
class S2SConfig:
    method: AuthMethod
    # For K8S
    k8s_token_path: str = "/var/run/secrets/kubernetes.io/serviceaccount/token"
    k8s_audience: str = "nmc-services"
    # For RSA
    rsa_private_key_path: str | None = None
    rsa_public_key_path: str | None = None
    # For shared secret
    shared_secret: str | None = None


def detect_auth_method() -> S2SConfig:
    """Auto-detect best available auth method."""
    # 1. K8s Service Account
    k8s_token = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")
    if k8s_token.exists() and os.getenv("K8S_AUTH_ENABLED", "true") == "true":
        return S2SConfig(
            method=AuthMethod.K8S_SA,
            k8s_audience=os.getenv("S2S_AUDIENCE", "nmc-services"),
        )

    # 2. RSA key (mounted)
    rsa_priv = os.getenv("S2S_RSA_PRIVATE_KEY_PATH")
    rsa_pub = os.getenv("S2S_RSA_PUBLIC_KEY_PATH")
    if rsa_priv and Path(rsa_priv).exists():
        return S2SConfig(
            method=AuthMethod.RSA_KEY,
            rsa_private_key_path=rsa_priv,
            rsa_public_key_path=rsa_pub,
        )

    # 3. Shared secret (fallback)
    secret = os.getenv("S2S_SHARED_SECRET")
    if secret:
        return S2SConfig(method=AuthMethod.SHARED_SECRET, shared_secret=secret)

    # 4. None (dev only!)
    return S2SConfig(method=AuthMethod.NONE)


class S2SAuthenticator(Protocol):
    def sign_request(self, headers: dict) -> dict: ...
    def verify_request(self, headers: dict) -> bool: ...


class K8sS2SAuth:
    """Uses Kubernetes Service Account token as bearer."""
    def __init__(self, config: S2SConfig):
        self.token = Path(config.k8s_token_path).read_text()
        self.audience = config.k8s_audience

    def sign_request(self, headers: dict) -> dict:
        headers["Authorization"] = f"Bearer {self.token}"
        headers["X-S2S-Method"] = AuthMethod.K8S_SA.value
        return headers

    def verify_request(self, headers: dict) -> bool:
        # В k8s-кластере token validation делает API server
        # Мы доверяем, что k8s network policy уже ограничил доступ
        return "Authorization" in headers


class RSAS2SAuth:
    """Uses RSA key pair for request signing."""
    def __init__(self, config: S2SConfig):
        with open(config.rsa_private_key_path, "rb") as f:
            self.private_key = serialization.load_pem_private_key(f.read(), password=None)
        with open(config.rsa_public_key_path, "rb") as f:
            self.public_key = serialization.load_pem_public_key(f.read())

    def sign_request(self, headers: dict) -> dict:
        import time
        payload = {
            "iss": os.getenv("SERVICE_NAME", "unknown"),
            "aud": "nmc-services",
            "iat": int(time.time()),
            "exp": int(time.time()) + 60,  # 1 min TTL
        }
        token = jwt.encode(payload, self.private_key, algorithm="RS256")
        headers["Authorization"] = f"Bearer {token}"
        headers["X-S2S-Method"] = AuthMethod.RSA_KEY.value
        return headers

    def verify_request(self, headers: dict) -> bool:
        auth = headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return False
        try:
            token = auth[7:]
            jwt.decode(token, self.public_key, algorithms=["RS256"], audience="nmc-services")
            return True
        except jwt.InvalidTokenError:
            return False


class SharedSecretS2SAuth:
    """HMAC-based shared secret (last resort)."""
    def __init__(self, config: S2SConfig):
        self.secret = config.shared_secret

    def sign_request(self, headers: dict) -> dict:
        import hashlib, time
        ts = str(int(time.time()))
        sig = hashlib.sha256(f"{ts}:{self.secret}".encode()).hexdigest()[:16]
        headers["X-S2S-Signature"] = sig
        headers["X-S2S-Timestamp"] = ts
        headers["X-S2S-Method"] = AuthMethod.SHARED_SECRET.value
        return headers

    def verify_request(self, headers: dict) -> bool:
        import hashlib
        ts = headers.get("X-S2S-Timestamp")
        sig = headers.get("X-S2S-Signature")
        if not ts or not sig:
            return False
        expected = hashlib.sha256(f"{ts}:{self.secret}".encode()).hexdigest()[:16]
        return sig == expected


def get_s2s_auth() -> S2SAuthenticator:
    config = detect_auth_method()
    match config.method:
        case AuthMethod.K8S_SA: return K8sS2SAuth(config)
        case AuthMethod.RSA_KEY: return RSAS2SAuth(config)
        case AuthMethod.SHARED_SECRET: return SharedSecretS2SAuth(config)
        case AuthMethod.NONE:
            import logging
            logging.getLogger(__name__).warning("S2S auth disabled — dev mode only!")
            class NoopAuth:
                def sign_request(self, h): return h
                def verify_request(self, h): return True
            return NoopAuth()
```

> ⚠️ **Замечание команды (не из источника).** Этот референс-код имеет уязвимости, которые нельзя переносить в продакшен без доработки: усечение HMAC/SHA-256 до 16 hex-символов (64 бита) и сравнение через `==` (тайминг-атаки) — нужно `hmac.compare_digest` и полноразмерная подпись; нет nonce/replay-защиты, кроме `exp`; `K8sS2SAuth.verify_request` фактически не валидирует токен (доверяет наличию заголовка). В нашем плане эти места закрываются в эпике D (см. `05-solution-plan.md`).

### 📁 6. Единая структура контейнера

```text
/app/                          # WORKDIR, non-root владелец
├── service/                   # Артефакт (код сервиса)
│   ├── main.py
│   ├── routes.py
│   └── ...
├── libs/                      # Общие библиотеки
├── config/                    # Конфиги (mounted)
│   ├── app.yaml
│   └── policies.yaml
├── data/                      # Runtime data (mounted volume)
├── logs/                      # Если нужны file-логи (обычно только stdout)
├── build_info.json            # Build metadata
└── entrypoint.sh              # (в /entrypoint.sh)
```

В `docker-compose.yml`:

```yaml
services:
  contribution-ledger:
    image: ghcr.io/xlabtg/nmc-contribution-ledger:latest
    user: "1000:1000"           # app:app
    read_only_root_filesystem: true
    security_opt:
      - no-new-privileges:true
    volumes:
      - ./config/contribution-ledger:/app/config:ro
      - ledger-data:/app/data
    environment:
      SERVICE_PORT: "7700"
      LOG_LEVEL: "INFO"
      S2S_RSA_PRIVATE_KEY_PATH: /run/secrets/s2s-private-key
    secrets:
      - s2s-private-key
      - s2s-public-key
```

### 📋 7. Checklist соответствия требованиям (из источника)

| Требование | Реализация | Статус |
| --- | --- | --- |
| Multi-stage build | `Dockerfile.service` (builder → runtime) | ✅ |
| Минимальный runtime | `python:3.13-slim` + tini + curl | ✅ |
| Готовый entrypoint | `/entrypoint.sh` с `exec` | ✅ |
| Единый порт 7700 | `ENV SERVICE_PORT=7700` + `EXPOSE 7700` | ✅ |
| Healthcheck endpoint | `/health` + `/ready` (liveness/readiness) | ✅ |
| Build info endpoint | `/info` (версия, commit, дата, python, тег) | ✅ |
| Metrics endpoint | `/metrics` (Prometheus) | ✅ |
| Логи в консоль | JSON-логи в stdout | ✅ |
| Runtime log level | `PUT /admin/log-level` | ✅ |
| GHCR push | GitHub Actions workflow | ✅ |
| S2S auth (k8s → RSA → secret) | `libs/shared/s2s_auth.py` с fallback chain | ✅ |
| Единый путь артефакта | `/app/service` + `/app/config` | ✅ |
| Non-root user | `USER app` (uid 1000) | ✅ |
| Security hardening | `read_only_root_filesystem` + `no-new-privileges` | ✅ |

### 💡 Честная оценка (из источника)

| Что есть | Что нужно доделать |
| --- | --- |
| Docker-compose работает | Нужен multi-stage Dockerfile |
| FastAPI-сервисы есть | Нужен `create_base_app()` с едиными endpoints |
| Prometheus настроен | Нужен `/metrics` endpoint в каждом сервисе |
| Vault есть | Нужен S2S auth (сейчас только Internal Headers Trust) |
| Логи есть | Нужен runtime log-level + JSON формат |

Оценка усилий из источника: «2–3 дня работы». Ассистент предложил продолжить с полного `Dockerfile.service`, реализации `create_base_app()`, адаптации `services/*/main.py` и настройки GHCR workflow с matrix-сборкой 14 сервисов.

---

## Важные оговорки при использовании источника

1. **Имена образов.** В источнике предлагается префикс `ghcr.io/<owner>/nmc-<service>`. В текущем CI репозитория уже используется `ghcr.io/<owner>/media-center-<service>` (см. `.github/workflows/ci.yml`). В плане сохраняем существующую схему именования во избежание разрыва совместимости (детали в `02-gap-analysis.md`).
2. **`docker-compose` ключи.** `read_only_root_filesystem` — это поле Kubernetes `securityContext`, а не Compose; в Compose используется `read_only: true`. Это поправлено в нашем solution plan.
3. **Безопасность референс-кода S2S.** См. предупреждение выше — план закрывает эти дефекты.
4. **`docs_url=None` для всех сервисов.** Полное отключение OpenAPI может конфликтовать с потребностями API Gateway/тестов; в плане делаем это конфигурируемым (по умолчанию выключено в prod, включаемо через переменную окружения).
