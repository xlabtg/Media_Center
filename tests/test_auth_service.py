from __future__ import annotations

from collections.abc import Callable

from libs.shared import (
    AuthTokenService,
    InMemoryRefreshTokenStore,
    RefreshTokenRecord,
    TenantContext,
    TOTPService,
    UnauthorizedError,
    decode_hs256_jwt,
    hash_refresh_token,
)

NOW = 1_800_000_000
JWT_SECRET = "local-dev-secret"
TOTP_SECRET = "JBSWY3DPEHPK3PXP"


def _auth_service(
    *,
    store: InMemoryRefreshTokenStore | None = None,
    clock: Callable[[], float] | None = None,
) -> AuthTokenService:
    return AuthTokenService(
        jwt_secret=JWT_SECRET,
        refresh_store=store or InMemoryRefreshTokenStore(),
        issuer="nmc",
        audience="api-gateway",
        access_ttl_seconds=1_800,
        refresh_ttl_seconds=86_400,
        clock=clock or (lambda: NOW),
    )


def _capture_unauthorized(callback: Callable[[], object]) -> UnauthorizedError:
    try:
        callback()
    except UnauthorizedError as error:
        return error

    raise AssertionError("Ожидался UnauthorizedError")


def test_auth_service_issues_hs256_access_token_with_tenant_and_roles() -> None:
    store = InMemoryRefreshTokenStore()
    service = _auth_service(store=store)

    token_pair = service.issue_token_pair(
        subject="member-1",
        tenant_id="tenant-a",
        roles=("member_full", "council"),
        correlation_id="corr-1",
    )

    assert token_pair.token_type == "Bearer"
    assert token_pair.expires_in == 1_800
    assert token_pair.refresh_expires_in == 86_400
    assert token_pair.access_token.count(".") == 2

    claims = decode_hs256_jwt(
        token_pair.access_token,
        JWT_SECRET,
        expected_issuer="nmc",
        expected_audience="api-gateway",
        now=NOW + 60,
    )
    assert claims["typ"] == "access"
    assert claims["tenant_id"] == "tenant-a"
    assert claims["sub"] == "member-1"
    assert claims["roles"] == ["member_full", "council"]
    assert claims["iat"] == NOW
    assert claims["exp"] == NOW + 1_800

    context = service.verify_access_token(
        token_pair.access_token,
        correlation_id="corr-1",
    )
    assert context == TenantContext(
        tenant_id="tenant-a",
        subject="member-1",
        roles=("member_full", "council"),
        correlation_id="corr-1",
    )

    stored_refresh = store.get(hash_refresh_token(token_pair.refresh_token))
    assert isinstance(stored_refresh, RefreshTokenRecord)
    assert stored_refresh.token_hash != token_pair.refresh_token
    assert stored_refresh.tenant_id == "tenant-a"
    assert stored_refresh.subject == "member-1"
    assert stored_refresh.expires_at == NOW + 86_400


def test_refresh_token_rotation_rejects_replay_and_expiration() -> None:
    store = InMemoryRefreshTokenStore()
    service = _auth_service(store=store)
    token_pair = service.issue_token_pair(
        subject="member-1",
        tenant_id="tenant-a",
        roles=("member_full",),
    )

    refreshed_pair = service.refresh_token_pair(
        token_pair.refresh_token,
        correlation_id="corr-2",
    )

    assert refreshed_pair.access_token != token_pair.access_token
    assert refreshed_pair.refresh_token != token_pair.refresh_token

    replay_error = _capture_unauthorized(
        lambda: service.refresh_token_pair(token_pair.refresh_token)
    )
    assert replay_error.error_code == "unauthorized"
    assert "refresh token" in replay_error.message.lower()

    expired_pair = service.issue_token_pair(
        subject="member-2",
        tenant_id="tenant-a",
        roles=("member_full",),
    )
    expired_service = _auth_service(
        store=store,
        clock=lambda: NOW + 86_401,
    )

    expired_error = _capture_unauthorized(
        lambda: expired_service.refresh_token_pair(expired_pair.refresh_token)
    )
    assert expired_error.error_code == "unauthorized"
    assert "истёк" in expired_error.message


def test_access_token_expiration_is_enforced() -> None:
    service = _auth_service()
    token_pair = service.issue_token_pair(
        subject="member-1",
        tenant_id="tenant-a",
        roles=("member_full",),
    )
    expired_service = _auth_service(clock=lambda: NOW + 1_801)

    error = _capture_unauthorized(
        lambda: expired_service.verify_access_token(token_pair.access_token)
    )

    assert error.error_code == "unauthorized"
    assert "истёк" in error.message


def test_payout_confirmation_requires_valid_totp() -> None:
    context = TenantContext(
        tenant_id="tenant-a",
        subject="council-1",
        roles=("council",),
        correlation_id="corr-3",
    )
    two_factor = TOTPService(issuer="НМЦ", clock=lambda: NOW)
    code = two_factor.generate_totp(TOTP_SECRET)

    confirmation = two_factor.confirm_sensitive_operation(
        context=context,
        secret=TOTP_SECRET,
        code=code,
        operation="payout.confirm",
        resource_id="payout-1",
    )

    assert confirmation.tenant_id == "tenant-a"
    assert confirmation.subject == "council-1"
    assert confirmation.operation == "payout.confirm"
    assert confirmation.resource_id == "payout-1"
    assert confirmation.confirmed_at == NOW
    assert confirmation.method == "totp"
    assert confirmation.correlation_id == "corr-3"

    invalid_error = _capture_unauthorized(
        lambda: two_factor.confirm_sensitive_operation(
            context=context,
            secret=TOTP_SECRET,
            code="000000",
            operation="payout.confirm",
            resource_id="payout-1",
        )
    )
    assert invalid_error.error_code == "unauthorized"
    assert "2FA" in invalid_error.message


def test_totp_rejects_codes_outside_allowed_window() -> None:
    two_factor = TOTPService(clock=lambda: NOW, allowed_drift_steps=0)
    code = two_factor.generate_totp(TOTP_SECRET)
    late_two_factor = TOTPService(
        clock=lambda: NOW + 60,
        allowed_drift_steps=0,
    )

    assert not late_two_factor.verify_totp(TOTP_SECRET, code)
