from __future__ import annotations

import time
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from libs.shared.s2s_auth import (
    AuthMethod,
    K8sS2SAuth,
    K8sTokenReviewResult,
    RSAS2SAuth,
    S2SAuthenticationError,
    S2SConfig,
    SharedSecretS2SAuth,
)

ROOT = Path(__file__).resolve().parents[1]


def read_text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_issue_245_s2s_methods_reject_replay_and_use_timing_safe_hmac(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    now = int(time.time())
    compare_digest_calls: list[tuple[str, str]] = []

    def spy_compare_digest(left: str, right: str) -> bool:
        compare_digest_calls.append((left, right))
        return left == right

    monkeypatch.setattr(
        "libs.shared.s2s_auth.hmac.compare_digest",
        spy_compare_digest,
    )

    shared = SharedSecretS2SAuth(
        S2SConfig(shared_secret="issue-245-shared-secret"),
        clock=lambda: float(now),
    )
    shared_headers = shared.sign_request(
        method="POST",
        path="/admin/log-level",
        service_name="api-gateway",
        timestamp=now,
        nonce="issue-245-secret",
    )

    assert (
        shared.verify_request(
            shared_headers,
            method="POST",
            path="/admin/log-level",
        ).method
        is AuthMethod.SHARED_SECRET
    )
    with pytest.raises(S2SAuthenticationError, match="nonce"):
        shared.verify_request(shared_headers, method="POST", path="/admin/log-level")
    assert compare_digest_calls
    assert all(
        len(left) == 64 and len(right) == 64 for left, right in compare_digest_calls
    )

    private_key_path, public_key_path = _write_rsa_keypair(tmp_path)
    rsa_auth = RSAS2SAuth(
        S2SConfig(
            method=AuthMethod.RSA_KEY,
            rsa_private_key_path=private_key_path,
            rsa_public_key_path=public_key_path,
            rsa_issuer="issue-245-tests",
            rsa_audience="nmc-services",
        ),
        clock=lambda: float(now),
    )
    rsa_headers = rsa_auth.sign_request(
        method="GET",
        path="/admin/log-level",
        service_name="policy-manager",
        timestamp=now,
        nonce="issue-245-rsa",
    )

    assert (
        rsa_auth.verify_request(
            rsa_headers,
            method="GET",
            path="/admin/log-level",
        ).method
        is AuthMethod.RSA_KEY
    )
    with pytest.raises(S2SAuthenticationError, match="nonce"):
        rsa_auth.verify_request(rsa_headers, method="GET", path="/admin/log-level")

    token_path = tmp_path / "service-account.token"
    token_path.write_text("projected-token", encoding="utf-8")

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
        method="PUT",
        path="/admin/log-level",
        service_name="ignored-header-service",
        timestamp=now,
        nonce="issue-245-k8s",
    )

    assert (
        k8s_auth.verify_request(
            k8s_headers,
            method="PUT",
            path="/admin/log-level",
        ).method
        is AuthMethod.K8S_SA
    )
    with pytest.raises(S2SAuthenticationError, match="nonce"):
        k8s_auth.verify_request(k8s_headers, method="PUT", path="/admin/log-level")


def test_issue_245_s2s_threat_model_and_spiffe_adr_are_documented() -> None:
    s2s_doc = read_text("docs/S2S_AUTH.md")
    security_doc = read_text("docs/SECURITY.md")
    adr = read_text("docs/adr/0010-spiffe-mtls-s2s.md")
    adr_index = read_text("docs/adr/README.md")

    required_s2s_markers = [
        "# Service-to-service авторизация",
        "#245",
        "kubernetes_sa",
        "rsa_key",
        "shared_secret",
        "projected ServiceAccount token",
        "TokenReview",
        "OIDC issuer",
        "timestamp + nonce",
        "hmac.compare_digest",
        "STRIDE",
        "Replay",
        "SPIFFE",
        "SPIRE",
        "mTLS",
    ]
    missing_s2s = [marker for marker in required_s2s_markers if marker not in s2s_doc]

    assert not missing_s2s
    assert "docs/S2S_AUTH.md" in security_doc
    assert "DF-07. Service-to-service авторизация" in security_doc
    assert "# ADR-0010: Целевой переход S2S на SPIFFE/SPIRE и mTLS" in adr
    assert "**Статус:** Accepted" in adr
    assert "JWT-SVID" in adr
    assert "X.509-SVID" in adr
    assert "mTLS" in adr
    assert "[ADR-0010](0010-spiffe-mtls-s2s.md)" in adr_index


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
