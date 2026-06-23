# syntax=docker/dockerfile:1.18

FROM python:3.13.14-slim AS builder

ARG SERVICE_NAME=service
ARG SERVICE_PATH=services/service
ARG SERVICE_VERSION=0.0.0-dev
ARG BUILD_DATE=1970-01-01T00:00:00Z
ARG GIT_COMMIT=unknown
ARG GIT_TAG=
ARG RUNTIME_REQUIREMENT_GROUPS=runtime-core

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
ENV PIP_NO_CACHE_DIR=1
ENV VIRTUAL_ENV=/opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

WORKDIR /build

COPY pyproject.toml /tmp/media-center-pyproject.toml

RUN python -m venv "$VIRTUAL_ENV"

RUN python - <<'PY'
import os
import tomllib
from pathlib import Path


def split_groups(value: str) -> list[str]:
    return [group.strip() for group in value.split(",") if group.strip()]


pyproject = tomllib.loads(
    Path("/tmp/media-center-pyproject.toml").read_text(encoding="utf-8")
)
runtime_groups = split_groups(os.environ.get("RUNTIME_REQUIREMENT_GROUPS", ""))
service_name = os.environ.get("SERVICE_NAME", "service")
service_group = f"runtime-{service_name}"
optional_dependencies = pyproject["project"].get("optional-dependencies", {})
if service_group in optional_dependencies and service_group not in runtime_groups:
    runtime_groups.append(service_group)
missing_groups = [
    group for group in runtime_groups if group not in optional_dependencies
]
if missing_groups:
    raise SystemExit(
        "Missing runtime dependency groups in pyproject.toml: "
        + ", ".join(missing_groups)
    )
dependencies = []
seen = set()
for group in runtime_groups:
    for dependency in optional_dependencies[group]:
        if dependency not in seen:
            dependencies.append(dependency)
            seen.add(dependency)
if not dependencies:
    raise SystemExit("Runtime dependency groups resolved to an empty requirement set")
Path("/tmp/requirements-runtime.txt").write_text(
    "\n".join(dependencies) + "\n",
    encoding="utf-8",
)
PY

RUN python -m pip install --upgrade pip \
    && python -m pip install -r /tmp/requirements-runtime.txt \
    && python -m pip uninstall -y pip setuptools wheel \
    && find "$VIRTUAL_ENV" -type d -name "__pycache__" -prune -exec rm -rf {} + \
    && find "$VIRTUAL_ENV" -type f \( -name "*.pyc" -o -name "*.pyo" \) -delete \
    && rm -rf /root/.cache/pip \
    && rm -f /tmp/media-center-pyproject.toml /tmp/requirements-runtime.txt

RUN python - <<'PY'
from compileall import compile_dir
from pathlib import Path

site_packages = next(Path("/opt/venv/lib").glob("python*/site-packages"))
for package in ("fastapi", "starlette", "pydantic", "pydantic_settings"):
    package_path = site_packages / package
    if package_path.exists():
        compile_dir(package_path, quiet=1)
PY

RUN mkdir -p /build/app/service /build/app/config /build/app/libs

COPY ${SERVICE_PATH}/ /build/app/service/
COPY services/ /build/services-source/
COPY libs/ /build/app/libs/
COPY docker/entrypoint.sh /build/app/entrypoint.sh

RUN python - <<'PY'
import shutil
from pathlib import Path


source_root = Path("/build/services-source")
target_root = Path("/build/app/service")
for package_dir in sorted(source_root.glob("*/*")):
    if not package_dir.is_dir() or not (package_dir / "__init__.py").is_file():
        continue
    target_dir = target_root / package_dir.name
    if target_dir.exists():
        shutil.rmtree(target_dir)
    shutil.copytree(package_dir, target_dir)
shutil.rmtree(source_root)
PY

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
Path("/build/app/config/build_info.json").write_text(
    json.dumps(build_info, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY

RUN chmod 0755 /build/app/entrypoint.sh \
    && chmod 0444 /build/app/config/build_info.json \
    && python - <<'PY'
import os
from compileall import compile_dir
from pathlib import Path


def service_package_name(service_name: str) -> str:
    return service_name.replace("-", "_")


service_package = service_package_name(os.environ.get("SERVICE_NAME", "service"))
for path in (
    Path("/build/app/service") / service_package,
    Path("/build/app/service") / f"{service_package}_app",
    Path("/build/app/libs/shared"),
):
    if path.exists():
        compile_dir(path, quiet=1)
PY

FROM python:3.13.14-slim AS runtime

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
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
ENV PIP_NO_CACHE_DIR=1
ENV TMPDIR=/tmp
ENV PYTHONPATH=/app/service:/app
ENV VIRTUAL_ENV=/opt/venv
ENV PATH="/opt/venv/bin:${PATH}"
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
    && mkdir -p /app/service /app/config /app/logs \
    && chown -R 1000:1000 /app \
    && chmod 0775 /app/logs \
    && chmod 1777 /tmp \
    && /usr/local/bin/python -m pip uninstall -y pip setuptools wheel \
    && rm -rf /root/.cache/pip /usr/local/lib/python3.13/ensurepip

COPY --from=builder --chown=1000:1000 /opt/venv /opt/venv
COPY --from=builder --chown=1000:1000 /build/app/service/ /app/service/
COPY --from=builder --chown=1000:1000 /build/app/libs/ /app/libs/
COPY --from=builder --chown=1000:1000 /build/app/config/build_info.json /app/config/build_info.json
COPY --from=builder --chown=1000:1000 /build/app/entrypoint.sh /app/entrypoint.sh

RUN chmod 0755 /app/entrypoint.sh \
    && chmod 0444 /app/config/build_info.json

USER 1000:1000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:7700/health', timeout=3).read()"

ENTRYPOINT ["/usr/bin/tini", "--", "/app/entrypoint.sh"]
CMD ["serve"]
