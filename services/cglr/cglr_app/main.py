from __future__ import annotations

from cglr import create_cglr_app
from fastapi import FastAPI

from .settings import build_service_config


def build_app() -> FastAPI:
    return create_cglr_app(build_service_config())


app = build_app()
