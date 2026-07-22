"""Standalone demos for gaze-driven tasks."""

import argparse
from collections.abc import Sequence

from PySide6.QtCore import QTimer
from PySide6.QtTextToSpeech import QTextToSpeech
from PySide6.QtWidgets import (
    QDialog,
    QMessageBox,
)

from oculidoc.app import create_qt_application
from oculidoc.config import apply_saved_gaze_device_config, get_settings
from oculidoc.devices.preflight import GazePreflightResult, GazePreflightStore
from oculidoc.experiments.task_runtime import RecordedTaskRuntime
from oculidoc.image_library import ImageLibraryStore
from oculidoc.lan_control import (
    LanControlStateStore,
    LanControlTransitionError,
    PatientDisplayMode,
)
from oculidoc.speech_replay import SpeechReplayStore
from oculidoc.task_configs import (
    TaskConfigConflict,
    TaskConfigStore,
    task_config_from_dict,
    task_config_to_dict,
)
from oculidoc.tasks.binary_question import (
    BinaryQuestionConfig,
    BinaryQuestionSetupDialog,
    BinaryQuestionTask,
    binary_question_sequence,
)
from oculidoc.tasks.gaze_stream import (
    GazeStreamWorker,
)
from oculidoc.tasks.image_choice import (
    ImageChoiceConfig,
    ImageChoiceSetupDialog,
    ImageChoiceTask,
    image_question_sequence,
)
from oculidoc.tasks.instruction_fixation import (
    InstructionFixationConfig,
    InstructionFixationSetupDialog,
    InstructionFixationTask,
)
from oculidoc.tasks.multiple_choice import (
    MultipleChoiceConfig,
    MultipleChoiceSetupDialog,
    MultipleChoiceTask,
)
from oculidoc.tasks.question_bank import CommonQuestionStore
from oculidoc.tasks.screen_keyboard import (
    ScreenKeyboardConfig,
    ScreenKeyboardSetupDialog,
    ScreenKeyboardTask,
)
from oculidoc.tasks.sequential_choice import SequentialChoiceTask
from oculidoc.tasks.task_window import (
    TimedTaskWindow,
)
from oculidoc.tasks.tracking_ball import (
    TrackingBallConfig,
    TrackingBallSetupDialog,
    TrackingBallTask,
)

TASK_START_COUNTDOWN_SECONDS = 3
TASK_RESULT_MESSAGE_MILLISECONDS = 6_000


def main(
    argv: Sequence[str] | None = None,
) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "task",
        choices=(
            "tracking",
            "binary",
            "binary-vertical",
            "typing",
            "multiple-choice",
            "image-choice",
            "instruction-fixation",
        ),
    )
    parser.add_argument("--direct", action="store_true")
    parser.add_argument("--config-revision", type=int)
    args = parser.parse_args(argv)

    if args.direct != (args.config_revision is not None):
        parser.error("--direct and --config-revision must be used together.")

    app = create_qt_application()
    settings = apply_saved_gaze_device_config(get_settings())
    allow_mouse_fallback = settings.gaze_source == "mock"
    module_id = {
        "tracking": "tracking_ball",
        "binary": "binary_horizontal",
        "binary-vertical": "binary_vertical",
        "typing": "screen_keyboard",
        "multiple-choice": "multiple_choice",
        "image-choice": "image_choice",
        "instruction-fixation": "instruction_fixation",
    }[args.task]
    config_store = TaskConfigStore(settings.data_dir / "runtime" / "task_configs.json")
    record = config_store.load(module_id)
    config = task_config_from_dict(module_id, record.config)
    setup: QDialog

    if args.direct:
        if args.config_revision != record.revision:
            raise SystemExit(
                "Task config revision changed before launch: "
                f"requested {args.config_revision}, current {record.revision}."
            )
    elif args.task == "tracking":
        if not isinstance(config, TrackingBallConfig):
            raise TypeError("Tracking task configuration type mismatch.")

        setup = TrackingBallSetupDialog(
            config=config,
            image_library_path=(settings.data_dir / "image_library"),
        )

        if setup.exec() != QDialog.DialogCode.Accepted:
            return 0

        config = setup.build_config()
    elif args.task in {"binary", "binary-vertical"}:
        if not isinstance(config, BinaryQuestionConfig):
            raise TypeError("Binary task configuration type mismatch.")

        setup = BinaryQuestionSetupDialog(
            question_bank_path=(settings.data_dir / "common_questions.json"),
            config=config,
            layout=("vertical" if args.task == "binary-vertical" else "horizontal"),
        )

        if setup.exec() != QDialog.DialogCode.Accepted:
            return 0

        config = setup.build_config()
    elif args.task == "typing":
        if not isinstance(config, ScreenKeyboardConfig):
            raise TypeError("Typing task configuration type mismatch.")

        setup = ScreenKeyboardSetupDialog(config=config)

        if setup.exec() != QDialog.DialogCode.Accepted:
            return 0

        config = setup.build_config()
    elif args.task == "multiple-choice":
        if not isinstance(config, MultipleChoiceConfig):
            raise TypeError("Multiple-choice task configuration type mismatch.")

        setup = MultipleChoiceSetupDialog(config=config)

        if setup.exec() != QDialog.DialogCode.Accepted:
            return 0

        config = setup.build_config()
    elif args.task == "image-choice":
        if not isinstance(config, ImageChoiceConfig):
            raise TypeError("Image-choice task configuration type mismatch.")

        setup = ImageChoiceSetupDialog(
            config=config,
            image_library_path=(settings.data_dir / "image_library"),
        )

        if setup.exec() != QDialog.DialogCode.Accepted:
            return 0

        config = setup.build_config()
    else:
        if not isinstance(config, InstructionFixationConfig):
            raise TypeError("Instruction-fixation task configuration type mismatch.")

        setup = InstructionFixationSetupDialog(config=config)

        if setup.exec() != QDialog.DialogCode.Accepted:
            return 0

        config = setup.build_config()

    if not args.direct:
        try:
            config_store.save(
                module_id,
                task_config_to_dict(config),
                expected_revision=record.revision,
            )
        except TaskConfigConflict:
            QMessageBox.warning(
                setup,
                "任务设置已更新",
                "手机端已修改这项任务设置。请关闭后重新打开设置窗口。",
            )
            return 2

    question_to_speak = ""
    task: (
        TrackingBallTask
        | BinaryQuestionTask
        | ScreenKeyboardTask
        | MultipleChoiceTask
        | SequentialChoiceTask
        | InstructionFixationTask
    )

    if args.task == "tracking":
        if not isinstance(config, TrackingBallConfig):
            raise TypeError("Tracking task configuration type mismatch.")

        task = TrackingBallTask(
            config,
            allow_mouse_fallback=(allow_mouse_fallback),
        )
        title = "追踪球"
        duration_seconds = config.duration_seconds
    elif args.task in {"binary", "binary-vertical"}:
        if not isinstance(config, BinaryQuestionConfig):
            raise TypeError("Binary task configuration type mismatch.")

        vertical = args.task == "binary-vertical"
        layout = "vertical" if vertical else "horizontal"
        sequence = binary_question_sequence(
            config,
            CommonQuestionStore(settings.data_dir / "common_questions.json"),
        )

        if config.question_template_ids:
            task = SequentialChoiceTask(
                config=config,
                question_ids=[question_id for question_id, _question in sequence],
                task_factory=lambda index: BinaryQuestionTask(
                    sequence[index][1],
                    allow_mouse_fallback=allow_mouse_fallback,
                    layout=layout,
                ),
                layout_orientation=layout,
            )
        else:
            task = BinaryQuestionTask(
                config,
                allow_mouse_fallback=allow_mouse_fallback,
                layout=layout,
            )
        title = "上下二分问答" if vertical else "左右二分问答"
        duration_seconds = min(3_600, config.duration_seconds * len(sequence))
        question_to_speak = sequence[0][1].question
    elif args.task == "typing":
        if not isinstance(config, ScreenKeyboardConfig):
            raise TypeError("Typing task configuration type mismatch.")

        task = ScreenKeyboardTask(
            config,
            allow_mouse_fallback=allow_mouse_fallback,
        )
        title = "屏幕打字"
        duration_seconds = config.duration_seconds
    elif args.task == "multiple-choice":
        if not isinstance(config, MultipleChoiceConfig):
            raise TypeError("Multiple-choice task configuration type mismatch.")

        task = MultipleChoiceTask(
            config,
            allow_mouse_fallback=allow_mouse_fallback,
        )
        title = "多选项问答"
        duration_seconds = config.duration_seconds
        question_to_speak = config.question
    elif args.task == "image-choice":
        if not isinstance(config, ImageChoiceConfig):
            raise TypeError("Image-choice task configuration type mismatch.")

        image_store = ImageLibraryStore(settings.data_dir / "image_library")
        image_assets = {asset.image_id: asset for asset in image_store.load()}
        image_questions = image_question_sequence(config, image_store)
        task = SequentialChoiceTask(
            config=config,
            question_ids=[question.question_id for question in image_questions],
            task_factory=lambda index: ImageChoiceTask(
                image_questions[index],
                config,
                image_store,
                assets=image_assets,
                allow_mouse_fallback=allow_mouse_fallback,
            ),
            layout_orientation="horizontal",
        )
        title = "语音图片选择"
        duration_seconds = min(3_600, config.duration_seconds * len(image_questions))
        question_to_speak = image_questions[0].prompt
    else:
        if not isinstance(config, InstructionFixationConfig):
            raise TypeError("Instruction-fixation task configuration type mismatch.")

        task = InstructionFixationTask(
            config,
            allow_mouse_fallback=allow_mouse_fallback,
        )
        title = "随指令注视"
        duration_seconds = min(
            3_600,
            config.trial_count * config.trial_duration_seconds + config.trial_count,
        )
        question_to_speak = f"请注视{config.target_description}"

    window = TimedTaskWindow(
        task,
        duration_seconds=duration_seconds,
        title=title,
    )

    speech = QTextToSpeech(window)
    last_spoken_text = ""

    def speak(text: str) -> None:
        nonlocal last_spoken_text
        normalized = text.strip()

        if not normalized:
            return

        last_spoken_text = normalized
        speech.stop()
        speech.say(normalized)

    if isinstance(task, ScreenKeyboardTask):
        task.speech_requested.connect(speak)

    if isinstance(
        task,
        BinaryQuestionTask,
    ):
        task.answered.connect(
            lambda side, answer: QTimer.singleShot(
                700,
                lambda: window.finish("answered"),
            )
        )

    if isinstance(task, SequentialChoiceTask):
        task.question_changed.connect(speak)
        task.sequence_completed.connect(lambda: window.finish("answered"))

    if isinstance(task, InstructionFixationTask):
        task.instruction_changed.connect(speak)
        task.protocol_completed.connect(lambda: window.finish("completed"))

    preflight_seconds = 0 if settings.environment == "test" else settings.gaze_preflight_seconds
    preflight_store = GazePreflightStore(settings.data_dir / "runtime" / "gaze_preflight.json")
    worker = GazeStreamWorker(
        settings,
        window,
        preflight_seconds=preflight_seconds,
        preflight_store=preflight_store,
    )
    recorded_runtime = RecordedTaskRuntime(
        task=task,
        sample_sink=task.consume_sample,
        task_kind=module_id,
        announce=True,
        parent=task,
    )
    worker.sample_received.connect(recorded_runtime.handle_sample)

    window.finished.connect(recorded_runtime.finish)
    window.finished.connect(lambda reason: app.quit())
    app.aboutToQuit.connect(worker.stop)

    display_state_store = LanControlStateStore(
        settings.data_dir / "runtime" / "lan_control_state.json"
    )
    speech_replay_store = SpeechReplayStore(settings.data_dir / "runtime" / "speech_replay.json")

    try:
        last_replay_revision = speech_replay_store.load().revision
    except (OSError, KeyError, TypeError, ValueError):
        last_replay_revision = 0

    replay_timer = QTimer(window)
    replay_timer.setInterval(250)

    def poll_speech_replay() -> None:
        nonlocal last_replay_revision

        try:
            request = speech_replay_store.load()
        except (OSError, KeyError, TypeError, ValueError):
            return

        if request.revision <= last_replay_revision:
            return

        last_replay_revision = request.revision

        if request.task_id == module_id and last_spoken_text:
            speak(last_spoken_text)

    replay_timer.timeout.connect(poll_speech_replay)
    replay_timer.start()

    if isinstance(task, ScreenKeyboardTask):

        def sync_typing_text(text: str) -> None:
            state = display_state_store.load()

            if state.mode is PatientDisplayMode.RUNNING and state.task_id == module_id:
                display_state_store.set_display(
                    text,
                    mode=PatientDisplayMode.RUNNING,
                    task_id=module_id,
                )

        task.display_text_changed.connect(sync_typing_text)

    if isinstance(task, MultipleChoiceTask):
        multiple_choice_task = task

        def sync_multiple_choice_text(option_id: str, selected: bool) -> None:
            del option_id, selected
            state = display_state_store.load()

            if state.mode is PatientDisplayMode.RUNNING and state.task_id == module_id:
                display_state_store.set_display(
                    multiple_choice_task.patient_display_text,
                    mode=PatientDisplayMode.RUNNING,
                    task_id=module_id,
                )

        multiple_choice_task.selection_changed.connect(sync_multiple_choice_text)

    if isinstance(task, SequentialChoiceTask):
        sequential_task = task

        def sync_sequential_question(text: str) -> None:
            state = display_state_store.load()

            if state.mode is PatientDisplayMode.RUNNING and state.task_id == module_id:
                display_state_store.set_display(
                    sequential_task.patient_display_text,
                    mode=PatientDisplayMode.RUNNING,
                    task_id=module_id,
                )

        sequential_task.question_changed.connect(sync_sequential_question)

    if isinstance(task, InstructionFixationTask):
        fixation_task = task

        def sync_fixation_instruction(text: str) -> None:
            del text
            state = display_state_store.load()

            if state.mode is PatientDisplayMode.RUNNING and state.task_id == module_id:
                display_state_store.set_display(
                    fixation_task.patient_display_text,
                    mode=PatientDisplayMode.RUNNING,
                    task_id=module_id,
                )

        fixation_task.instruction_changed.connect(sync_fixation_instruction)

    countdown_seconds = 0 if settings.environment == "test" else TASK_START_COUNTDOWN_SECONDS
    source_hint = "\n模拟模式" if settings.gaze_source == "mock" else ""
    display_state_store.set_display(
        f"{title}\n正在进行眼动设备预检{source_hint}",
        mode=PatientDisplayMode.PREVIEW,
        task_id=module_id,
    )

    preflight_failed = False

    def fail_preflight(message: str) -> None:
        nonlocal preflight_failed

        if preflight_failed:
            return

        preflight_failed = True
        try:
            display_state_store.set_display(
                "眼动设备预检失败\n请联系管理员",
                mode=PatientDisplayMode.ERROR,
                task_id=module_id,
            )
        except LanControlTransitionError:
            pass
        box = QMessageBox(
            QMessageBox.Icon.Warning,
            "眼动设备预检失败",
            message + "\n\n任务已阻止，不会回退到模拟眼动源。",
            QMessageBox.StandardButton.NoButton,
            window,
        )
        box.show()
        QTimer.singleShot(TASK_RESULT_MESSAGE_MILLISECONDS, box.close)
        QTimer.singleShot(TASK_RESULT_MESSAGE_MILLISECONDS, lambda: app.exit(3))

    worker.stream_error.connect(fail_preflight)

    def start_task() -> None:
        current = display_state_store.load()

        if current.mode is not PatientDisplayMode.READY or current.task_id != module_id:
            app.quit()
            return

        display_state_store.set_display(
            f"正在进行：{title}",
            mode=PatientDisplayMode.RUNNING,
            task_id=module_id,
        )
        worker.enable_sample_delivery()
        window.showFullScreen()
        window.start()

        if args.task == "tracking":
            speak("请保持注视标志物，并让视线跟随它移动。")
        elif not isinstance(task, SequentialChoiceTask) and args.task in {
            "binary",
            "binary-vertical",
            "multiple-choice",
            "image-choice",
        }:
            speak(question_to_speak)

    def begin_countdown(result: GazePreflightResult) -> None:
        if not result.passed:
            return

        current = display_state_store.load()
        if current.mode is not PatientDisplayMode.PREVIEW or current.task_id != module_id:
            app.exit(2)
            return

        try:
            display_state_store.set_display(
                f"{title}\n即将开始\n{countdown_seconds}",
                mode=PatientDisplayMode.READY,
                task_id=module_id,
                countdown_seconds=countdown_seconds,
            )
        except LanControlTransitionError:
            app.exit(2)
            return

        if countdown_seconds == 0:
            start_task()
            return

        remaining_seconds = countdown_seconds
        countdown_timer = QTimer(window)
        countdown_timer.setInterval(1_000)

        def advance_countdown() -> None:
            nonlocal remaining_seconds
            current = display_state_store.load()

            if current.mode is not PatientDisplayMode.READY or current.task_id != module_id:
                countdown_timer.stop()
                app.quit()
                return

            remaining_seconds -= 1

            if remaining_seconds <= 0:
                countdown_timer.stop()
                start_task()
                return

            display_state_store.set_display(
                f"{title}\n即将开始\n{remaining_seconds}",
                mode=PatientDisplayMode.READY,
                task_id=module_id,
                countdown_seconds=remaining_seconds,
            )

        countdown_timer.timeout.connect(advance_countdown)
        countdown_timer.start()

    worker.preflight_completed.connect(begin_countdown)
    worker.start()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
