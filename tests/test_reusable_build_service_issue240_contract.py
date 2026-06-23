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


def test_issue_240_ci_calls_reusable_service_build_workflow() -> None:
    workflow = read_text(".github/workflows/ci.yml")

    required_markers = [
        "images:",
        "uses: ./.github/workflows/build-service.yml",
        "service: ${{ matrix.service }}",
        "secrets: inherit",
        "packages: write",
        "id-token: write",
        "attestations: write",
    ]
    missing = [marker for marker in required_markers if marker not in workflow]

    assert not missing
    assert "docker/build-push-action@v7.2.0" not in workflow
    assert "anchore/sbom-action@v0.24.0" not in workflow
    assert "sigstore/cosign-installer@v4.1.2" not in workflow


def test_issue_240_service_build_workflow_is_reusable() -> None:
    reusable = read_text(".github/workflows/build-service.yml")

    required_markers = [
        "workflow_call:",
        "inputs:",
        "service:",
        "required: true",
        "type: string",
        "Build service image (${{ inputs.service }})",
        "bash .github/scripts/resolve-build-metadata.sh",
        "docker/metadata-action@v6.1.0",
        "docker/build-push-action@v7.2.0",
        "aquasecurity/trivy-action@v0.36.0",
        "sigstore/cosign-installer@v4.1.2",
        "actions/attest-build-provenance@v4.1.0",
        "anchore/sbom-action@v0.24.0",
        "actions/attest@v4.1.0",
    ]
    missing = [marker for marker in required_markers if marker not in reusable]

    assert not missing
    assert "matrix.service" not in reusable


def test_issue_240_image_matrix_covers_all_product_services() -> None:
    workflow = read_text(".github/workflows/ci.yml")
    service_dirs = {
        path.name
        for path in (ROOT / "services").iterdir()
        if path.is_dir() and path.name != "service-template"
    }

    assert service_dirs == set(PRODUCT_SERVICES)
    for service in PRODUCT_SERVICES:
        assert f"- {service}" in workflow
