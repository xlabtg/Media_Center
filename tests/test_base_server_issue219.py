from __future__ import annotations

import json
import platform
from pathlib import Path

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from libs.shared import BaseAppConfig, ServiceTemplateConfig, create_base_app


def test_info_reads_build_info_json(tmp_path: Path) -> None:
    build_info_path = tmp_path / "build_info.json"
    build_info_path.write_text(
        json.dumps(
            {
                "service": "issue-219-build-file",
                "version": "01.04.15",
                "build_date": "2026-06-22T20:30:00Z",
                "git_commit": "abc123def456",
                "git_tag": "01.04.15",
                "python": "Python 3.13.0",
            }
        ),
        encoding="utf-8",
    )
    app = create_base_app(
        BaseAppConfig(
            service=ServiceTemplateConfig(
                service_name="issue-219-config",
                version="0.1.0",
                jwt_secret="test-only-jwt-secret",
            ),
            build_info_path=build_info_path,
        )
    )
    client = TestClient(app)

    info = client.get("/info")

    assert info.status_code == 200
    assert info.json() == {
        "service": "issue-219-build-file",
        "version": "01.04.15",
        "app_port": 7700,
        "port": 7700,
        "build": {
            "service": "issue-219-build-file",
            "version": "01.04.15",
            "build_date": "2026-06-22T20:30:00Z",
            "git_commit": "abc123def456",
            "git_tag": "01.04.15",
            "python": "Python 3.13.0",
            "python_version": platform.python_version(),
            "python_compiler": platform.python_compiler(),
        },
        "build_date": "2026-06-22T20:30:00Z",
        "git_commit": "abc123def456",
        "git_tag": "01.04.15",
        "python": "Python 3.13.0",
        "python_version": platform.python_version(),
        "python_compiler": platform.python_compiler(),
    }


def test_info_uses_env_fallback_when_build_info_json_is_missing(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("SERVICE_NAME", "issue-219-env-service")
    monkeypatch.setenv("SERVICE_VERSION", "01.04.16")
    monkeypatch.setenv("BUILD_DATE", "2026-06-22T21:00:00Z")
    monkeypatch.setenv("GIT_COMMIT", "fed456cba123")
    monkeypatch.setenv("GIT_TAG", "01.04.16")
    app = create_base_app(
        BaseAppConfig(
            service=ServiceTemplateConfig(
                service_name="issue-219-config",
                version="0.1.0",
                jwt_secret="test-only-jwt-secret",
            ),
            build_info_path=tmp_path / "missing-build_info.json",
        )
    )
    client = TestClient(app)

    info = client.get("/info")

    assert info.status_code == 200
    assert info.json()["service"] == "issue-219-env-service"
    assert info.json()["version"] == "01.04.16"
    assert info.json()["build_date"] == "2026-06-22T21:00:00Z"
    assert info.json()["git_commit"] == "fed456cba123"
    assert info.json()["python_version"] == platform.python_version()
