from __future__ import annotations

from typing import Any

from contribution_ledger import create_contribution_ledger_app
from fastapi import FastAPI

from libs.shared.server import BaseAppConfig

from .settings import build_app_host, build_base_app_config

runtime_host = build_app_host()
runtime_config = build_base_app_config()


def build_app(config: BaseAppConfig | None = None) -> FastAPI:
    return create_contribution_ledger_app(config or runtime_config)


app = build_app(runtime_config)


class _LazyUvicorn:
    def run(self, *args: Any, **kwargs: Any) -> None:
        import uvicorn as uvicorn_module

        uvicorn_module.run(*args, **kwargs)


uvicorn = _LazyUvicorn()


def run() -> None:
    uvicorn.run(
        app,
        host=runtime_host,
        port=runtime_config.app_port,
        log_level=runtime_config.log_level.lower(),
    )


if __name__ == "__main__":
    run()
