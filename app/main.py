"""FastAPI application factory. Run with: uvicorn app.main:app --reload"""

import logging

from fastapi import FastAPI

from app.api.files import router as files_router
from app.config import Settings
from app.database import make_session_factory


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    settings.upload_dir.mkdir(parents=True, exist_ok=True)

    app = FastAPI(
        title="Geospatial File Measurement API",
        version="1.0.0",
        description="Upload a Shapefile (.zip) or KML/KMZ file and get area and length for every feature.",
    )
    app.state.settings = settings
    app.state.session_factory = make_session_factory(settings.database_url)
    app.include_router(files_router)

    @app.get("/health", tags=["health"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app = create_app()
