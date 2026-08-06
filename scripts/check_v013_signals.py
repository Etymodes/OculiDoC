"""Deterministic v0.1.3 neural-signal acceptance without patient data or hardware."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast

from oculidoc.signal_tasks.config import SignalTaskConfig, SignalTaskKind
from oculidoc.signal_tasks.runner import run_signal_task
from oculidoc.signals.models import SignalSourceKind


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    return parser


def _result(config: SignalTaskConfig, root: Path, name: str) -> dict[str, Any]:
    path = run_signal_task(config, root / name, patient_id="Beta00-engineering-acceptance")
    payload = cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
    if not isinstance(payload, dict) or not isinstance(payload.get("result"), dict):
        raise RuntimeError(f"{name} did not produce a structured result.")
    if payload["result"].get("report_eligible") is not False:
        raise RuntimeError(f"{name} simulation was not isolated from patient reports.")
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    with TemporaryDirectory(prefix="oculidoc-v013-signals-") as temporary:
        root = Path(temporary)
        cases = {
            "eeg_quality": SignalTaskConfig(
                SignalTaskKind.EEG_QUALITY,
                SignalSourceKind.SIMULATION,
                trial_count=1,
            ),
            "ssvep_cca": SignalTaskConfig(
                SignalTaskKind.SSVEP_FOUR_TARGET,
                SignalSourceKind.SIMULATION,
                frequencies_hz=(8.0, 10.0, 12.0, 15.0),
                decoder_name="cca",
                trial_count=1,
            ),
            "ssvep_fbcca": SignalTaskConfig(
                SignalTaskKind.SSVEP_FOUR_TARGET,
                SignalSourceKind.SIMULATION,
                frequencies_hz=(8.0, 10.0, 12.0, 15.0),
                decoder_name="fbcca",
                trial_count=1,
            ),
            "ssvep_calibration": SignalTaskConfig(
                SignalTaskKind.SSVEP_FREQUENCY_SCAN,
                SignalSourceKind.SIMULATION,
                frequencies_hz=(10.0, 12.0),
                decoder_name="fbcca",
                trial_count=2,
            ),
            "mi_protocol": SignalTaskConfig(
                SignalTaskKind.MI_PROTOCOL,
                SignalSourceKind.SIMULATION,
                channel_names=("C3", "C4"),
                trial_count=1,
            ),
        }
        results = {name: _result(config, root, name) for name, config in cases.items()}
        for name in ("ssvep_cca", "ssvep_fbcca"):
            evaluation = results[name]["result"].get("evaluation")
            if not isinstance(evaluation, dict) or evaluation.get("accuracy") != 1.0:
                raise RuntimeError(f"{name} did not classify all deterministic trials.")
        calibration = results["ssvep_calibration"]["result"].get("calibration_model")
        if not isinstance(calibration, dict) or calibration.get("algorithm_version") != "trca-1.0":
            raise RuntimeError("Frequency scan did not create the TRCA calibration artifact.")
        mi = results["mi_protocol"]["result"]
        if mi.get("classification") is not None:
            raise RuntimeError("v0.1.3 MI protocol crossed the no-control-fusion boundary.")
        summary = {
            "schema_version": "1.0",
            "status": "pass",
            "patient_scope": "Beta00 engineering only",
            "cases": {
                name: {
                    "task_kind": payload["task_kind"],
                    "sample_count": payload["summary"]["sample_count"],
                    "valid_sample_ratio": payload["summary"]["valid_sample_ratio"],
                }
                for name, payload in results.items()
            },
        }
    if arguments.output is not None:
        output = arguments.output.expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"OCULIDOC_V013_SIGNAL_REPORT={output}")
    print("OCULIDOC_V013_SIGNALS=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
