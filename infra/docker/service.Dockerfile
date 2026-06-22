# syntax=docker/dockerfile:1.18

FROM python:3.13.14-slim

ARG SERVICE_NAME=service
ARG SERVICE_PATH=services/service
ARG SERVICE_VERSION=0.0.0-dev
ARG BUILD_DATE=1970-01-01T00:00:00Z
ARG GIT_COMMIT=unknown
ARG GIT_TAG=
ARG IMAGE_SOURCE=https://github.com/xlabtg/Media_Center
ARG DEBIAN_FRONTEND=noninteractive

LABEL org.opencontainers.image.title="Media Center service image" \
      org.opencontainers.image.description="Hardened baseline CI image for Media Center service skeletons" \
      org.opencontainers.image.licenses="AGPL-3.0-only" \
      org.opencontainers.image.source="${IMAGE_SOURCE}" \
      org.opencontainers.image.version="${SERVICE_VERSION}" \
      org.opencontainers.image.revision="${GIT_COMMIT}" \
      org.opencontainers.image.created="${BUILD_DATE}"

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPYCACHEPREFIX=/tmp/python-pyc
ENV TMPDIR=/tmp
ENV PYTHONPATH=/app/service:/app
ENV APP_LOG_DIR=/app/logs
ENV SERVICE_NAME=${SERVICE_NAME}
ENV SERVICE_VERSION=${SERVICE_VERSION}
ENV BUILD_DATE=${BUILD_DATE}
ENV GIT_COMMIT=${GIT_COMMIT}
ENV GIT_TAG=${GIT_TAG}

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends tini \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 1000 app \
    && useradd --uid 1000 --gid 1000 --home-dir /app --shell /usr/sbin/nologin --no-create-home app \
    && mkdir -p /app/service /app/config /app/logs /tmp/python-pyc \
    && chown -R 1000:1000 /app /tmp/python-pyc \
    && chmod 0775 /app/logs \
    && chmod 1777 /tmp

RUN python - <<'PY'
import json
import os
import platform
from pathlib import Path


def env(name: str, default: str = "") -> str:
    value = os.environ.get(name, "").strip()
    return value or default


git_tag = env("GIT_TAG")
service_version = env("SERVICE_VERSION") or git_tag or "0.0.0-dev"
build_info = {
    "service": env("SERVICE_NAME", "service"),
    "version": service_version,
    "build_date": env("BUILD_DATE", "1970-01-01T00:00:00Z"),
    "git_commit": env("GIT_COMMIT", "unknown"),
    "git_tag": git_tag,
    "python": f"Python {platform.python_version()}",
    "python_version": platform.python_version(),
    "python_compiler": platform.python_compiler(),
}
Path("/app/config/build_info.json").write_text(
    json.dumps(build_info, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY

RUN chown 1000:1000 /app/config/build_info.json \
    && chmod 0444 /app/config/build_info.json

COPY --chown=1000:1000 ${SERVICE_PATH}/ /app/service/
COPY --chown=1000:1000 libs/ /app/libs/

USER 1000:1000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:7700/health', timeout=3).read()"

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-c", "import os; print(os.environ.get('SERVICE_NAME', 'service') + ' image is ready')"]
