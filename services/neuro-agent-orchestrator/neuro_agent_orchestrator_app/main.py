from __future__ import annotations

from fastapi import FastAPI
from neuro_agent_orchestrator import create_neuro_agent_orchestrator_app

from .settings import build_service_config


def build_app() -> FastAPI:
    return create_neuro_agent_orchestrator_app(build_service_config())


app = build_app()
