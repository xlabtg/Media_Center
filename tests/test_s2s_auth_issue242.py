from __future__ import annotations

import time
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from libs.shared.s2s_auth import (
    AuthMethod,
    K8sS2SAuth,
    RSAS2SAuth,
    S2SAuthenticationError,
    S2SConfig,
    SharedSecretS2SAuth,
    detect_auth_method,
)


def test_detect_auth_method_prefers_k8s_then_rsa_then_shared(
    tmp_path: Path,
) -> None:
    k8s_token_path = tmp_path / "service-account.token"
    k8s_token_path.write_text("projected-token", encoding="utf-8")
    private_key_path, public_key_path, _ = _write_rsa_keypair(tmp_path)

    assert (
        detect_auth_method(
            S2SConfig(
                k8s_token_path=k8s_token_path,
                rsa_private_key_path=private_key_path,
                rsa_public_key_path=public_key_path,
                shared_secret="test-secret",
            )
        )
        is AuthMethod.K8S_SA
    )
    assert (
        detect_auth_method(
            S2SConfig(
                k8s_enabled=False,
                k8s_token_path=k8s_token_path,
                rsa_private_key_path=private_key_path,
                rsa_public_key_path=public_key_path,
                shared_secret="test-secret",
            )
        )
        is AuthMethod.RSA_KEY
    )
    assert (
        detect_auth_method(
            S2SConfig(
                k8s_enabled=False,
                k8s_token_path=tmp_path / "missing.token",
                shared_secret="test-secret",
            )
        )
        is AuthMethod.SHARED_SECRET
    )


def test_shared_secret_uses_full_hmac_compare_digest_and_rejects_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compared_values: list[tuple[str, str]] = []

    def spy_compare_digest(left: str, right: str) -> bool:
        compared_values.append((left, right))
        return left == right

    monkeypatch.setattr(
        "libs.shared.s2s_auth.hmac.compare_digest",
        spy_compare_digest,
    )
    auth = SharedSecretS2SAuth(
        S2SConfig(shared_secret="test-only-shared-secret"),
        clock=lambda: 1_900_000_000.0,
    )
    headers = auth.sign_request(
        method="POST",
        path="/internal/reindex",
        service_name="analytics-engine",
        timestamp=1_900_000_000,
        nonce="shared-nonce",
    )

    assert len(headers["X-S2S-Signature"]) == 64
    identity = auth.verify_request(
        headers,
        method="POST",
        path="/internal/reindex",
    )
    assert identity.service_name == "analytics-engine"
    assert identity.method is AuthMethod.SHARED_SECRET
    assert compared_values
    assert all(len(left) == 64 and len(right) == 64 for left, right in compared_values)

    with pytest.raises(S2SAuthenticationError, match="nonce"):
        auth.verify_request(headers, method="POST", path="/internal/reindex")


def test_rsa_s2s_auth_signs_rs256_jwt_and_rejects_replay(
    tmp_path: Path,
) -> None:
    now = int(time.time())
    private_key_path, public_key_path, _ = _write_rsa_keypair(tmp_path)
    auth = RSAS2SAuth(
        S2SConfig(
            method=AuthMethod.RSA_KEY,
            rsa_private_key_path=private_key_path,
            rsa_public_key_path=public_key_path,
            rsa_issuer="nmc-tests",
            rsa_audience="nmc-services",
        ),
        clock=lambda: float(now),
    )

    headers = auth.sign_request(
        method="POST",
        path="/internal/reindex",
        service_name="analytics-engine",
        timestamp=now,
        nonce="rsa-nonce",
    )

    assert headers["X-S2S-Method"] == AuthMethod.RSA_KEY.value
    token = headers["Authorization"].removeprefix("Bearer ")
    assert jwt.get_unverified_header(token)["alg"] == "RS256"

    identity = auth.verify_request(
        headers,
        method="POST",
        path="/internal/reindex",
    )
    assert identity.service_name == "analytics-engine"
    assert identity.method is AuthMethod.RSA_KEY

    with pytest.raises(S2SAuthenticationError, match="nonce"):
        auth.verify_request(headers, method="POST", path="/internal/reindex")

    wrong_path_headers = auth.sign_request(
        method="POST",
        path="/internal/reindex",
        service_name="analytics-engine",
        timestamp=now,
        nonce="rsa-wrong-path",
    )
    with pytest.raises(S2SAuthenticationError, match="path"):
        auth.verify_request(
            wrong_path_headers,
            method="POST",
            path="/internal/other",
        )


def test_k8s_s2s_auth_validates_projected_token_audience_issuer_and_replay(
    tmp_path: Path,
) -> None:
    now = int(time.time())
    _private_key_path, public_key_path, private_key_pem = _write_rsa_keypair(tmp_path)
    token = jwt.encode(
        {
            "iss": "https://kubernetes.default.svc",
            "aud": "nmc-services",
            "sub": "system:serviceaccount:media:api-gateway",
            "iat": now,
            "exp": now + 60,
        },
        private_key_pem,
        algorithm="RS256",
    )
    token_path = tmp_path / "service-account.token"
    token_path.write_text(token, encoding="utf-8")
    auth = K8sS2SAuth(
        S2SConfig(
            method=AuthMethod.K8S_SA,
            k8s_token_path=token_path,
            k8s_oidc_public_key_path=public_key_path,
            k8s_audience="nmc-services",
            k8s_issuer="https://kubernetes.default.svc",
        ),
        clock=lambda: float(now),
    )

    headers = auth.sign_request(
        method="GET",
        path="/admin/log-level",
        service_name="ignored-header-service",
        timestamp=now,
        nonce="k8s-nonce",
    )

    assert headers["X-S2S-Method"] == AuthMethod.K8S_SA.value
    identity = auth.verify_request(headers, method="GET", path="/admin/log-level")
    assert identity.service_name == "media/api-gateway"
    assert identity.method is AuthMethod.K8S_SA

    with pytest.raises(S2SAuthenticationError, match="nonce"):
        auth.verify_request(headers, method="GET", path="/admin/log-level")

    wrong_issuer_auth = K8sS2SAuth(
        S2SConfig(
            method=AuthMethod.K8S_SA,
            k8s_token_path=token_path,
            k8s_oidc_public_key_path=public_key_path,
            k8s_audience="nmc-services",
            k8s_issuer="https://unexpected-issuer",
        ),
        clock=lambda: float(now),
    )
    with pytest.raises(S2SAuthenticationError, match="issuer"):
        wrong_issuer_auth.verify_request(
            headers | {"X-S2S-Nonce": "k8s-wrong-issuer"},
            method="GET",
            path="/admin/log-level",
        )


def _write_rsa_keypair(tmp_path: Path) -> tuple[Path, Path, bytes]:
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
    return private_key_path, public_key_path, private_key_pem
