"""Gaze-session report and heatmap generation."""

from __future__ import annotations

import hashlib
import html
import json
import mimetypes
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pyarrow.parquet as pq

from oculidoc.application.experiment_session_service import (
    DuplicateSessionArtifactError,
    ExperimentSessionService,
    RegisterSessionArtifactRequest,
)
from oculidoc.domain.experiment_session import (
    ExperimentSessionStatus,
    SessionArtifactKind,
)


@dataclass(frozen=True, slots=True)
class GazeReportArtifacts:
    """Paths produced for one gaze-session report."""

    report_directory: Path
    report_json_path: Path
    html_path: Path
    screen_heatmap_path: Path
    semantic_aoi_path: Path
    tracking_error_path: Path | None


def _finite_float(
    value: object,
) -> float | None:
    if isinstance(value, bool):
        return None

    if not isinstance(value, (int, float)):
        return None

    normalized = float(value)

    if not np.isfinite(normalized):
        return None

    return normalized


def _atomic_write_text(
    path: Path,
    text: str,
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    temporary_path = path.with_name(f".{path.name}.tmp")

    try:
        temporary_path.write_text(
            text,
            encoding="utf-8",
        )
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _atomic_write_json(
    path: Path,
    payload: dict[str, object],
) -> None:
    _atomic_write_text(
        path,
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )


def _atomic_save_figure(
    figure: Any,
    path: Path,
) -> None:
    temporary_path = path.with_name(f".{path.name}.tmp")

    try:
        figure.savefig(
            temporary_path,
            format="png",
            dpi=160,
            bbox_inches="tight",
        )
        temporary_path.replace(path)
    finally:
        plt.close(figure)
        temporary_path.unlink(missing_ok=True)


def _sha256(
    path: Path,
) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as stream:
        for chunk in iter(
            lambda: stream.read(1024 * 1024),
            b"",
        ):
            digest.update(chunk)

    return digest.hexdigest()


def _register_artifact(
    service: ExperimentSessionService,
    *,
    session_id: UUID,
    session_directory: Path,
    path: Path,
) -> None:
    relative_path = path.resolve().relative_to(session_directory.resolve()).as_posix()

    try:
        service.register_artifact(
            RegisterSessionArtifactRequest(
                session_id=session_id,
                kind=SessionArtifactKind.OTHER,
                relative_path=relative_path,
                source="gaze_report",
                mime_type=(mimetypes.guess_type(path.name)[0]),
                size_bytes=path.stat().st_size,
                sha256=_sha256(path),
            )
        )
    except DuplicateSessionArtifactError:
        return


def _load_rows(
    session_directory: Path,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []

    for gaze_path in sorted(session_directory.glob("tasks/*/gaze_events.parquet")):
        table = pq.read_table(gaze_path)
        rows.extend(table.to_pylist())

    return rows


def _load_event_counts(
    run_directory: Path,
) -> dict[str, int]:
    event_path = run_directory / "task_events.jsonl"
    counts: dict[str, int] = {}

    if not event_path.is_file():
        return counts

    try:
        lines = event_path.read_text(
            encoding="utf-8",
        ).splitlines()
    except (
        OSError,
        UnicodeDecodeError,
    ):
        return counts

    for line in lines:
        if not line.strip():
            continue

        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        if not isinstance(event, dict):
            continue

        event_type = str(
            event.get(
                "event_type",
                "",
            )
        ).strip()

        if event_type:
            counts[event_type] = (
                counts.get(
                    event_type,
                    0,
                )
                + 1
            )

    return dict(sorted(counts.items()))


def _load_task_results(
    session_directory: Path,
) -> list[dict[str, object]]:
    task_results: list[dict[str, object]] = []

    for result_path in sorted(session_directory.glob("tasks/*/task_result.json")):
        try:
            payload = json.loads(
                result_path.read_text(
                    encoding="utf-8",
                )
            )
        except (
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as error:
            task_results.append(
                {
                    "run_id": result_path.parent.name,
                    "source_path": (result_path.relative_to(session_directory).as_posix()),
                    "load_error": str(error),
                    "event_counts": (_load_event_counts(result_path.parent)),
                }
            )
            continue

        if not isinstance(payload, dict):
            task_results.append(
                {
                    "run_id": result_path.parent.name,
                    "source_path": (result_path.relative_to(session_directory).as_posix()),
                    "load_error": ("task_result.json root must be an object."),
                    "event_counts": (_load_event_counts(result_path.parent)),
                }
            )
            continue

        summary = payload.get("summary")
        result = payload.get("result")
        task_results.append(
            {
                "run_id": str(payload.get("run_id") or result_path.parent.name),
                "task_kind": (
                    str(payload.get("task_kind")) if payload.get("task_kind") is not None else None
                ),
                "end_reason": (
                    str(payload.get("end_reason"))
                    if payload.get("end_reason") is not None
                    else None
                ),
                "source_path": (result_path.relative_to(session_directory).as_posix()),
                "summary": (dict(summary) if isinstance(summary, dict) else {}),
                "result": (dict(result) if isinstance(result, dict) else {}),
                "event_counts": (_load_event_counts(result_path.parent)),
            }
        )

    return task_results


def _display_value(
    value: object,
    *,
    suffix: str = "",
) -> str:
    if value is None:
        return "-"

    if isinstance(value, bool):
        return "是" if value else "否"

    if isinstance(value, float):
        return f"{value:.3f}{suffix}"

    return f"{value}{suffix}"


def _display_ratio(
    value: object,
) -> str:
    number = _finite_float(value)

    if number is None:
        return "-"

    return f"{number:.1%}"


def _display_ms(
    value: object,
) -> str:
    number = _finite_float(value)

    if number is None:
        return "-"

    return f"{number:.0f} ms"


def _task_result_rows(
    task_record: dict[str, object],
) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    result_value = task_record.get("result")
    result = result_value if isinstance(result_value, dict) else {}
    task_kind = task_record.get("task_kind")
    end_reason = task_record.get("end_reason")
    rows.extend(
        (
            (
                "运行 ID",
                _display_value(task_record.get("run_id")),
            ),
            (
                "任务类型",
                _display_value(task_kind),
            ),
            (
                "结束原因",
                _display_value(end_reason),
            ),
            (
                "完成状态",
                _display_value(result.get("completion_status")),
            ),
        )
    )

    is_binary = any(
        key in result
        for key in (
            "question",
            "question_type",
            "selected_option_id",
            "selected_answer",
        )
    )

    if is_binary:
        correct = result.get("correct")

        if result.get("is_scored") is False:
            correctness = "不评分"
        elif correct is True:
            correctness = "正确"
        elif correct is False:
            correctness = "错误"
        else:
            correctness = "-"

        rows.extend(
            (
                (
                    "问题",
                    _display_value(result.get("question")),
                ),
                (
                    "问题类型",
                    _display_value(result.get("question_type")),
                ),
                (
                    "患者选择",
                    _display_value(result.get("selected_answer")),
                ),
                (
                    "逻辑选项",
                    _display_value(result.get("selected_option_id")),
                ),
                (
                    "显示侧",
                    _display_value(result.get("selected_side")),
                ),
                (
                    "评分结果",
                    correctness,
                ),
                (
                    "反应时间",
                    _display_ms(result.get("reaction_time_ms")),
                ),
                (
                    "确认停留",
                    _display_ms(result.get("confirmation_dwell_ms")),
                ),
            )
        )

    is_tracking = task_kind == "tracking_ball" or any(
        key in result
        for key in (
            "target_hit_ratio",
            "tracking_error_normalized",
            "first_target_acquired_ms",
        )
    )

    if is_tracking:
        normalized_error = result.get("tracking_error_normalized")

        if not isinstance(
            normalized_error,
            dict,
        ):
            normalized_error = {}

        pixel_error = result.get("tracking_error_px")

        if not isinstance(
            pixel_error,
            dict,
        ):
            pixel_error = {}

        rows.extend(
            (
                (
                    "有效样本率",
                    _display_ratio(result.get("valid_sample_ratio")),
                ),
                (
                    "目标命中率",
                    _display_ratio(result.get("target_hit_ratio")),
                ),
                (
                    "命中时长占比",
                    _display_ratio(result.get("target_hit_duration_ratio")),
                ),
                (
                    "首次进入目标",
                    _display_ms(result.get("first_target_entry_ms")),
                ),
                (
                    "首次稳定获得",
                    _display_ms(result.get("first_target_acquired_ms")),
                ),
                (
                    "最长连续追踪",
                    _display_ms(result.get("longest_continuous_tracking_ms")),
                ),
                (
                    "目标丢失次数",
                    _display_value(result.get("target_loss_count")),
                ),
                (
                    "重新获得次数",
                    _display_value(result.get("target_reacquisition_count")),
                ),
                (
                    "平均标准化误差",
                    _display_value(normalized_error.get("mean")),
                ),
                (
                    "中位标准化误差",
                    _display_value(normalized_error.get("median")),
                ),
                (
                    "P95 标准化误差",
                    _display_value(normalized_error.get("p95")),
                ),
                (
                    "平均像素误差",
                    _display_value(
                        pixel_error.get("mean"),
                        suffix=" px",
                    ),
                ),
            )
        )

    event_counts = task_record.get("event_counts")

    if isinstance(event_counts, dict):
        event_text = "、".join(
            f"{event_type}: {count}" for event_type, count in sorted(event_counts.items())
        )
        rows.append(
            (
                "事件计数",
                event_text or "-",
            )
        )

    load_error = task_record.get("load_error")

    if load_error is not None:
        rows.append(
            (
                "读取错误",
                str(load_error),
            )
        )

    return rows


def _task_result_sections(
    task_results: list[dict[str, object]],
) -> str:
    if not task_results:
        return "<section><h2>结构化任务结果</h2><p>没有可读取的 task_result.json。</p></section>"

    cards: list[str] = []

    for index, task_record in enumerate(
        task_results,
        start=1,
    ):
        table_rows = "\n".join(
            ("<tr><th>" + html.escape(label) + "</th><td>" + html.escape(value) + "</td></tr>")
            for label, value in _task_result_rows(task_record)
        )
        cards.append(
            '<article class="task-result">'
            f"<h3>任务结果 {index}</h3>"
            f"<table>{table_rows}</table>"
            "</article>"
        )

    return "<section><h2>结构化任务结果</h2>" + "\n".join(cards) + "</section>"


def _build_metrics(
    rows: list[dict[str, object]],
) -> dict[str, object]:
    valid_points: list[tuple[float, float, float]] = []
    dwell_by_role_ms: dict[
        str,
        float,
    ] = {}
    tracking_errors: list[float] = []
    tracking_inside_count = 0
    tracking_sample_count = 0
    question_ids: set[str] = set()

    for row in rows:
        question_id = row.get("question_id")

        if question_id is not None:
            question_ids.add(str(question_id))

        x = _finite_float(row.get("gaze_x_normalized"))
        y = _finite_float(row.get("gaze_y_normalized"))
        valid = bool(row.get("analysis_valid"))

        if not valid or x is None or y is None or not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            continue

        duration_ms = max(
            0.0,
            _finite_float(row.get("duration_ms")) or 0.0,
        )
        weight = duration_ms if duration_ms > 0 else 1.0
        valid_points.append((x, y, weight))

        role = str(row.get("aoi_role") or "non_option")
        dwell_by_role_ms[role] = (
            dwell_by_role_ms.get(
                role,
                0.0,
            )
            + duration_ms
        )

        left = _finite_float(row.get("reference_aoi_left"))
        top = _finite_float(row.get("reference_aoi_top"))
        right = _finite_float(row.get("reference_aoi_right"))
        bottom = _finite_float(row.get("reference_aoi_bottom"))

        if None in {
            left,
            top,
            right,
            bottom,
        }:
            continue

        assert left is not None
        assert top is not None
        assert right is not None
        assert bottom is not None

        if not (right >= left and bottom >= top):
            continue

        center_x = (left + right) / 2.0
        center_y = (top + bottom) / 2.0
        tracking_errors.append(
            float(
                np.hypot(
                    x - center_x,
                    y - center_y,
                )
            )
        )
        tracking_sample_count += 1

        if left <= x <= right and top <= y <= bottom:
            tracking_inside_count += 1

    sample_count = len(rows)
    valid_count = len(valid_points)
    valid_ratio = valid_count / sample_count if sample_count else 0.0
    total_dwell_ms = sum(dwell_by_role_ms.values())
    dwell_ratio_by_role = {
        role: (duration / total_dwell_ms if total_dwell_ms > 0 else 0.0)
        for role, duration in sorted(dwell_by_role_ms.items())
    }

    gaze_centroid: (
        dict[
            str,
            float,
        ]
        | None
    ) = None

    if valid_points:
        x_values = np.array(
            [point[0] for point in valid_points],
            dtype=float,
        )
        y_values = np.array(
            [point[1] for point in valid_points],
            dtype=float,
        )
        weights = np.array(
            [point[2] for point in valid_points],
            dtype=float,
        )
        gaze_centroid = {
            "x": float(
                np.average(
                    x_values,
                    weights=weights,
                )
            ),
            "y": float(
                np.average(
                    y_values,
                    weights=weights,
                )
            ),
        }

    tracking_metrics: (
        dict[
            str,
            object,
        ]
        | None
    ) = None

    if tracking_errors:
        errors = np.array(
            tracking_errors,
            dtype=float,
        )
        tracking_metrics = {
            "sample_count": (tracking_sample_count),
            "mean_error_normalized": float(np.mean(errors)),
            "median_error_normalized": float(np.median(errors)),
            "p95_error_normalized": float(
                np.percentile(
                    errors,
                    95,
                )
            ),
            "target_hit_ratio": (
                tracking_inside_count / tracking_sample_count if tracking_sample_count else 0.0
            ),
        }

    correct_dwell = dwell_by_role_ms.get(
        "correct_option",
        0.0,
    )
    incorrect_dwell = dwell_by_role_ms.get(
        "incorrect_option",
        0.0,
    )
    non_option_dwell = dwell_by_role_ms.get(
        "non_option",
        0.0,
    )
    option_dwell = correct_dwell + incorrect_dwell
    binary_metrics = {
        "correct_option_dwell_ms": (correct_dwell),
        "incorrect_option_dwell_ms": (incorrect_dwell),
        "non_option_dwell_ms": (non_option_dwell),
        "option_dwell_ms": option_dwell,
        "correct_option_share": (correct_dwell / option_dwell if option_dwell > 0 else None),
    }

    return {
        "sample_count": sample_count,
        "valid_sample_count": valid_count,
        "invalid_sample_count": (sample_count - valid_count),
        "valid_sample_ratio": valid_ratio,
        "question_count": len(question_ids),
        "gaze_centroid_normalized": (gaze_centroid),
        "dwell_by_role_ms": {
            role: round(
                duration,
                3,
            )
            for role, duration in sorted(dwell_by_role_ms.items())
        },
        "dwell_ratio_by_role": {
            role: round(
                ratio,
                6,
            )
            for role, ratio in dwell_ratio_by_role.items()
        },
        "tracking": tracking_metrics,
        "binary": binary_metrics,
        "_valid_points": valid_points,
        "_tracking_errors": (tracking_errors),
    }


def _screen_heatmap(
    valid_points: list[tuple[float, float, float]],
    output_path: Path,
) -> None:
    figure, axis = plt.subplots(figsize=(10, 6))

    if valid_points:
        x_values = np.array(
            [point[0] for point in valid_points],
            dtype=float,
        )
        y_values = np.array(
            [point[1] for point in valid_points],
            dtype=float,
        )
        weights = np.array(
            [point[2] for point in valid_points],
            dtype=float,
        )
        density, _, _ = np.histogram2d(
            y_values,
            x_values,
            bins=(54, 96),
            range=(
                (0.0, 1.0),
                (0.0, 1.0),
            ),
            weights=weights,
        )
        image = axis.imshow(
            density,
            origin="upper",
            extent=(
                0.0,
                1.0,
                1.0,
                0.0,
            ),
            aspect="auto",
        )
        figure.colorbar(
            image,
            ax=axis,
            label="Weighted dwell",
        )
    else:
        axis.text(
            0.5,
            0.5,
            "No valid gaze samples",
            ha="center",
            va="center",
        )

    axis.set_title("Screen-space gaze density")
    axis.set_xlabel("Normalized screen X")
    axis.set_ylabel("Normalized screen Y")
    axis.set_xlim(0.0, 1.0)
    axis.set_ylim(1.0, 0.0)
    _atomic_save_figure(
        figure,
        output_path,
    )


def _semantic_aoi_plot(
    dwell_by_role_ms: dict[
        str,
        float,
    ],
    output_path: Path,
) -> None:
    figure, axis = plt.subplots(figsize=(9, 5))
    roles = list(dwell_by_role_ms.keys())
    values = [dwell_by_role_ms[role] for role in roles]

    if roles:
        axis.bar(
            roles,
            values,
        )
        axis.tick_params(
            axis="x",
            rotation=20,
        )
    else:
        axis.text(
            0.5,
            0.5,
            "No AOI dwell data",
            ha="center",
            va="center",
        )

    axis.set_title("Semantic AOI dwell")
    axis.set_ylabel("Dwell duration (ms)")
    _atomic_save_figure(
        figure,
        output_path,
    )


def _tracking_error_plot(
    errors: list[float],
    output_path: Path,
) -> None:
    figure, axis = plt.subplots(figsize=(9, 5))
    axis.hist(
        errors,
        bins=min(
            30,
            max(
                5,
                int(np.sqrt(len(errors))),
            ),
        ),
    )
    axis.set_title("Tracking error distribution")
    axis.set_xlabel("Normalized gaze-target distance")
    axis.set_ylabel("Samples")
    _atomic_save_figure(
        figure,
        output_path,
    )


def _metric_rows(
    metrics: dict[str, object],
) -> str:
    rows = [
        (
            "Samples",
            metrics["sample_count"],
        ),
        (
            "Valid samples",
            metrics["valid_sample_count"],
        ),
        (
            "Valid ratio",
            f"{float(metrics['valid_sample_ratio']):.1%}",
        ),
        (
            "Questions",
            metrics["question_count"],
        ),
    ]
    tracking = metrics.get("tracking")

    if isinstance(tracking, dict):
        rows.extend(
            [
                (
                    "Mean tracking error",
                    (f"{float(tracking['mean_error_normalized']):.4f}"),
                ),
                (
                    "Median tracking error",
                    (f"{float(tracking['median_error_normalized']):.4f}"),
                ),
                (
                    "Target hit ratio",
                    (f"{float(tracking['target_hit_ratio']):.1%}"),
                ),
            ]
        )

    binary = metrics.get("binary")

    if isinstance(binary, dict):
        correct_share = binary.get("correct_option_share")
        rows.extend(
            [
                (
                    "Correct-option dwell",
                    (f"{float(binary['correct_option_dwell_ms']):.0f} ms"),
                ),
                (
                    "Incorrect-option dwell",
                    (f"{float(binary['incorrect_option_dwell_ms']):.0f} ms"),
                ),
                (
                    "Non-option dwell",
                    (f"{float(binary['non_option_dwell_ms']):.0f} ms"),
                ),
                (
                    "Correct-option share",
                    (f"{float(correct_share):.1%}" if correct_share is not None else "-"),
                ),
            ]
        )

    return "\n".join(
        (f"<tr><th>{html.escape(str(label))}</th><td>{html.escape(str(value))}</td></tr>")
        for label, value in rows
    )


def _html_document(
    *,
    patient_id: UUID,
    session_id: UUID,
    module_id: str,
    generated_at: str,
    metrics: dict[str, object],
    task_results: list[dict[str, object]],
    has_tracking_plot: bool,
) -> str:
    task_result_html = _task_result_sections(task_results)
    tracking_image = (
        "<section><h2>Tracking error</h2>"
        '<img src="tracking_error.png" '
        'alt="Tracking error"></section>'
        if has_tracking_plot
        else ""
    )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>OculiDoC Gaze Report</title>
<style>
body {{
    font-family: "Microsoft YaHei UI", Arial, sans-serif;
    margin: 32px auto;
    max-width: 1080px;
    color: #17324d;
}}
header, section {{
    background: #ffffff;
    border: 1px solid #d9e3ec;
    border-radius: 12px;
    padding: 20px;
    margin-bottom: 18px;
}}
body {{ background: #eef3f8; }}
img {{ width: 100%; height: auto; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{
    border-bottom: 1px solid #d9e3ec;
    padding: 9px;
    text-align: left;
}}
.notice {{
    color: #7a4e00;
    background: #fff7df;
}}
code {{ word-break: break-all; }}
</style>
</head>
<body>
<header>
<h1>OculiDoC 眼动结果报告</h1>
<p>患者 ID：<code>{html.escape(str(patient_id))}</code></p>
<p>会话 ID：<code>{html.escape(str(session_id))}</code></p>
<p>任务：{html.escape(module_id)}</p>
<p>生成时间：{html.escape(generated_at)}</p>
</header>
<section>
<h2>核心指标</h2>
<table>{_metric_rows(metrics)}</table>
</section>
{task_result_html}
<section>
<h2>屏幕空间热图</h2>
<img src="screen_heatmap.png" alt="Screen heatmap">
</section>
<section>
<h2>语义 AOI 汇总</h2>
<img src="semantic_aoi.png" alt="Semantic AOI dwell">
</section>
{tracking_image}
<section class="notice">
<strong>用途声明：</strong>
本报告用于研究与临床辅助观察，不能单独作为意识状态诊断、
治疗决策或预后判断依据。应结合 CRS-R、神经电生理、影像学、
床旁观察与患者基础状态综合解释。
</section>
</body>
</html>
"""


def generate_gaze_session_report(
    service: ExperimentSessionService,
    session_id: UUID,
) -> GazeReportArtifacts:
    """Generate and register a report for one completed session."""

    session = service.get_session(session_id)

    if session.status is not ExperimentSessionStatus.COMPLETED:
        raise ValueError("Only completed sessions can generate reports.")

    session_directory = service.resolve_session_directory(session_id)
    rows = _load_rows(session_directory)
    task_results = _load_task_results(session_directory)

    if not rows:
        raise ValueError("No gaze_events.parquet rows were found.")

    generated_at = datetime.now(UTC)
    report_id = generated_at.strftime("%Y%m%dT%H%M%S%fZ")
    report_directory = session_directory / "reports" / f"gaze_mvp_{report_id}"
    report_directory.mkdir(
        parents=True,
        exist_ok=False,
    )

    metrics = _build_metrics(rows)
    valid_points = metrics.pop("_valid_points")
    tracking_errors = metrics.pop("_tracking_errors")

    assert isinstance(
        valid_points,
        list,
    )
    assert isinstance(
        tracking_errors,
        list,
    )

    screen_heatmap_path = report_directory / "screen_heatmap.png"
    semantic_aoi_path = report_directory / "semantic_aoi.png"
    tracking_error_path = report_directory / "tracking_error.png" if tracking_errors else None
    report_json_path = report_directory / "report.json"
    html_path = report_directory / "report.html"

    _screen_heatmap(
        valid_points,
        screen_heatmap_path,
    )
    _semantic_aoi_plot(
        dict(metrics["dwell_by_role_ms"]),
        semantic_aoi_path,
    )

    if tracking_error_path is not None:
        _tracking_error_plot(
            tracking_errors,
            tracking_error_path,
        )

    generated_at_text = generated_at.isoformat()
    report_document = {
        "schema_version": "1.1",
        "task_results": task_results,
        "generated_at_utc": (generated_at_text),
        "patient_id": str(session.patient_id),
        "session_id": str(session.session_id),
        "module_id": session.module_id,
        "metrics": metrics,
        "clinical_use_notice": (
            "Research and clinical-assistive use only; not a standalone diagnosis."
        ),
    }
    _atomic_write_json(
        report_json_path,
        report_document,
    )
    _atomic_write_text(
        html_path,
        _html_document(
            patient_id=(session.patient_id),
            session_id=(session.session_id),
            module_id=(session.module_id),
            generated_at=(generated_at_text),
            metrics=metrics,
            task_results=task_results,
            has_tracking_plot=(tracking_error_path is not None),
        ),
    )

    produced_paths = [
        report_json_path,
        html_path,
        screen_heatmap_path,
        semantic_aoi_path,
    ]

    if tracking_error_path is not None:
        produced_paths.append(tracking_error_path)

    for path in produced_paths:
        _register_artifact(
            service,
            session_id=session_id,
            session_directory=(session_directory),
            path=path,
        )

    return GazeReportArtifacts(
        report_directory=(report_directory),
        report_json_path=(report_json_path),
        html_path=html_path,
        screen_heatmap_path=(screen_heatmap_path),
        semantic_aoi_path=(semantic_aoi_path),
        tracking_error_path=(tracking_error_path),
    )
