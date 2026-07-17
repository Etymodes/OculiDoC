"""Runtime recording for gaze-driven tasks."""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import (
    QEvent,
    QObject,
    Signal,
    Slot,
)
from PySide6.QtWidgets import (
    QApplication,
    QWidget,
)

from oculidoc.devices.contracts import (
    EyeTrackerSample,
)
from oculidoc.experiments.recording import (
    AoiRole,
    NormalizedAoi,
    ScreenContext,
    TaskRunRecorder,
)


class RecordedTaskRuntime(QObject):
    """Record and forward the same eye-tracker samples."""

    recording_finished = Signal(str)
    recording_error = Signal(str)

    def __init__(
        self,
        *,
        task: QWidget,
        sample_sink: Callable[
            [EyeTrackerSample],
            None,
        ],
        session_directory: str | Path | None = None,
        session_root: str | Path | None = None,
        patient_id: str | None = None,
        session_id: str | None = None,
        task_kind: str | None = None,
        announce: bool = False,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)

        if not callable(sample_sink):
            raise TypeError("sample_sink must be callable.")

        self.task = task
        self.sample_sink = sample_sink
        self.patient_id = patient_id or os.getenv("OCULIDOC_PATIENT_ID") or "standalone-demo"
        self.session_id = session_id or os.getenv("OCULIDOC_SESSION_ID") or str(uuid4())
        self.task_kind = task_kind or self._infer_task_kind(task)
        self.announce = announce

        if session_directory is None:
            session_directory = os.getenv("OCULIDOC_SESSION_DIRECTORY")

        if session_directory is None:
            base_directory = Path(
                session_root
                or os.getenv("OCULIDOC_EXPERIMENT_ROOT")
                or (Path.home() / ".oculidoc" / "experiment_sessions")
            )
            session_directory = base_directory / self.patient_id / self.session_id

        self.session_directory = Path(session_directory).expanduser().resolve()

        self._recorder: TaskRunRecorder | None = None
        self._finished = False
        self._recording_failed = False
        self._registered_question_ids: set[str] = set()
        self._watched_window = task.window()

        self._watched_window.installEventFilter(self)

        application = QApplication.instance()

        if application is not None:
            application.aboutToQuit.connect(self._on_application_quit)

    @property
    def recorder(
        self,
    ) -> TaskRunRecorder | None:
        return self._recorder

    @property
    def run_directory(
        self,
    ) -> Path | None:
        if self._recorder is None:
            return None

        return self._recorder.run_directory

    @staticmethod
    def _infer_task_kind(
        task: QWidget,
    ) -> str:
        class_name = type(task).__name__
        lowered = class_name.lower()

        if "tracking" in lowered:
            return "tracking_ball"

        if "binary" in lowered or "question" in lowered:
            return "binary_horizontal"

        return (
            re.sub(
                r"(?<!^)(?=[A-Z])",
                "_",
                class_name,
            )
            .lower()
            .removesuffix("_task")
        )

    @classmethod
    def _json_safe(
        cls,
        value: object,
    ) -> object:
        if value is None or isinstance(
            value,
            (
                bool,
                int,
                float,
                str,
            ),
        ):
            return value

        if isinstance(value, Path):
            return str(value)

        if isinstance(value, Enum):
            return cls._json_safe(value.value)

        if is_dataclass(value) and not isinstance(value, type):
            return cls._json_safe(asdict(value))

        if isinstance(value, Mapping):
            return {str(key): cls._json_safe(item) for key, item in value.items()}

        if isinstance(value, (list, tuple)):
            return [cls._json_safe(item) for item in value]

        model_dump = getattr(
            value,
            "model_dump",
            None,
        )

        if callable(model_dump):
            try:
                result = model_dump(mode="json")
            except TypeError:
                result = model_dump()

            return cls._json_safe(result)

        return repr(value)

    def _task_config(
        self,
    ) -> dict[str, object]:
        return {
            "task_class": type(self.task).__name__,
            "config": self._json_safe(
                getattr(
                    self.task,
                    "config",
                    None,
                )
            ),
        }

    def _screen_context(
        self,
    ) -> ScreenContext:
        application = QApplication.instance()
        task_window = self.task.window()
        screen = task_window.screen()

        if screen is None and application is not None:
            screen = application.primaryScreen()

        if screen is None:
            width = max(
                1,
                task_window.width(),
            )
            height = max(
                1,
                task_window.height(),
            )

            return ScreenContext(
                screen_width_px=width,
                screen_height_px=height,
                window_width_px=width,
                window_height_px=height,
            )

        screen_geometry = screen.geometry()
        window_geometry = task_window.frameGeometry()

        screen_width = max(
            1,
            screen_geometry.width(),
        )
        screen_height = max(
            1,
            screen_geometry.height(),
        )
        window_width = max(
            1,
            window_geometry.width(),
        )
        window_height = max(
            1,
            window_geometry.height(),
        )

        display_index = 0

        if application is not None:
            screens = application.screens()

            if screen in screens:
                display_index = screens.index(screen)

        return ScreenContext(
            screen_width_px=screen_width,
            screen_height_px=screen_height,
            window_x_px=window_geometry.x(),
            window_y_px=window_geometry.y(),
            window_width_px=window_width,
            window_height_px=window_height,
            device_pixel_ratio=float(screen.devicePixelRatio()),
            dpi_x=float(screen.logicalDotsPerInchX()),
            dpi_y=float(screen.logicalDotsPerInchY()),
            orientation=("landscape" if screen_width >= screen_height else "portrait"),
            display_index=display_index,
        )

    def _ensure_recorder(
        self,
    ) -> TaskRunRecorder:
        if self._recorder is None:
            self._recorder = TaskRunRecorder(
                session_directory=(self.session_directory),
                patient_id=self.patient_id,
                session_id=self.session_id,
                task_kind=self.task_kind,
                task_config=self._task_config(),
                screen_context=(self._screen_context()),
            )

        return self._recorder

    @staticmethod
    def _coerce_aoi(
        value: object,
    ) -> NormalizedAoi:
        if isinstance(
            value,
            NormalizedAoi,
        ):
            return value

        if not isinstance(
            value,
            Mapping,
        ):
            raise TypeError("Task AOI must be a NormalizedAoi or mapping.")

        role_value = value.get(
            "role",
            AoiRole.OTHER.value,
        )
        role = (
            role_value
            if isinstance(
                role_value,
                AoiRole,
            )
            else AoiRole(str(role_value))
        )
        metadata_value = value.get(
            "metadata",
            {},
        )

        return NormalizedAoi(
            aoi_id=str(value["aoi_id"]),
            role=role,
            left=float(value["left"]),
            top=float(value["top"]),
            right=float(value["right"]),
            bottom=float(value["bottom"]),
            label=(str(value["label"]) if value.get("label") is not None else None),
            metadata=(
                dict(metadata_value)
                if isinstance(
                    metadata_value,
                    Mapping,
                )
                else {}
            ),
        )

    def _recording_context(
        self,
        sample: EyeTrackerSample,
        recorder: TaskRunRecorder,
    ) -> dict[str, object]:
        provider = getattr(
            self.task,
            "recording_context_for_sample",
            None,
        )

        if not callable(provider):
            return {}

        raw_context = provider(sample)

        if raw_context is None:
            return {}

        if not isinstance(
            raw_context,
            Mapping,
        ):
            raise TypeError("Task recording context must be a mapping.")

        question_id_value = raw_context.get("question_id")
        question_id = str(question_id_value) if question_id_value is not None else None

        phase_value = raw_context.get("phase")
        phase = str(phase_value) if phase_value is not None else None

        raw_aois = raw_context.get(
            "aois",
            (),
        )

        if raw_aois is None:
            raw_aois = ()

        if isinstance(
            raw_aois,
            (str, bytes),
        ) or not isinstance(
            raw_aois,
            (list, tuple),
        ):
            raise TypeError("Task AOIs must be a list or tuple.")

        aois = tuple(self._coerce_aoi(value) for value in raw_aois)

        reference_value = raw_context.get("reference_aoi")
        reference_aoi = self._coerce_aoi(reference_value) if reference_value is not None else None

        register_layout = bool(
            raw_context.get(
                "register_layout",
                bool(question_id and aois),
            )
        )
        metadata_value = raw_context.get(
            "question_metadata",
            {},
        )

        if not isinstance(
            metadata_value,
            Mapping,
        ):
            raise TypeError("question_metadata must be a mapping.")

        if (
            register_layout
            and question_id is not None
            and question_id not in self._registered_question_ids
        ):
            recorder.register_question(
                question_id,
                aois=aois,
                metadata=dict(metadata_value),
            )
            self._registered_question_ids.add(question_id)

        return {
            "question_id": question_id,
            "phase": phase,
            "aois": aois,
            "reference_aoi": (reference_aoi),
        }

    def handle_sample(
        self,
        sample: EyeTrackerSample,
    ) -> None:
        """Record a sample and forward it to the task."""

        if self._finished:
            return

        if not self._recording_failed:
            try:
                recorder = self._ensure_recorder()
                context = self._recording_context(
                    sample,
                    recorder,
                )
                recorder.record_sample(
                    sample,
                    **context,
                )
            except Exception as error:  # noqa: BLE001
                self._recording_failed = True
                self.recording_error.emit(str(error))

        self.sample_sink(sample)

    def eventFilter(
        self,
        watched: QObject,
        event: QEvent,
    ) -> bool:
        if watched is self._watched_window and event.type() == QEvent.Type.Close:
            self.finish("window_closed")

        return super().eventFilter(
            watched,
            event,
        )

    @Slot()
    def _on_application_quit(
        self,
    ) -> None:
        self.finish("application_quit")

    def finish(
        self,
        reason: object = "completed",
    ) -> None:
        """Finalize the run once."""

        if self._finished:
            return

        self._finished = True

        reason_text = (
            reason.strip() if (isinstance(reason, str) and reason.strip()) else "completed"
        )

        try:
            recorder = self._ensure_recorder()
            recorder.finish(
                reason=reason_text,
                result={
                    "recording_failed": (self._recording_failed),
                },
            )
        except Exception as error:  # noqa: BLE001
            self.recording_error.emit(str(error))
            return

        path_text = str(recorder.run_directory)
        self.recording_finished.emit(path_text)

        if self.announce:
            print(
                (f"OCULIDOC_EXPERIMENT_RUN={path_text}"),
                flush=True,
            )
