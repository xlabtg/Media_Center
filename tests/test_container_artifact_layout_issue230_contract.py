from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_issue_230_service_dockerfile_uses_canonical_app_layout() -> None:
    dockerfile = read_text("infra/docker/service.Dockerfile")

    required_markers = [
        "WORKDIR /app",
        "ENV PYTHONPATH=/app/service:/app",
        "mkdir -p /app/service /app/config /app/logs",
        ("COPY --from=builder --chown=1000:1000 /build/app/service/ /app/service/"),
        "COPY --from=builder --chown=1000:1000 /build/app/libs/ /app/libs/",
        "/app/config/build_info.json",
    ]
    missing = [marker for marker in required_markers if marker not in dockerfile]

    assert not missing
    assert "./SERVICE.md" not in dockerfile
    assert "./SHARED.md" not in dockerfile
    assert "/tmp/python-pyc" not in dockerfile


def test_issue_230_infra_docs_describe_canonical_app_layout() -> None:
    docs = read_text("infra/README.md")

    required_markers = [
        "/app/service",
        "/app/config",
        "/app/config/build_info.json",
        "PYTHONPATH=/app/service:/app",
        "/app/libs",
    ]
    missing = [marker for marker in required_markers if marker not in docs]

    assert not missing
