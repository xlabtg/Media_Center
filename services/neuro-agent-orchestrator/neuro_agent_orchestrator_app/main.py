from __future__ import annotations

import uvicorn
from fastapi import FastAPI
from neuro_agent_orchestrator import create_neuro_agent_orchestrator_app

from libs.shared import (
    BaseAppConfig,
    build_runtime_app_host,
    build_runtime_base_app_config,
)

from .settings import build_service_config

runtime_host = build_runtime_app_host()
runtime_config = build_runtime_base_app_config(build_service_config())


def build_app(config: BaseAppConfig | None = None) -> FastAPI:
    return create_neuro_agent_orchestrator_app(config or runtime_config)


app = build_app(runtime_config)


def run() -> None:
    uvicorn.run(
        app,
        host=runtime_host,
        port=runtime_config.app_port,
        log_level=runtime_config.log_level.lower(),
    )


if __name__ == "__main__":
    run()
