import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def assert_markers(path: str, markers: list[str]) -> None:
    content = read_text(path)
    missing = [marker for marker in markers if marker not in content]

    assert not missing


def test_local_compose_declares_required_development_services() -> None:
    compose = read_text("infra/local/docker-compose.yml")

    required_markers = [
        "postgres:",
        "image: postgres:17",
        "redis:",
        "image: redis:7.4",
        "rabbitmq:",
        "image: rabbitmq:4.1-management",
        "chroma:",
        "image: chromadb/chroma:1.5.9",
        "minio:",
        "image: minio/minio:RELEASE.2025-09-07T16-13-09Z",
        "postgres-data:",
        "redis-data:",
        "rabbitmq-data:",
        "chroma-data:",
        "minio-data:",
    ]
    missing = [marker for marker in required_markers if marker not in compose]

    assert not missing
    assert ":latest" not in compose


def test_local_env_template_contains_safe_dev_defaults() -> None:
    assert_markers(
        "infra/local/.env.local.example",
        [
            "POSTGRES_DB=nmc",
            "POSTGRES_USER=nmc",
            "POSTGRES_PASSWORD=",
            "REDIS_PORT=6379",
            "REDIS_URL=redis://localhost:6379/0",
            "RABBITMQ_DEFAULT_USER=nmc",
            "RABBITMQ_DEFAULT_PASS=",
            "RABBITMQ_URL=amqp://nmc:nmc_dev_password@localhost:5672/",
            "CHROMA_PORT=8001",
            "MINIO_ROOT_USER=nmc_minio",
            "MINIO_ROOT_PASSWORD=",
            "MINIO_BUCKET=nmc-dev",
        ],
    )
    assert "CHANGE_ME" not in read_text("infra/local/.env.local.example")


def test_makefile_exposes_local_development_workflow() -> None:
    makefile = read_text("Makefile")

    for target in ("up", "down", "test", "migrate"):
        assert re.search(rf"^{target}:", makefile, re.MULTILINE)

    required_markers = [
        "infra/local/docker-compose.yml",
        "infra/local/.env.local.example",
        "docker compose",
        "experiments/validate_issue10_local_env.sh",
        "infra/local/scripts/migrate.sh",
    ]
    missing = [marker for marker in required_markers if marker not in makefile]

    assert not missing


def test_local_migrations_seeds_and_fixtures_are_available() -> None:
    assert_markers(
        "infra/local/postgres/migrations/001_dev_schema.sql",
        [
            "CREATE SCHEMA IF NOT EXISTS nmc_dev;",
            "CREATE TABLE IF NOT EXISTS nmc_dev.tenants",
            "CREATE TABLE IF NOT EXISTS nmc_dev.participants",
            "CREATE TABLE IF NOT EXISTS nmc_dev.contribution_events",
        ],
    )
    assert_markers(
        "infra/local/postgres/seeds/001_dev_seed.sql",
        [
            "INSERT INTO nmc_dev.tenants",
            "INSERT INTO nmc_dev.participants",
            "INSERT INTO nmc_dev.contribution_events",
        ],
    )
    assert_markers(
        "infra/local/fixtures/dev-fixtures.json",
        [
            '"tenant_id": "00000000-0000-4000-8000-000000000001"',
            '"participants"',
            '"contribution_events"',
        ],
    )
    assert_markers(
        "infra/local/scripts/migrate.sh",
        [
            "docker compose",
            "/migrations/001_dev_schema.sql",
            "infra/local/scripts/seed.sh",
        ],
    )
    assert_markers(
        "infra/local/scripts/seed.sh",
        [
            "docker compose",
            "/seeds/001_dev_seed.sql",
        ],
    )


def test_local_run_documentation_covers_acceptance_workflow() -> None:
    docs = "\n".join(
        [
            read_text("README.md"),
            read_text("infra/README.md"),
            read_text("infra/local/README.md"),
        ],
    )

    required_markers = [
        "make up",
        "make migrate",
        "make test",
        "make down",
        "PostgreSQL",
        "Redis",
        "RabbitMQ",
        "ChromaDB",
        "MinIO",
        "infra/local/.env.local.example",
    ]
    missing = [marker for marker in required_markers if marker not in docs]

    assert not missing
