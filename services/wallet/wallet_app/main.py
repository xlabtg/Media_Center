from __future__ import annotations

from fastapi import FastAPI
from wallet import create_wallet_app

from .settings import build_service_config


def build_app() -> FastAPI:
    return create_wallet_app(build_service_config())


app = build_app()
