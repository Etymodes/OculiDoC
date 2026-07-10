"""FastAPI application factory."""

from fastapi import FastAPI

from oculidoc import __version__
from oculidoc.config import Settings, get_settings


def create_api(settings: Settings | None = None) -> FastAPI:
    active_settings = settings or get_settings()
    api = FastAPI(
        title="OculiDoC Local Administrator API",
        version=__version__,
    )

    @api.get("/health", tags=["system"])
    def health() -> dict[str, str]:
        return {
            "application": active_settings.app_name,
            "version": __version__,
            "status": "ok",
            "environment": active_settings.environment,
            "gaze_source": active_settings.gaze_source,
            "collaborator": active_settings.collaborator_name,
        }

    return api


app = create_api()
