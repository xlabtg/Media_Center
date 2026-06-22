from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_issue_227_service_dockerfile_hardens_runtime_user_and_init() -> None:
    dockerfile = read_text("infra/docker/service.Dockerfile")

    required_markers = [
        "apt-get install -y --no-install-recommends tini",
        "groupadd --gid 1000 app",
        (
            "useradd --uid 1000 --gid 1000 --home-dir /app "
            "--shell /usr/sbin/nologin --no-create-home app"
        ),
        "mkdir -p /app/service /app/config /app/logs /tmp/python-pyc",
        "chown -R 1000:1000 /app /tmp/python-pyc",
        "chmod 0775 /app/logs",
        "chmod 1777 /tmp",
        "ENV PYTHONPYCACHEPREFIX=/tmp/python-pyc",
        "ENV TMPDIR=/tmp",
        "ENV APP_LOG_DIR=/app/logs",
        "COPY --chown=1000:1000 ${SERVICE_PATH}/ /app/service/",
        "COPY --chown=1000:1000 libs/ /app/libs/",
        'ENTRYPOINT ["/usr/bin/tini", "--", "/app/entrypoint.sh"]',
        "USER 1000:1000",
    ]
    missing = [marker for marker in required_markers if marker not in dockerfile]

    assert not missing
    assert "USER app" not in dockerfile


def test_issue_227_runtime_hardening_flags_are_documented_for_compose_and_k8s() -> None:
    docs = read_text("docs/operations/container-hardening.md")

    required_markers = [
        "read_only: true",
        "tmpfs:",
        "/tmp",
        "/app/logs",
        "security_opt:",
        "no-new-privileges:true",
        "cap_drop:",
        "ALL",
        "runAsUser: 1000",
        "runAsGroup: 1000",
        "runAsNonRoot: true",
        "readOnlyRootFilesystem: true",
        "allowPrivilegeEscalation: false",
        "capabilities:",
        "drop:",
    ]
    missing = [marker for marker in required_markers if marker not in docs]

    assert not missing
