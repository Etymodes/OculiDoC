"""Recover historical accepted training without modifying active calibrations.

The replay verifier checks the recorded per-point feature medians and validation
errors first. Only the training rows enter the learning archive. Missing or
incompatible evidence is reported instead of inventing samples.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from check_acies_mapping_replay import recover_training

from oculidoc.devices.acies import CALIBRATION_FEATURE_VERSION, COMPATIBLE_MODEL_VERSIONS
from oculidoc.devices.acies_learning import save_training_round


def migrate(runtime: Path) -> list[dict]:
    results = []
    for path in sorted((runtime / "calibration_attempts").glob("*.json")):
        result = {"attempt": path.name, "status": "skipped"}
        try:
            attempt = json.loads(path.read_text(encoding="utf-8-sig"))
            if attempt.get("algorithm_version") not in COMPATIBLE_MODEL_VERSIONS:
                raise ValueError("Incompatible historical feature semantics")
            if not attempt.get("candidate_file") or not attempt.get("trace_file"):
                raise ValueError("No complete numeric trace and candidate evidence")
            candidate_path = (
                runtime / "calibration_candidates" / Path(attempt["candidate_file"]).name
            )
            candidate = json.loads(candidate_path.read_text(encoding="utf-8-sig"))
            if candidate["model"].get("feature_version", CALIBRATION_FEATURE_VERSION) != (
                CALIBRATION_FEATURE_VERSION
            ):
                raise ValueError("Incompatible historical feature version")
            record = recover_training(path)
            rows = record["training"]
            round_id = "legacy-" + path.stem.split("-", maxsplit=1)[-1]
            destination = save_training_round(
                runtime,
                record["identity"],
                round_id,
                [r["features"] for r in rows],
                record["labels"],
                [r["quality"] for r in rows],
                [r.get("eye_features", {}) for r in rows],
            )
            result.update(
                status="migrated",
                round_id=round_id,
                samples=len(rows),
                destination=str(destination),
                trace_sha256=record["trace_sha256"],
                active_model_changed=False,
                validation_used_for_training=False,
            )
        except (OSError, ValueError, KeyError, TypeError) as error:
            result["reason"] = str(error)
        results.append(result)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.report.exists():
        parser.error("Report already exists; choose a new report path")
    results = migrate(args.runtime)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "migrated": sum(r["status"] == "migrated" for r in results),
                "skipped": sum(r["status"] == "skipped" for r in results),
                "report": str(args.report),
                "active_model_changed": False,
            }
        )
    )


if __name__ == "__main__":
    main()
