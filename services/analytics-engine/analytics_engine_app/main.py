from __future__ import annotations

from analytics_engine import create_analytics_engine_app
from fastapi import FastAPI

from .settings import build_service_config


def build_app() -> FastAPI:
    return create_analytics_engine_app(build_service_config())


app = build_app()
