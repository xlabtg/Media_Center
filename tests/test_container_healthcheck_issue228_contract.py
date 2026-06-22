from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_issue_228_service_dockerfile_declares_python_healthcheck() -> None:
    dockerfile = read_text("infra/docker/service.Dockerfile")

    required_markers = [
        "HEALTHCHECK",
        "--interval=30s",
        "--timeout=5s",
        "--start-period=10s",
        "--retries=3",
        "CMD python -c",
        "urllib.request",
        "http://localhost:7700/health",
    ]
    missing = [marker for marker in required_markers if marker not in dockerfile]

    assert not missing
    assert "curl" not in dockerfile.lower()
