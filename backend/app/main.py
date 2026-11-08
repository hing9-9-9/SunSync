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
    auth,
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
from app.auth import auth_settings, current_user
from app.db import get_session, init_db


def create_app(database_engine: Engine | None = None) -> FastAPI:
    """Build the API, optionally binding every DB access to an isolated engine."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        auth_settings().check()
        init_db(database_engine)
        yield

    application = FastAPI(title="SunSync API", lifespan=lifespan)

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
    # Health and the login routes stay open; every other route needs a logged-in user when the
    # login is switched on (SUNSYNC_AUTH=google).
    protected = [Depends(current_user)]
    application.include_router(health.router, prefix="/api")
    application.include_router(auth.router, prefix="/api")
    for router in (
        scenes.router,
        sunpath.router,
        ui_spaces.router,
        handoffs.router,
        measurements.router,
        shading.router,
        film.router,
        nowcast_ai.router,
        vision_ai.router,
        adaptive_control.router,
    ):
        application.include_router(router, prefix="/api", dependencies=protected)

    if database_engine is not None:

        def isolated_session() -> Iterator[Session]:
            with Session(database_engine) as session:
                yield session

        application.dependency_overrides[get_session] = isolated_session

    return application


app = create_app()
