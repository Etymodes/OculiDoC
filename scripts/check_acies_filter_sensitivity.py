"""Reproducible synthetic OFAT/factorial checks; never install chosen parameters."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from oculidoc.devices.acies import GazeSmoother
from oculidoc.devices.acies_experiments import filter_variants


def run() -> dict:
    records = []
    seeds = (109, 207, 319, 421, 533, 641, 757)
    for fps in (20, 30, 60):
        for seed in seeds:
            rng = np.random.default_rng(seed)
            signal = np.tile([0.45, 0.55], (fps * 8, 1))
            signal[fps * 4 :, 0] += 0.06
            raw = signal + rng.normal(0, 0.025, signal.shape)
            for name, parameters in filter_variants().items():
                smoother = GazeSmoother(**parameters)
                output = np.array(
                    [
                        smoother.update(tuple(point), int((i + 1) * 1e9 / fps), 0.8)
                        for i, point in enumerate(raw)
                    ]
                )
                quiet = output[fps : fps * 4] - signal[fps : fps * 4]
                # Noise-free step isolates algorithm response from random early crossings.
                smoother.reset()
                response = np.array(
                    [
                        smoother.update(
                            (0.45 if i < fps else 0.51, 0.55), int((i + 1) * 1e9 / fps), 0.8
                        )
                        for i in range(fps * 3)
                    ]
                )
                crossed = np.flatnonzero(response[fps:, 0] >= 0.45 + 0.06 * 0.9)
                records.append(
                    {
                        "fps": fps,
                        "seed": seed,
                        "variant": name,
                        "parameters": parameters,
                        "static_rms": float(np.sqrt(np.mean(np.sum(quiet**2, axis=1)))),
                        "small_step_90_ms": float(crossed[0] * 1000 / fps)
                        if len(crossed)
                        else None,
                    }
                )
    return {
        "seeds": seeds,
        "records": records,
        "auto_applied": False,
        "note": (
            "Synthetic mechanisms only. Same noise realization for all variants in each seed/fps "
            "block. Not participant accuracy or end-to-end camera latency."
        ),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    result = run()
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"Saved {len(result['records'])} paired synthetic runs to {args.output}")
