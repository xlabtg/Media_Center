from __future__ import annotations

from blockchain_auditor import (
    build_blockchain_auditor_settings,
    create_blockchain_auditor_app,
)
from fastapi import FastAPI

from .settings import build_service_config


def build_app() -> FastAPI:
    return create_blockchain_auditor_app(
        build_service_config(),
        auditor_settings=build_blockchain_auditor_settings(),
    )


app = build_app()
