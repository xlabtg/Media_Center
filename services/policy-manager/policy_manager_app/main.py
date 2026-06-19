from __future__ import annotations

from fastapi import FastAPI
from policy_manager import create_policy_manager_app

from .settings import build_service_config


def build_app() -> FastAPI:
    return create_policy_manager_app(build_service_config())


app = build_app()
