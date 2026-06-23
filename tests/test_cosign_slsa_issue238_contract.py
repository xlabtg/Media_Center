from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_issue_238_ci_signs_published_images_with_cosign_keyless() -> None:
    workflow = read_text(".github/workflows/ci.yml")

    required_markers = [
        "id-token: write",
        "uses: sigstore/cosign-installer@v4.1.2",
        "cosign-release: v3.1.1",
        (
            "cosign sign --yes ghcr.io/${{ github.repository_owner }}/"
            "media-center-${{ matrix.service }}@${{ steps.build-image.outputs.digest }}"
        ),
    ]
    missing = [marker for marker in required_markers if marker not in workflow]

    assert not missing


def test_issue_238_ci_attests_slsa_build_provenance() -> None:
    workflow = read_text(".github/workflows/ci.yml")

    required_markers = [
        "uses: actions/attest-build-provenance@v4.1.0",
        (
            "subject-name: ghcr.io/${{ github.repository_owner }}/"
            "media-center-${{ matrix.service }}"
        ),
        "subject-digest: ${{ steps.build-image.outputs.digest }}",
        "push-to-registry: true",
        "create-storage-record: false",
    ]
    missing = [marker for marker in required_markers if marker not in workflow]

    assert not missing


def test_issue_238_documents_signature_and_provenance_verification() -> None:
    verification = read_text("docs/operations/image-signing-verification.md")

    required_markers = [
        "# Проверка подписей и provenance сервисных образов",
        "#238",
        "cosign verify",
        "--certificate-oidc-issuer https://token.actions.githubusercontent.com",
        "--certificate-identity-regexp",
        "cosign verify-attestation",
        "--type slsaprovenance",
        "gh attestation verify",
        "ghcr.io/xlabtg/media-center-api-gateway@sha256:",
    ]
    missing = [marker for marker in required_markers if marker not in verification]

    assert not missing
