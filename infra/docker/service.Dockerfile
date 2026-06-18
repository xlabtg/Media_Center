# syntax=docker/dockerfile:1.18

FROM python:3.13.14-slim

ARG SERVICE_NAME=service
ARG SERVICE_PATH=services/service

LABEL org.opencontainers.image.title="Media Center service image"
LABEL org.opencontainers.image.description="Baseline CI image for Media Center service skeletons"
LABEL org.opencontainers.image.licenses="AGPL-3.0-only"

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV SERVICE_NAME=${SERVICE_NAME}

WORKDIR /app

RUN groupadd --system app \
    && useradd --system --gid app --home-dir /app --shell /usr/sbin/nologin app \
    && chown app:app /app

COPY --chown=app:app ${SERVICE_PATH}/README.md ./SERVICE.md
COPY --chown=app:app libs/shared/README.md ./SHARED.md

USER app

CMD ["python", "-c", "import os; print(os.environ.get('SERVICE_NAME', 'service') + ' image is ready')"]
