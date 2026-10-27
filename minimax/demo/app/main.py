"""Application factory for the orders service."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .errors import ApiError
from .repo import OrderRepository
from .routes import router


def create_app(repository: OrderRepository | None = None) -> FastAPI:
    app = FastAPI(title="orders-service", version="1.0.0")
    app.state.repo = repository if repository is not None else OrderRepository()
    app.include_router(router)

    @app.exception_handler(ApiError)
    async def _handle_api_error(_request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_payload())

    return app


app = create_app()
