from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PRODUCT_SERVICES = (
    "activity-command-center",
    "analytics-engine",
    "api-gateway",
    "blockchain-auditor",
    "cglr",
    "contribution-ledger",
    "hitl-payout-gateway",
    "messenger-adapter",
    "neuro-agent-orchestrator",
    "notification-gateway",
    "policy-manager",
    "voice-to-chain",
    "wallet",
    "web-cabinet",
)


def read_text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def read_workflow_bundle() -> str:
    return "\n".join(
        [
            read_text(".github/workflows/ci.yml"),
            read_text(".github/workflows/build-service.yml"),
        ]
    )


def test_issue_241_release_pipeline_keeps_full_supply_chain_contract() -> None:
    workflow = read_workflow_bundle()

    required_markers = [
        "uses: ./.github/workflows/build-service.yml",
        "workflow_call:",
        "fetch-depth: 0",
        "bash .github/scripts/resolve-build-metadata.sh",
        "docker/metadata-action@v6.1.0",
        "type=semver,pattern={{version}}",
        "type=semver,pattern={{major}}.{{minor}}",
        "type=sha,prefix=,format=long",
        "type=raw,value=latest",
        "BUILD_DATE=${{ steps.build-metadata.outputs.build_date }}",
        "GIT_COMMIT=${{ github.sha }}",
        "GIT_TAG=${{ steps.build-metadata.outputs.git_tag }}",
        "SERVICE_VERSION=${{ steps.build-metadata.outputs.service_version }}",
        "docker/setup-qemu-action@v4.1.0",
        "docker/setup-buildx-action@v4.1.0",
        "platforms: linux/amd64,linux/arm64",
        "cache-from: type=gha",
        "cache-to: type=gha,mode=max",
        "docker/login-action@v4.2.0",
        "password: ${{ secrets.GITHUB_TOKEN }}",
        "Trivy image scan",
        "scan-type: image",
        "severity: HIGH,CRITICAL",
        "anchore/sbom-action@v0.24.0",
        "format: spdx-json",
        "upload-artifact: true",
        "sigstore/cosign-installer@v4.1.2",
        "cosign sign --yes",
        "actions/attest-build-provenance@v4.1.0",
        "actions/attest@v4.1.0",
        "sbom-path: sbom/media-center-${{ inputs.service }}.spdx.json",
        "id-token: write",
        "attestations: write",
    ]
    missing = [marker for marker in required_markers if marker not in workflow]

    assert not missing

    reusable = read_text(".github/workflows/build-service.yml")
    assert reusable.index("Trivy image scan") < reusable.index("Login to GHCR")
    assert reusable.index("Login to GHCR") < reusable.index(
        "      - name: Build image\n        id: build-image"
    )
    assert reusable.index("      - name: Build image\n        id: build-image") < (
        reusable.index("Sign image digest")
    )
    assert reusable.index("Sign image digest") < reusable.index(
        "Attest SLSA provenance"
    )
    assert reusable.index("Generate SBOM") < reusable.index("Attest SBOM")


def test_issue_241_image_matrix_covers_every_product_service() -> None:
    workflow = read_text(".github/workflows/ci.yml")
    service_dirs = {
        path.name
        for path in (ROOT / "services").iterdir()
        if path.is_dir() and path.name != "service-template"
    }

    assert service_dirs == set(PRODUCT_SERVICES)
    for service in PRODUCT_SERVICES:
        assert f"- {service}" in workflow
        assert f"media-center-{service}" not in workflow
    assert "- service-template" not in workflow


def test_issue_241_stage9_acceptance_snapshot_documents_epic_c() -> None:
    snapshot = read_text("docs/STAGE_9_ACCEPTANCE.md")

    required_markers = [
        "#241",
        "C1",
        "C2",
        "C3",
        "C4",
        "C5",
        "C6",
        "C7",
        ".github/workflows/ci.yml",
        ".github/workflows/build-service.yml",
        ".github/scripts/resolve-build-metadata.sh",
        "tests/test_stage9_epic_c_issue241_contract.py",
        "semver",
        "linux/amd64,linux/arm64",
        "SBOM",
        "cosign",
        "SLSA",
        "Trivy",
        "workflow_call",
    ]
    missing = [marker for marker in required_markers if marker not in snapshot]

    assert not missing
