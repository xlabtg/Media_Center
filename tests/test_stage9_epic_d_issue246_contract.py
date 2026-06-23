from __future__ import annotations

import time
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Request
from fastapi.testclient import TestClient

from libs.shared import BaseAppConfig, ServiceTemplateConfig, create_base_app
from libs.shared.s2s_auth import (
    AuthMethod,
    K8sS2SAuth,
    K8sTokenReviewResult,
    RSAS2SAuth,
    S2SAuthenticationError,
    S2SConfig,
    SharedSecretS2SAuth,
    detect_auth_method,
)

ROOT = Path(__file__).resolve().parents[1]
S2S_SECRET = "issue-246-s2s-secret"


def read_text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_issue_246_fallback_chain_and_security_controls_cover_req10(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    now = int(time.time())
    token_path = tmp_path / "service-account.token"
    token_path.write_text("projected-token", encoding="utf-8")
    private_key_path, public_key_path = _write_rsa_keypair(tmp_path)

    assert (
        detect_auth_method(
            S2SConfig(
                k8s_token_path=token_path,
                rsa_private_key_path=private_key_path,
                rsa_public_key_path=public_key_path,
                shared_secret=S2S_SECRET,
            )
        )
        is AuthMethod.K8S_SA
    )
    assert (
        detect_auth_method(
            S2SConfig(
                k8s_enabled=False,
                k8s_token_path=token_path,
                rsa_private_key_path=private_key_path,
                rsa_public_key_path=public_key_path,
                shared_secret=S2S_SECRET,
            )
        )
        is AuthMethod.RSA_KEY
    )
    assert (
        detect_auth_method(S2SConfig(k8s_enabled=False, shared_secret=S2S_SECRET))
        is AuthMethod.SHARED_SECRET
    )

    compare_digest_calls: list[tuple[str, str]] = []

    def spy_compare_digest(left: str, right: str) -> bool:
        compare_digest_calls.append((left, right))
        return left == right

    monkeypatch.setattr(
        "libs.shared.s2s_auth.hmac.compare_digest",
        spy_compare_digest,
    )
    shared = SharedSecretS2SAuth(
        S2SConfig(shared_secret=S2S_SECRET),
        clock=lambda: float(now),
    )
    shared_headers = shared.sign_request(
        method="GET",
        path="/admin/log-level",
        service_name="api-gateway",
        timestamp=now,
        nonce="issue-246-shared",
    )

    assert len(shared_headers["X-S2S-Signature"]) == 64
    assert (
        shared.verify_request(
            shared_headers,
            method="GET",
            path="/admin/log-level",
        ).method
        is AuthMethod.SHARED_SECRET
    )
    assert compare_digest_calls
    assert all(
        len(left) == 64 and len(right) == 64 for left, right in compare_digest_calls
    )
    with pytest.raises(S2SAuthenticationError, match="nonce"):
        shared.verify_request(shared_headers, method="GET", path="/admin/log-level")

    rsa_auth = RSAS2SAuth(
        S2SConfig(
            method=AuthMethod.RSA_KEY,
            rsa_private_key_path=private_key_path,
            rsa_public_key_path=public_key_path,
            rsa_issuer="issue-246-tests",
            rsa_audience="nmc-services",
        ),
        clock=lambda: float(now),
    )
    rsa_headers = rsa_auth.sign_request(
        method="PUT",
        path="/admin/log-level",
        service_name="policy-manager",
        timestamp=now,
        nonce="issue-246-rsa",
    )

    assert (
        rsa_auth.verify_request(
            rsa_headers,
            method="PUT",
            path="/admin/log-level",
        ).method
        is AuthMethod.RSA_KEY
    )
    with pytest.raises(S2SAuthenticationError, match="nonce"):
        rsa_auth.verify_request(rsa_headers, method="PUT", path="/admin/log-level")

    k8s_auth = K8sS2SAuth(
        S2SConfig(
            method=AuthMethod.K8S_SA,
            k8s_token_path=token_path,
            k8s_audience="nmc-services",
            k8s_issuer="https://kubernetes.default.svc",
        ),
        clock=lambda: float(now),
        token_validator=lambda token, config: K8sTokenReviewResult(
            service_name="media/api-gateway",
            audience=config.k8s_audience,
            issuer=config.k8s_issuer,
            claims={"sub": "system:serviceaccount:media:api-gateway"},
        ),
    )
    k8s_headers = k8s_auth.sign_request(
        method="GET",
        path="/admin/log-level",
        service_name="untrusted-header-service",
        timestamp=now,
        nonce="issue-246-k8s",
    )

    k8s_identity = k8s_auth.verify_request(
        k8s_headers,
        method="GET",
        path="/admin/log-level",
    )
    assert k8s_identity.method is AuthMethod.K8S_SA
    assert k8s_identity.service_name == "media/api-gateway"
    with pytest.raises(S2SAuthenticationError, match="nonce"):
        k8s_auth.verify_request(k8s_headers, method="GET", path="/admin/log-level")


def test_issue_246_admin_endpoints_require_verified_s2s_identity() -> None:
    app = create_base_app(
        BaseAppConfig(
            service=ServiceTemplateConfig(
                service_name="issue-246-admin",
                jwt_secret="test-only-jwt-secret",
            ),
            s2s=S2SConfig(shared_secret=S2S_SECRET),
        )
    )

    @app.get("/admin/issue-246-identity")
    def admin_identity(request: Request) -> dict[str, str]:
        identity = request.state.s2s_identity
        return {"service": identity.service_name, "method": identity.method.value}

    client = TestClient(app)
    missing_signature = client.get("/admin/issue-246-identity")
    signer = SharedSecretS2SAuth(S2SConfig(shared_secret=S2S_SECRET))
    valid_headers = signer.sign_request(
        method="GET",
        path="/admin/issue-246-identity",
        service_name="api-gateway",
        nonce="issue-246-admin-route",
    )
    valid_signature = client.get(
        "/admin/issue-246-identity",
        headers=valid_headers,
    )

    assert missing_signature.status_code == 401
    assert valid_signature.status_code == 200
    assert valid_signature.json() == {
        "service": "api-gateway",
        "method": "shared_secret",
    }


def test_issue_246_stage9_acceptance_snapshot_documents_epic_d() -> None:
    stage9_snapshot = read_text("docs/STAGE_9_ACCEPTANCE.md")
    s2s_doc = read_text("docs/S2S_AUTH.md")

    required_stage9_markers = [
        "#246",
        "D1",
        "D2",
        "D3",
        "D4",
        "kubernetes_sa",
        "rsa_key",
        "shared_secret",
        "/admin/*",
        "libs/shared/s2s_auth.py",
        "libs/shared/config.py",
        "libs/shared/server.py",
        "docs/S2S_AUTH.md",
        "docs/adr/0010-spiffe-mtls-s2s.md",
        "tests/test_stage9_epic_d_issue246_contract.py",
    ]
    missing_stage9 = [
        marker for marker in required_stage9_markers if marker not in stage9_snapshot
    ]

    assert not missing_stage9
    assert "#246" in s2s_doc
    assert "tests/test_stage9_epic_d_issue246_contract.py" in s2s_doc


def _write_rsa_keypair(tmp_path: Path) -> tuple[Path, Path]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_key_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_key_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    private_key_path = tmp_path / "s2s-private.pem"
    public_key_path = tmp_path / "s2s-public.pem"
    private_key_path.write_bytes(private_key_pem)
    public_key_path.write_bytes(public_key_pem)
    return private_key_path, public_key_path
