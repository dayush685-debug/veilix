"""Aggregates v1 routes under a single prefix."""

from __future__ import annotations

from fastapi import APIRouter

from veilix.api.v1 import admin, engines, health, search

api_router = APIRouter(prefix="/api/v1")

api_router.include_router(health.router)
api_router.include_router(search.router)
api_router.include_router(engines.router)
api_router.include_router(admin.router)
