from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_issue_239_ci_runs_trivy_image_scan_as_release_gate() -> None:
    workflow = read_text(".github/workflows/ci.yml")

    required_markers = [
        "Build image for Trivy scan",
        "tags: media-center-${{ matrix.service }}:trivy-scan",
        "load: true",
        "platforms: linux/amd64",
        "Trivy image scan",
        "scan-type: image",
        "image-ref: media-center-${{ matrix.service }}:trivy-scan",
        "scanners: vuln",
        "format: sarif",
        "output: trivy-reports/media-center-${{ matrix.service }}.sarif",
        'exit-code: "1"',
        "ignore-unfixed: true",
        "vuln-type: os,library",
        "severity: HIGH,CRITICAL",
        "limit-severities-for-sarif: true",
    ]
    missing = [marker for marker in required_markers if marker not in workflow]

    assert not missing
    assert workflow.index("Trivy image scan") < workflow.index("Login to GHCR")


def test_issue_239_ci_uploads_trivy_image_scan_report_artifact() -> None:
    workflow = read_text(".github/workflows/ci.yml")

    required_markers = [
        "Prepare Trivy image scan report directory",
        "mkdir -p trivy-reports",
        "Upload Trivy image scan report",
        "Upload Trivy image scan report\n        if: always()",
        "uses: actions/upload-artifact@v7.0.1",
        "name: trivy-image-scan-${{ matrix.service }}",
        "path: trivy-reports/media-center-${{ matrix.service }}.sarif",
        "if-no-files-found: warn",
    ]
    missing = [marker for marker in required_markers if marker not in workflow]

    assert not missing
