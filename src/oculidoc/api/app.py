"""FastAPI application factory for local LAN control."""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from oculidoc import __version__
from oculidoc.api.mobile_page import mobile_control_html
from oculidoc.config import Settings, get_settings
from oculidoc.lan_commands import (
    REMOTE_GAZE_MODULE_IDS,
    LanCommandStore,
    LanCommandType,
)
from oculidoc.lan_control import (
    LanControlStateStore,
    LanControlTransitionError,
    PatientDisplayMode,
    generate_pairing_token,
)
from oculidoc.modules.registry import DEFAULT_MODULES
from oculidoc.task_configs import (
    TaskConfigConflict,
    TaskConfigStore,
)


class DisplayTextRequest(BaseModel):
    text: str = Field(min_length=1, max_length=500)


class TaskPreviewRequest(BaseModel):
    module_id: str = Field(min_length=1, max_length=100)


class DesktopCommandRequest(BaseModel):
    command_type: LanCommandType
    module_id: str | None = Field(default=None, min_length=1, max_length=100)
    config_revision: int | None = Field(default=None, ge=0)


class TaskConfigUpdateRequest(BaseModel):
    revision: int = Field(ge=0)
    config: dict[str, object]


def create_api(
    settings: Settings | None = None,
    *,
    token: str | None = None,
    state_store: LanControlStateStore | None = None,
    command_store: LanCommandStore | None = None,
    task_config_store: TaskConfigStore | None = None,
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
    command_directory = Path(
        os.environ.get(
            "OCULIDOC_LAN_COMMAND_DIR",
            str(active_settings.data_dir / "runtime" / "lan_commands"),
        )
    )
    store = state_store if state_store is not None else LanControlStateStore(state_path)
    commands = command_store if command_store is not None else LanCommandStore(command_directory)
    task_configs = task_config_store or TaskConfigStore(
        active_settings.data_dir / "runtime" / "task_configs.json"
    )
    modules = {module.module_id: module for module in DEFAULT_MODULES}

    api = FastAPI(
        title="OculiDoC Local Administrator API",
        version=__version__,
    )
    api.state.lan_token = active_token
    api.state.lan_state_store = store
    api.state.lan_command_store = commands
    api.state.task_config_store = task_configs

    def authorize(provided_token: str) -> None:
        if not secrets.compare_digest(provided_token, active_token):
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

    @api.get("/control", response_class=HTMLResponse, include_in_schema=False)
    def control_page(token: Annotated[str, Query(min_length=8)]) -> str:
        authorize(token)
        return mobile_control_html(token)

    @api.get("/api/v1/runtime", tags=["lan-control"])
    def runtime(token: Annotated[str, Query(min_length=8)]) -> dict[str, object]:
        authorize(token)
        state = store.load()
        return {
            "application": active_settings.app_name,
            "version": __version__,
            "gaze_source": active_settings.gaze_source,
            "patient_display": state.to_dict(),
            "commands": [command.to_dict() for command in commands.list_commands(limit=10)],
            "modules": [
                {
                    "module_id": module.module_id,
                    "title": module.title,
                    "status": module.status,
                    "remote_start_available": (module.module_id in REMOTE_GAZE_MODULE_IDS),
                    "config_revision": (
                        task_configs.load(module.module_id).revision
                        if module.module_id in REMOTE_GAZE_MODULE_IDS
                        else None
                    ),
                }
                for module in DEFAULT_MODULES
            ],
        }

    @api.get("/api/v1/patient-display", tags=["lan-control"])
    def patient_display(
        token: Annotated[str, Query(min_length=8)],
    ) -> dict[str, object]:
        authorize(token)
        return store.load().to_dict()

    @api.post("/api/v1/patient-display/text", tags=["lan-control"])
    def set_patient_display_text(
        request: DisplayTextRequest,
        token: Annotated[str, Query(min_length=8)],
    ) -> dict[str, object]:
        authorize(token)

        try:
            return store.set_display(
                request.text,
                mode=PatientDisplayMode.PREVIEW,
            ).to_dict()
        except LanControlTransitionError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @api.post("/api/v1/patient-display/idle", tags=["lan-control"])
    def reset_patient_display(
        token: Annotated[str, Query(min_length=8)],
    ) -> dict[str, object]:
        authorize(token)

        if store.load().mode in {
            PatientDisplayMode.READY,
            PatientDisplayMode.RUNNING,
            PatientDisplayMode.PAUSED,
        }:
            raise HTTPException(
                status_code=409,
                detail="Stop the active task before returning the patient display to idle.",
            )

        return store.reset_idle().to_dict()

    @api.post("/api/v1/tasks/preview", tags=["lan-control"])
    def preview_task(
        request: TaskPreviewRequest,
        token: Annotated[str, Query(min_length=8)],
    ) -> dict[str, object]:
        authorize(token)
        module = modules.get(request.module_id)

        if module is None:
            raise HTTPException(status_code=404, detail="Unknown OculiDoC module.")

        try:
            return store.set_display(
                f"任务预览：{module.title}\n等待管理员开始",
                mode=PatientDisplayMode.PREVIEW,
                task_id=module.module_id,
            ).to_dict()
        except LanControlTransitionError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @api.get("/api/v1/commands", tags=["desktop-commands"])
    def list_commands(
        token: Annotated[str, Query(min_length=8)],
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
    ) -> list[dict[str, object]]:
        authorize(token)
        return [command.to_dict() for command in commands.list_commands(limit=limit)]

    @api.get("/api/v1/task-configs/{module_id}", tags=["task-configs"])
    def get_task_config(
        module_id: str,
        token: Annotated[str, Query(min_length=8)],
    ) -> dict[str, object]:
        authorize(token)

        try:
            return task_configs.load(module_id).to_dict()
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @api.put(
        "/api/v1/task-configs/{module_id}",
        tags=["task-configs"],
        response_model=None,
    )
    def update_task_config(
        module_id: str,
        request: TaskConfigUpdateRequest,
        token: Annotated[str, Query(min_length=8)],
    ) -> dict[str, object] | JSONResponse:
        authorize(token)

        try:
            return task_configs.save(
                module_id,
                request.config,
                expected_revision=request.revision,
            ).to_dict()
        except TaskConfigConflict as error:
            return JSONResponse(status_code=409, content=error.current.to_dict())
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except (TypeError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @api.post("/api/v1/commands", tags=["desktop-commands"])
    def submit_command(
        request: DesktopCommandRequest,
        token: Annotated[str, Query(min_length=8)],
    ) -> dict[str, object]:
        authorize(token)
        module_id = request.module_id.strip() if request.module_id else None

        if (
            request.command_type is LanCommandType.START_TASK
            and module_id not in REMOTE_GAZE_MODULE_IDS
        ):
            raise HTTPException(
                status_code=422,
                detail="This task is not available for remote start.",
            )

        if request.command_type is LanCommandType.START_TASK and request.config_revision is None:
            raise HTTPException(
                status_code=422,
                detail="start_task requires config_revision.",
            )

        if (
            request.command_type is not LanCommandType.START_TASK
            and request.config_revision is not None
        ):
            raise HTTPException(
                status_code=422,
                detail="config_revision is only valid for start_task.",
            )

        if request.command_type is LanCommandType.OPEN_PATIENT_DISPLAY and module_id is not None:
            raise HTTPException(
                status_code=422,
                detail="open_patient_display does not accept module_id.",
            )

        payload: dict[str, object] = {}

        if module_id is not None:
            payload["module_id"] = module_id

        if request.config_revision is not None:
            payload["config_revision"] = request.config_revision

        return commands.submit(request.command_type, payload=payload).to_dict()

    return api


app = create_api()
