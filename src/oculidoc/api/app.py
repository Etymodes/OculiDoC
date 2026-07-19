"""FastAPI application factory for local LAN control."""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from oculidoc import __version__
from oculidoc.api.mobile_page import mobile_control_html
from oculidoc.config import Settings, get_settings
from oculidoc.lan_control import (
    LanControlStateStore,
    generate_pairing_token,
)
from oculidoc.modules.registry import DEFAULT_MODULES


class DisplayTextRequest(BaseModel):
    text: str = Field(
        min_length=1,
        max_length=500,
    )


class TaskPreviewRequest(BaseModel):
    module_id: str = Field(
        min_length=1,
        max_length=100,
    )


def create_api(
    settings: Settings | None = None,
    *,
    token: str | None = None,
    state_store: LanControlStateStore | None = None,
) -> FastAPI:
    active_settings = settings or get_settings()
    active_token = (
        token or os.environ.get("OCULIDOC_LAN_TOKEN", "").strip() or generate_pairing_token()
    )
    state_path = Path(
        os.environ.get(
            "OCULIDOC_LAN_STATE_PATH",
            str(active_settings.data_dir / "runtime" / "lan_control_state.json"),
        )
    )
    store = state_store if state_store is not None else LanControlStateStore(state_path)
    modules = {module.module_id: module for module in DEFAULT_MODULES}

    api = FastAPI(
        title="OculiDoC Local Administrator API",
        version=__version__,
    )
    api.state.lan_token = active_token
    api.state.lan_state_store = store

    def authorize(provided_token: str) -> None:
        if not secrets.compare_digest(
            provided_token,
            active_token,
        ):
            raise HTTPException(
                status_code=401,
                detail="Invalid or expired LAN pairing token.",
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

    @api.get(
        "/control",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    def control_page(
        token: Annotated[str, Query(min_length=8)],
    ) -> str:
        authorize(token)
        return mobile_control_html(token)

    @api.get("/api/v1/runtime", tags=["lan-control"])
    def runtime(
        token: Annotated[str, Query(min_length=8)],
    ) -> dict[str, object]:
        authorize(token)
        state = store.load()
        return {
            "application": active_settings.app_name,
            "version": __version__,
            "gaze_source": active_settings.gaze_source,
            "patient_display": state.to_dict(),
            "modules": [
                {
                    "module_id": module.module_id,
                    "title": module.title,
                    "status": module.status,
                }
                for module in DEFAULT_MODULES
            ],
        }

    @api.get(
        "/api/v1/patient-display",
        tags=["lan-control"],
    )
    def patient_display(
        token: Annotated[str, Query(min_length=8)],
    ) -> dict[str, object]:
        authorize(token)
        return store.load().to_dict()

    @api.post(
        "/api/v1/patient-display/text",
        tags=["lan-control"],
    )
    def set_patient_display_text(
        request: DisplayTextRequest,
        token: Annotated[str, Query(min_length=8)],
    ) -> dict[str, object]:
        authorize(token)
        return store.set_display(
            request.text,
            mode="message",
        ).to_dict()

    @api.post(
        "/api/v1/patient-display/idle",
        tags=["lan-control"],
    )
    def reset_patient_display(
        token: Annotated[str, Query(min_length=8)],
    ) -> dict[str, object]:
        authorize(token)
        return store.reset_idle().to_dict()

    @api.post(
        "/api/v1/tasks/preview",
        tags=["lan-control"],
    )
    def preview_task(
        request: TaskPreviewRequest,
        token: Annotated[str, Query(min_length=8)],
    ) -> dict[str, object]:
        authorize(token)
        module = modules.get(request.module_id)

        if module is None:
            raise HTTPException(
                status_code=404,
                detail="Unknown OculiDoC module.",
            )

        return store.set_display(
            f"任务预览：{module.title}\n等待管理员开始",
            mode="preview",
            task_id=module.module_id,
        ).to_dict()

    return api


app = create_api()
