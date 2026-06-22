# syntax=docker/dockerfile:1.18

FROM python:3.13.14-slim

ARG SERVICE_NAME=service
ARG SERVICE_PATH=services/service
ARG DEBIAN_FRONTEND=noninteractive

LABEL org.opencontainers.image.title="Media Center service image"
LABEL org.opencontainers.image.description="Hardened baseline CI image for Media Center service skeletons"
LABEL org.opencontainers.image.licenses="AGPL-3.0-only"

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPYCACHEPREFIX=/tmp/python-pyc
ENV TMPDIR=/tmp
ENV APP_LOG_DIR=/app/logs
ENV SERVICE_NAME=${SERVICE_NAME}

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends tini \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 1000 app \
    && useradd --uid 1000 --gid 1000 --home-dir /app --shell /usr/sbin/nologin --no-create-home app \
    && mkdir -p /app/logs /tmp/python-pyc \
    && chown -R 1000:1000 /app /tmp/python-pyc \
    && chmod 0775 /app/logs \
    && chmod 1777 /tmp

COPY --chown=1000:1000 ${SERVICE_PATH}/README.md ./SERVICE.md
COPY --chown=1000:1000 libs/shared/README.md ./SHARED.md

USER 1000:1000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:7700/health', timeout=3).read()"

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-c", "import os; print(os.environ.get('SERVICE_NAME', 'service') + ' image is ready')"]
