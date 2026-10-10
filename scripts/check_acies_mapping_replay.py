"""Private, training-only mapping controls; never installs or approves a model."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np

from oculidoc.devices.acies import MappingParameters
from oculidoc.devices.acies_experiments import (
    prediction_error,
    summarize_experiment,
    write_error_table,
)
from oculidoc.devices.webcam_gaze import fit_calibration, predict_packet


def read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def packet(row: dict[str, Any]) -> dict[str, Any]:
    return {
        **row,
        **{
            f"{eye}_eye_valid": row.get("eye_features", {}).get(eye) is not None
            for eye in ("left", "right")
        },
    }


def recover_training(path: Path) -> dict[str, Any]:
    """Recover only accepted training tails, fail rather than invent absent samples."""
    attempt = read(path)
    runtime = path.parent.parent
    trace = runtime / "calibration_traces" / Path(attempt["trace_file"]).name
    rows = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
    candidate = read(runtime / "calibration_candidates" / Path(attempt["candidate_file"]).name)
    training, labels, validation = [], [], []
    points = attempt["training_points"] + attempt["validation_points"]
    for index, point in enumerate(points):
        eligible = [
            r
            for r in rows
            if r["point_index"] == index
            and r["features"] is not None
            and r["quality"] >= 0.18
            and r["observation_state"] != "pose_unavailable"
        ]
        if index >= len(attempt["training_points"]):
            eligible = [r for r in eligible if r["raw_prediction"] is not None]
        accepted = eligible[-point["sample_count"] :]
        if not accepted or len(accepted) != point["sample_count"]:
            raise ValueError("Incomplete accepted calibration trace")
        if index < len(attempt["training_points"]):
            median = np.median([r["features"] for r in accepted], axis=0)
            if not np.allclose(
                median, candidate["point_aggregates"][index]["feature_median"], atol=1e-10
            ):
                raise ValueError("Recovered training differs from recorded aggregate")
            training.extend(accepted)
            labels.extend([tuple(point["target"])] * len(accepted))
        else:
            errors = [
                np.linalg.norm(np.asarray(r["raw_prediction"]) - point["target"]) for r in accepted
            ]
            if not np.isclose(np.mean(errors), point["mean_error"], atol=1e-8):
                raise ValueError("Recovered trace does not reproduce original validation")
            validation.extend((row, point["target"]) for row in accepted)
    return {
        "name": path.name,
        "identity": candidate["validation_metrics"]["identity"],
        "training": training,
        "labels": labels,
        "validation": validation,
        "trace_sha256": hashlib.sha256(trace.read_bytes()).hexdigest(),
    }


def fit(records: list[dict[str, Any]], parameters: MappingParameters) -> dict[str, Any]:
    rows = [row for record in records for row in record["training"]]
    model = fit_calibration(
        [r["features"] for r in rows],
        [target for record in records for target in record["labels"]],
        [r["quality"] for r in rows],
        eye_features=[r["eye_features"] for r in rows],
        mapping_parameters=parameters,
    )
    # Diagnostic-only. Per-eye task qualification is deliberately not fabricated.
    return {
        **model,
        "validated": False,
        "task_eligible": False,
        "mapping_parameters": asdict(parameters),
    }


def validation_metrics(
    model: dict[str, Any], records: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    output = []
    for record in records:
        residuals = []
        point_errors: dict[tuple[float, float], list[float]] = {}
        for row, target in record["validation"]:
            prediction = predict_packet(model, packet(row))
            if prediction is not None:
                residuals.append(np.asarray(prediction) - target)
                point_errors.setdefault(tuple(target), []).append(
                    float(np.linalg.norm(residuals[-1]))
                )
        delta = np.asarray(residuals)
        distance = np.linalg.norm(delta, axis=1)
        output.append(
            {
                "attempt": record["name"],
                "coverage": len(delta) / len(record["validation"]),
                "mean_error": float(np.mean([np.mean(v) for v in point_errors.values()])),
                "max_error": float(distance.max()),
                "bias": delta.mean(axis=0).tolist(),
            }
        )
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attempts", type=Path, nargs="+", required=True)
    parser.add_argument("--experiment", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Choose a new output directory to preserve previous comparisons")
    records = [recover_training(path) for path in sorted(args.attempts)]
    if len({r["identity"] for r in records}) != 1:
        raise ValueError("Do not pool different people, screens, or camera configurations")
    manifest = read(args.experiment / "manifest.json")
    original = read(args.experiment / "model.json")
    if original["identity"] != records[-1]["identity"]:
        raise ValueError("Experiment identity differs from training")
    frames = [
        json.loads(line) for line in (args.experiment / "frames.jsonl").read_text().splitlines()
    ]
    screen = tuple(manifest["screen"][1:])
    variants = {
        "baseline": MappingParameters(),
        "ridge_scale=3": MappingParameters(ridge_scale=3),
        "ridge_scale=0.3": MappingParameters(ridge_scale=0.3),
        "lid_penalty=16": MappingParameters(lid_penalty=16),
        "lid_penalty=1": MappingParameters(lid_penalty=1),
        "stationary_penalty=3": MappingParameters(stationary_penalty=3),
        "stationary_penalty=0": MappingParameters(stationary_penalty=0),
        "use_lids=false": MappingParameters(use_lids=False),
    }
    args.output.mkdir(parents=True)
    write_error_table(args.output / "recorded-errors.csv", manifest["plan"], frames, screen)
    results = {}
    models = {"recorded": original}
    for name, parameters in variants.items():
        models[name] = fit(records[-1:], parameters)
    if len(records) > 1:
        models["pooled_training_only"] = fit(records, MappingParameters())
    for name, model in models.items():
        replay = []
        for row in frames:
            raw = predict_packet(model, packet(row))
            in_bounds = raw is not None and all(0 <= v <= 1 for v in raw)
            replay.append(
                {**row, "raw_prediction": raw, "filtered_prediction": raw if in_bounds else None}
            )
        # No filter here: compare mapping accuracy before smoothing, retaining outliers.
        results[name] = {
            "parameters": model.get("mapping_parameters"),
            "calibration_validation": validation_metrics(model, records),
            "experiment": summarize_experiment(manifest["plan"], replay, screen),
            "axis_models": {
                eye: child["axis_models"] for eye, child in model.get("eye_models", {}).items()
            },
        }
        (args.output / f"model-{name}.json").write_text(
            json.dumps(model, indent=2), encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "variant": name,
                    "groups": [
                        {"condition": g["condition"], "region": g["region"], "raw": g["raw"]}
                        for g in results[name]["experiment"]["groups"]
                    ],
                }
            ),
            flush=True,
        )
    # Add the requested signed per-frame evidence without touching the original run.
    with (args.output / "recorded-errors.jsonl").open("w", encoding="utf-8") as stream:
        for row in frames:
            target = manifest["plan"][row["trial"]]["target"]
            stream.write(
                json.dumps(
                    {
                        "trial": row["trial"],
                        "timestamp_ns": row["timestamp_ns"],
                        "intended_target": target,
                        "raw_prediction": row["raw_prediction"],
                        "filtered_prediction": row["filtered_prediction"],
                        "raw_error": prediction_error(row["raw_prediction"], target, screen),
                        "filtered_error": prediction_error(
                            row["filtered_prediction"], target, screen
                        ),
                    }
                )
                + "\n"
            )
    (args.output / "comparison.json").write_text(
        json.dumps(
            {
                "auto_applied": False,
                "experiment_used_for_training": False,
                "warning": (
                    "Retrospective screening, not fresh confirmation. No target attraction; "
                    "filtered field is in-bounds raw only."
                ),
                "training": [{k: r[k] for k in ("name", "trace_sha256")} for r in records],
                "results": results,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
