from __future__ import annotations

from fastapi import FastAPI

from libs.shared import BaseAppConfig, create_base_app

from .settings import build_base_app_config

runtime_config = build_base_app_config()


def build_app(config: BaseAppConfig | None = None) -> FastAPI:
    return create_base_app(config or runtime_config)


app = build_app(runtime_config)
