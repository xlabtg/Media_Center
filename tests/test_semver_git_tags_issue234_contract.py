from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESOLVER = ROOT / ".github/scripts/resolve-build-metadata.sh"


def read_text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_issue_234_ci_uses_git_tags_metadata_action_and_build_args() -> None:
    workflow = read_text(".github/workflows/ci.yml")

    required_markers = [
        "tags:",
        "- '*.*.*'",
        "- 'v*.*.*'",
        "fetch-depth: 0",
        "id: build-metadata",
        "bash .github/scripts/resolve-build-metadata.sh",
        "id: docker-metadata",
        "docker/metadata-action@v6.1.0",
        "type=semver,pattern={{version}}",
        "type=semver,pattern={{major}}.{{minor}}",
        "enable=${{ steps.build-metadata.outputs.official_semver == 'true' }}",
        r"type=match,pattern=^v?([0-9]+\.[0-9]+\.[0-9]+.*)$,group=1",
        "type=sha,prefix=,format=long",
        "${{ steps.docker-metadata.outputs.tags }}",
        "${{ steps.docker-metadata.outputs.labels }}",
        "BUILD_DATE=${{ steps.build-metadata.outputs.build_date }}",
        "GIT_COMMIT=${{ github.sha }}",
        "GIT_TAG=${{ steps.build-metadata.outputs.git_tag }}",
        "SERVICE_VERSION=${{ steps.build-metadata.outputs.service_version }}",
    ]
    missing = [marker for marker in required_markers if marker not in workflow]

    assert not missing


def test_issue_234_resolver_uses_exact_req_55_git_tag(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path)
    _run(["git", "tag", "01.04.15"], cwd=repo)
    commit_sha = _git_stdout(["git", "rev-parse", "HEAD"], cwd=repo)

    metadata = _resolve_metadata(repo, github_sha=commit_sha)

    assert metadata["git_tag"] == "01.04.15"
    assert metadata["service_version"] == "01.04.15"
    assert metadata["official_semver"] == "false"
    assert metadata["image_source"] == "https://github.com/xlabtg/Media_Center"


def test_issue_234_resolver_strips_v_prefix_and_falls_back_to_sha(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path)
    commit_sha = _git_stdout(["git", "rev-parse", "HEAD"], cwd=repo)

    fallback = _resolve_metadata(repo, github_sha=commit_sha)

    assert fallback["git_tag"] == ""
    assert fallback["service_version"] == f"0.0.0-{commit_sha[:12]}"
    assert fallback["official_semver"] == "false"

    _run(["git", "tag", "v1.2.3"], cwd=repo)

    tagged = _resolve_metadata(repo, github_sha=commit_sha)

    assert tagged["git_tag"] == "v1.2.3"
    assert tagged["service_version"] == "1.2.3"
    assert tagged["official_semver"] == "true"


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(["git", "init"], cwd=repo)
    _run(["git", "config", "user.email", "ci@example.invalid"], cwd=repo)
    _run(["git", "config", "user.name", "CI"], cwd=repo)
    (repo / "README.md").write_text("test\n", encoding="utf-8")
    _run(["git", "add", "README.md"], cwd=repo)
    _run(["git", "commit", "-m", "init"], cwd=repo)

    return repo


def _resolve_metadata(repo: Path, *, github_sha: str) -> dict[str, str]:
    output = repo / "github-output.txt"
    env = os.environ.copy()
    env.update(
        {
            "GITHUB_OUTPUT": str(output),
            "GITHUB_REPOSITORY": "xlabtg/Media_Center",
            "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_SHA": github_sha,
        }
    )
    _run(["bash", str(RESOLVER)], cwd=repo, env=env)

    return dict(
        line.split("=", maxsplit=1)
        for line in output.read_text(encoding="utf-8").splitlines()
    )


def _git_stdout(args: list[str], *, cwd: Path) -> str:
    result = subprocess.run(
        args,
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )

    return result.stdout.strip()


def _run(
    args: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
) -> None:
    subprocess.run(args, cwd=cwd, env=env, check=True)
