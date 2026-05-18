from fastapi import APIRouter

from app.api.v1.endpoints import (
    analysis,
    candles,
    health,
    indicators,
    instruments,
    sync,
    workspace,
)


api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(instruments.router, prefix="/instruments", tags=["instruments"])
api_router.include_router(candles.router, prefix="/candles", tags=["candles"])
api_router.include_router(indicators.router, prefix="/indicators", tags=["indicators"])
api_router.include_router(analysis.router, prefix="/analysis", tags=["analysis"])
api_router.include_router(sync.router, prefix="/sync", tags=["sync"])
api_router.include_router(workspace.router, prefix="/workspace", tags=["workspace"])
