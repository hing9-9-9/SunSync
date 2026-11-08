import logging
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import Engine
from sqlmodel import Session

from app.api import (
    adaptive_control,
    capture_markers,
    film,
    handoffs,
    health,
    measurements,
    nowcast_ai,
    scenes,
    shading,
    sunpath,
    ui_spaces,
    vision_ai,
)
from app.api.auth import router as auth_router
from app.auth import auth_settings, current_user
from app.db import get_session, init_db


def create_app(database_engine: Engine | None = None) -> FastAPI:
    """Build the API, optionally binding every DB access to an isolated engine."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings = auth_settings()
        settings.check()
        logging.getLogger("uvicorn.error").info(settings.describe())
        init_db(database_engine)
        yield

    # Every route added to the app needs a logged-in user when the login is switched on
    # (SUNSYNC_AUTH=google), so a router added later is closed by default. `current_user`
    # lets the health check and the login routes through.
    application = FastAPI(
        title="SunSync API", lifespan=lifespan, dependencies=[Depends(current_user)]
    )

    @application.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Pydantic includes raw input in errors. NaN/Infinity cannot be encoded in
        # JSON responses; returning locations/messages also avoids echoing input.
        details = [{key: error[key] for key in ("loc", "msg", "type")} for error in exc.errors()]
        return JSONResponse(status_code=422, content={"detail": details})

    application.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],  # Vite dev server
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.include_router(health.router, prefix="/api")
    application.include_router(auth_router, prefix="/api")
    application.include_router(scenes.router, prefix="/api")
    application.include_router(sunpath.router, prefix="/api")
    application.include_router(ui_spaces.router, prefix="/api")
    application.include_router(handoffs.router, prefix="/api")
    application.include_router(measurements.router, prefix="/api")
    application.include_router(shading.router, prefix="/api")
    application.include_router(film.router, prefix="/api")
    application.include_router(capture_markers.router, prefix="/api")
    application.include_router(nowcast_ai.router, prefix="/api")
    application.include_router(vision_ai.router, prefix="/api")
    application.include_router(adaptive_control.router, prefix="/api")

    if database_engine is not None:

        def isolated_session() -> Iterator[Session]:
            with Session(database_engine) as session:
                yield session

        application.dependency_overrides[get_session] = isolated_session

    return application


app = create_app()
