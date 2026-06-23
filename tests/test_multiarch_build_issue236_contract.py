from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_issue_236_ci_builds_multiarch_service_images_with_gha_cache() -> None:
    workflow = read_text(".github/workflows/ci.yml")

    qemu_marker = "uses: docker/setup-qemu-action@v4.1.0"
    buildx_marker = "uses: docker/setup-buildx-action@v4.1.0"
    required_markers = [
        qemu_marker,
        buildx_marker,
        "uses: docker/build-push-action@v7.2.0",
        "platforms: linux/amd64,linux/arm64",
        "cache-from: type=gha",
        "cache-to: type=gha,mode=max",
    ]
    missing = [marker for marker in required_markers if marker not in workflow]

    assert not missing
    assert workflow.index(qemu_marker) < workflow.index(buildx_marker)
