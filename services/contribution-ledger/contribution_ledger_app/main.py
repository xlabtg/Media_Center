from __future__ import annotations

from contribution_ledger import create_contribution_ledger_app
from fastapi import FastAPI

from .settings import build_service_config


def build_app() -> FastAPI:
    return create_contribution_ledger_app(build_service_config())


app = build_app()
