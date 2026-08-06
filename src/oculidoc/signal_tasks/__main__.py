"""Command-line and frozen-app entry point for neural-signal tasks."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from PySide6.QtWidgets import QApplication

from oculidoc.application.signal_report import write_signal_report
from oculidoc.signal_tasks.config import SignalTaskConfig
from oculidoc.signal_tasks.runner import SignalTaskCancelled, run_signal_task
from oculidoc.signals.snapshot import SessionSignalSnapshot


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run an independent OculiDoC signal task.")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--patient-id")
    parser.add_argument("--headless", action="store_true")
    return parser


def _write_report(snapshot_path: Path | None, result_path: str | Path) -> None:
    if snapshot_path is None:
        return
    write_signal_report(SessionSignalSnapshot.read(snapshot_path), result_path)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    config = SignalTaskConfig.read(arguments.config)
    if arguments.headless:
        try:
            result_path = run_signal_task(
                config,
                arguments.output,
                patient_id=arguments.patient_id,
            )
            _write_report(arguments.snapshot, result_path)
        except SignalTaskCancelled:
            return 2
        except Exception as error:  # noqa: BLE001 -- process boundary.
            print(f"SIGNAL_TASK_ERROR={error}", file=sys.stderr)
            return 1
        print(f"SIGNAL_TASK_RESULT={result_path}")
        return 0

    from oculidoc.signal_tasks.window import SignalTaskWindow

    app = QApplication.instance() or QApplication(sys.argv[:1])
    window = SignalTaskWindow(
        config,
        arguments.output,
        patient_id=arguments.patient_id,
    )
    exit_code = 1

    def finish(code: int, message: str, result_path: str) -> None:
        nonlocal exit_code
        exit_code = code
        if code == 0:
            try:
                _write_report(arguments.snapshot, result_path)
            except Exception as error:  # noqa: BLE001 -- process boundary.
                print(f"SIGNAL_REPORT_ERROR={error}", file=sys.stderr)
                exit_code = 1
        elif message:
            print(f"SIGNAL_TASK_ERROR={message}", file=sys.stderr)

    window.completed.connect(finish)
    window.showFullScreen()
    app.exec()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
