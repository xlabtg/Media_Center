from __future__ import annotations

from fastapi import FastAPI
from voice_to_chain import build_voice_to_chain_settings, create_voice_to_chain_app

from .settings import build_service_config


def build_app() -> FastAPI:
    return create_voice_to_chain_app(
        build_service_config(),
        voice_settings=build_voice_to_chain_settings(),
    )


app = build_app()
