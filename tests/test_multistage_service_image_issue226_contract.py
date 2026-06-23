from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_issue_226_service_dockerfile_uses_builder_to_runtime_multistage() -> None:
    dockerfile = read_text("infra/docker/service.Dockerfile")

    required_markers = [
        "FROM python:3.13.14-slim AS builder",
        "FROM python:3.13.14-slim AS runtime",
        "ENV VIRTUAL_ENV=/opt/venv",
        'python -m venv "$VIRTUAL_ENV"',
        "python -m pip install -r /tmp/requirements-runtime.txt",
        "python -m pip uninstall -y pip setuptools wheel",
        "COPY ${SERVICE_PATH}/ /build/app/service/",
        "COPY libs/ /build/app/libs/",
        "COPY docker/entrypoint.sh /build/app/entrypoint.sh",
        "COPY --from=builder --chown=1000:1000 /opt/venv /opt/venv",
        ("COPY --from=builder --chown=1000:1000 /build/app/service/ /app/service/"),
        "COPY --from=builder --chown=1000:1000 /build/app/libs/ /app/libs/",
        (
            "COPY --from=builder --chown=1000:1000 "
            "/build/app/config/build_info.json /app/config/build_info.json"
        ),
    ]
    missing = [marker for marker in required_markers if marker not in dockerfile]

    assert not missing


def test_issue_226_runtime_stage_excludes_build_toolchain_and_pip_cache() -> None:
    dockerfile = read_text("infra/docker/service.Dockerfile")
    runtime_stage = dockerfile.split("FROM python:3.13.14-slim AS runtime", maxsplit=1)[
        -1
    ]

    forbidden_runtime_markers = [
        "build-essential",
        " gcc",
        " g++",
        " make",
        " cargo",
        " rustc",
    ]
    present = [
        marker for marker in forbidden_runtime_markers if marker in runtime_stage
    ]

    assert not present
    assert "PIP_NO_CACHE_DIR=1" in dockerfile
    assert "python -m pip uninstall -y pip setuptools wheel" in runtime_stage
    assert "rm -rf /root/.cache/pip" in runtime_stage
