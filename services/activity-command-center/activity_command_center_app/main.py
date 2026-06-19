from __future__ import annotations

from activity_command_center import create_activity_command_center_app
from fastapi import FastAPI

from .settings import build_service_config


def build_app() -> FastAPI:
    return create_activity_command_center_app(build_service_config())


app = build_app()
