from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_issue_229_service_dockerfile_generates_build_info_from_build_args() -> None:
    dockerfile = read_text("infra/docker/service.Dockerfile")

    required_markers = [
        "ARG BUILD_DATE",
        "ARG GIT_COMMIT",
        "ARG GIT_TAG",
        "ARG SERVICE_VERSION",
        "ARG IMAGE_SOURCE",
        "mkdir -p /app/service /app/config /app/logs /tmp/python-pyc",
        "/app/config/build_info.json",
        '"service":',
        '"version":',
        '"build_date":',
        '"git_commit":',
        '"git_tag":',
        '"python":',
        '"python_version":',
        '"python_compiler":',
    ]
    missing = [marker for marker in required_markers if marker not in dockerfile]

    assert not missing


def test_issue_229_service_dockerfile_sets_oci_labels_from_build_args() -> None:
    dockerfile = read_text("infra/docker/service.Dockerfile")

    required_labels = [
        'org.opencontainers.image.source="${IMAGE_SOURCE}"',
        'org.opencontainers.image.version="${SERVICE_VERSION}"',
        'org.opencontainers.image.revision="${GIT_COMMIT}"',
        'org.opencontainers.image.created="${BUILD_DATE}"',
    ]
    missing = [label for label in required_labels if label not in dockerfile]

    assert not missing


def test_issue_229_ci_passes_build_metadata_to_service_image_build() -> None:
    workflow = read_text(".github/workflows/ci.yml")
    resolver = read_text(".github/scripts/resolve-build-metadata.sh")

    required_workflow_markers = [
        "id: build-metadata",
        "BUILD_DATE=${{ steps.build-metadata.outputs.build_date }}",
        "GIT_COMMIT=${{ github.sha }}",
        "GIT_TAG=${{ steps.build-metadata.outputs.git_tag }}",
        "SERVICE_VERSION=${{ steps.build-metadata.outputs.service_version }}",
        "IMAGE_SOURCE=${{ steps.build-metadata.outputs.image_source }}",
        (
            "org.opencontainers.image.created="
            "${{ steps.build-metadata.outputs.build_date }}"
        ),
        (
            "org.opencontainers.image.version="
            "${{ steps.build-metadata.outputs.service_version }}"
        ),
        "org.opencontainers.image.revision=${{ github.sha }}",
        (
            "org.opencontainers.image.source="
            "${{ steps.build-metadata.outputs.image_source }}"
        ),
    ]
    required_resolver_markers = [
        'emit_output "build_date"',
        'emit_output "git_tag"',
        'emit_output "service_version"',
        'emit_output "image_source"',
        'emit_output "official_semver"',
    ]
    missing = [
        marker for marker in required_workflow_markers if marker not in workflow
    ] + [marker for marker in required_resolver_markers if marker not in resolver]

    assert not missing
