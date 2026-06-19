from __future__ import annotations

from fastapi import FastAPI
from web_cabinet import create_web_cabinet_app

from .settings import build_service_config


def build_app() -> FastAPI:
    return create_web_cabinet_app(build_service_config())


app = build_app()
