from __future__ import annotations

from fastapi import FastAPI
from hitl_payout_gateway import create_hitl_payout_app

from .settings import (
    build_payment_connector,
    build_service_config,
    build_totp_secrets,
)


def build_app() -> FastAPI:
    return create_hitl_payout_app(
        build_service_config(),
        totp_secrets=build_totp_secrets(),
        payment_connector=build_payment_connector(),
    )


app = build_app()
