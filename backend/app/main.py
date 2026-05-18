from fastapi import FastAPI

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.logging import configure_logging
from app.db import models as _models  # noqa: F401
from fastapi.middleware.cors import CORSMiddleware

def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    configure_logging()

    application = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="Local technical analysis API for personal trading research.",
    )
    application.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:5174",
        "http://localhost:5174",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    )
    application.include_router(api_router, prefix=settings.api_v1_prefix)
    return application


app = create_app()
