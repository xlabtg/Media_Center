from __future__ import annotations

import uvicorn
from fastapi import FastAPI
from hitl_payout_gateway import create_hitl_payout_app

from libs.shared import (
    BaseAppConfig,
    build_runtime_app_host,
    build_runtime_base_app_config,
)

from .settings import (
    build_payment_connector,
    build_service_config,
    build_totp_secrets,
)

runtime_host = build_runtime_app_host()
runtime_config = build_runtime_base_app_config(build_service_config())


def build_app(config: BaseAppConfig | None = None) -> FastAPI:
    return create_hitl_payout_app(
        config or runtime_config,
        totp_secrets=build_totp_secrets(),
        payment_connector=build_payment_connector(),
    )


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
