from __future__ import annotations

from fastapi import FastAPI

from libs.shared import create_service_app

from .settings import build_service_config


def build_app() -> FastAPI:
    return create_service_app(build_service_config())


app = build_app()
