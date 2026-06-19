from __future__ import annotations

from fastapi import FastAPI
from notification_gateway import create_notification_gateway_app

from .settings import build_service_config


def build_app() -> FastAPI:
    return create_notification_gateway_app(build_service_config())


app = build_app()
