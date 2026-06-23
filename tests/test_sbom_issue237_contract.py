from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_issue_237_ci_generates_service_image_sboms_with_syft() -> None:
    workflow = read_text(".github/workflows/ci.yml")

    required_markers = [
        "id: build-image",
        "uses: anchore/sbom-action@v0.24.0",
        (
            "image: ghcr.io/${{ github.repository_owner }}/"
            "media-center-${{ matrix.service }}@${{ steps.build-image.outputs.digest }}"
        ),
        "format: spdx-json",
        "artifact-name: media-center-${{ matrix.service }}.spdx.json",
        "output-file: sbom/media-center-${{ matrix.service }}.spdx.json",
        "upload-artifact: true",
        "upload-release-assets: false",
        "registry-username: ${{ github.actor }}",
        "registry-password: ${{ secrets.GITHUB_TOKEN }}",
    ]
    missing = [marker for marker in required_markers if marker not in workflow]

    assert not missing


def test_issue_237_ci_attests_published_image_sboms() -> None:
    workflow = read_text(".github/workflows/ci.yml")

    required_markers = [
        "id-token: write",
        "attestations: write",
        "uses: actions/attest@v4.1.0",
        (
            "subject-name: ghcr.io/${{ github.repository_owner }}/"
            "media-center-${{ matrix.service }}"
        ),
        "subject-digest: ${{ steps.build-image.outputs.digest }}",
        "sbom-path: sbom/media-center-${{ matrix.service }}.spdx.json",
        "push-to-registry: true",
        "create-storage-record: false",
    ]
    missing = [marker for marker in required_markers if marker not in workflow]

    assert not missing
