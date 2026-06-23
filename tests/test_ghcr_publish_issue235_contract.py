from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_issue_235_ci_publishes_full_ghcr_tag_set() -> None:
    workflow = read_text(".github/workflows/ci.yml")

    required_markers = [
        "permissions:\n      contents: read\n      packages: write",
        (
            "images: ghcr.io/${{ github.repository_owner }}/"
            "media-center-${{ matrix.service }}"
        ),
        "type=semver,pattern={{version}}",
        "type=semver,pattern={{major}}.{{minor}}",
        r"type=match,pattern=^v?([0-9]+\.[0-9]+\.[0-9]+.*)$,group=1",
        r"type=match,pattern=^v?([0-9]+\.[0-9]+)\.[0-9]+.*$,group=1",
        "type=sha,prefix=,format=long",
        "type=raw,value=latest,enable=${{ startsWith(github.ref, 'refs/tags/') }}",
        "uses: docker/login-action@",
        "password: ${{ secrets.GITHUB_TOKEN }}",
        (
            "push: ${{ github.event_name == 'push' && "
            "(github.ref == 'refs/heads/main' || "
            "startsWith(github.ref, 'refs/tags/')) }}"
        ),
    ]
    missing = [marker for marker in required_markers if marker not in workflow]

    assert not missing


def test_issue_235_image_prefix_decision_is_recorded() -> None:
    adr = read_text("docs/adr/0009-ghcr-image-naming.md")

    required_markers = [
        "# ADR-0009: Имена сервисных образов в GHCR",
        "#235",
        "ghcr.io/${owner}/media-center-${service}",
        "media-center-",
        "nmc-",
    ]
    missing = [marker for marker in required_markers if marker not in adr]

    assert not missing
