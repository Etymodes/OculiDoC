from __future__ import annotations

import hashlib
import html
import json
import mimetypes
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any
from uuid import UUID

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt

from oculidoc.application.experiment_session_service import (
    DuplicateSessionArtifactError,
    ExperimentSessionService,
    RegisterSessionArtifactRequest,
)
from oculidoc.application.gaze_report import (
    _build_metrics,
    _load_rows,
    _screen_heatmap,
)
from oculidoc.application.session_history import (
    SessionHistoryEntry,
    build_patient_session_history,
)
from oculidoc.domain.experiment_session import (
    ExperimentSessionStatus,
    SessionArtifactKind,
)
from oculidoc.modules.registry import DEFAULT_MODULES

LOW_VALID_SAMPLE_RATIO = 0.60
LOW_SAMPLE_COUNT = 10

_MODULE_TITLES = {module.module_id: module.title for module in DEFAULT_MODULES}

_COMPARABLE_METRICS = (
    "valid_sample_ratio",
    "target_hit_ratio",
    "target_hit_duration_ratio",
    "first_target_acquired_ms",
    "longest_continuous_tracking_ms",
    "target_loss_count",
    "target_reacquisition_count",
    "tracking_error_mean_normalized",
    "reaction_time_ms",
    "confirmation_dwell_ms",
    "target_acquisition_ratio",
    "mean_first_target_acquired_ms",
    "no_target_false_fixation_count",
)


@dataclass(frozen=True, slots=True)
class PatientTrendArtifacts:
    report_directory: Path
    report_json_path: Path
    html_path: Path
    data_quality_path: Path | None
    aggregate_heatmap_path: Path | None
    tracking_path: Path | None
    binary_path: Path | None


def _safe_number(
    value: object,
) -> float | None:
    if value is None or isinstance(value, bool):
        return None

    try:
        number = float(value)
    except (
        TypeError,
        ValueError,
    ):
        return None

    if not (float("-inf") < number < float("inf")):
        return None

    return number


def _safe_integer(
    value: object,
) -> int | None:
    number = _safe_number(value)

    if number is None:
        return None

    return max(
        0,
        int(number),
    )


def _result_mapping(
    task_record: dict[str, object],
) -> dict[str, object]:
    result = task_record.get("result")

    if not isinstance(result, dict):
        return {}

    return dict(result)


def _metric(
    result: dict[str, object],
    key: str,
) -> float | None:
    return _safe_number(result.get(key))


def _normalized_error_mean(
    result: dict[str, object],
) -> float | None:
    value = result.get("tracking_error_normalized")

    if not isinstance(value, dict):
        return None

    return _safe_number(value.get("mean"))


def _warning(
    code: str,
    severity: str,
    message: str,
) -> dict[str, str]:
    return {
        "code": code,
        "severity": severity,
        "message": message,
    }


def _point_warnings(
    entry: SessionHistoryEntry,
    result: dict[str, object],
    *,
    sample_count: int | None,
    valid_sample_ratio: float | None,
) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []

    if entry.status is not ExperimentSessionStatus.COMPLETED:
        warnings.append(
            _warning(
                "session_not_completed",
                "warning",
                ("该会话未以完成状态结束，不应直接用于疗效趋势判断。"),
            )
        )

    if not result:
        warnings.append(
            _warning(
                "missing_structured_result",
                "warning",
                ("未发现可读取的结构化任务结果。"),
            )
        )

    if result.get("recording_failed") is True:
        warnings.append(
            _warning(
                "recording_failed",
                "error",
                ("任务界面结束，但记录过程报告失败。"),
            )
        )

    completion_status = str(result.get("completion_status") or "").strip()

    if completion_status in {
        "interrupted",
        "unanswered",
    }:
        warnings.append(
            _warning(
                "incomplete_task",
                "warning",
                (f"任务未形成完整可解释结果：{completion_status}。"),
            )
        )

    if valid_sample_ratio is None:
        warnings.append(
            _warning(
                "missing_valid_ratio",
                "info",
                ("缺少有效样本率，无法评估本次数据完整性。"),
            )
        )
    elif valid_sample_ratio < LOW_VALID_SAMPLE_RATIO:
        warnings.append(
            _warning(
                "low_valid_sample_ratio",
                "warning",
                (f"有效样本率低于 {LOW_VALID_SAMPLE_RATIO:.0%}，趋势指标可信度可能受限。"),
            )
        )

    if sample_count is not None and sample_count < LOW_SAMPLE_COUNT:
        warnings.append(
            _warning(
                "low_sample_count",
                "info",
                ("有效比较所依据的样本数较少。"),
            )
        )

    return warnings


def _build_point(
    entry: SessionHistoryEntry,
    task_record: dict[str, object],
    *,
    task_index: int,
) -> dict[str, object]:
    result = _result_mapping(task_record)
    task_kind = str(task_record.get("task_kind") or entry.module_id)
    sample_count = _safe_integer(result.get("sample_count")) or entry.sample_count
    valid_sample_ratio = (
        _metric(
            result,
            "valid_sample_ratio",
        )
        if result
        else None
    )

    if valid_sample_ratio is None:
        valid_sample_ratio = entry.valid_sample_ratio

    metrics: dict[
        str,
        object,
    ] = {
        "sample_count": sample_count,
        "valid_sample_ratio": (valid_sample_ratio),
    }
    metric_family = "general"

    is_tracking = task_kind == "tracking_ball" or any(
        key in result
        for key in (
            "target_hit_ratio",
            "tracking_error_normalized",
            "first_target_acquired_ms",
        )
    )

    if is_tracking:
        metric_family = "tracking"
        metrics.update(
            {
                "target_hit_ratio": _metric(
                    result,
                    "target_hit_ratio",
                ),
                "target_hit_duration_ratio": (
                    _metric(
                        result,
                        "target_hit_duration_ratio",
                    )
                ),
                "first_target_entry_ms": (
                    _metric(
                        result,
                        "first_target_entry_ms",
                    )
                ),
                "first_target_acquired_ms": (
                    _metric(
                        result,
                        "first_target_acquired_ms",
                    )
                ),
                "longest_continuous_tracking_ms": (
                    _metric(
                        result,
                        "longest_continuous_tracking_ms",
                    )
                ),
                "target_loss_count": (_safe_integer(result.get("target_loss_count"))),
                "target_reacquisition_count": (
                    _safe_integer(result.get("target_reacquisition_count"))
                ),
                "tracking_error_mean_normalized": (_normalized_error_mean(result)),
            }
        )

    if task_kind == "instruction_fixation" or "target_acquisition_ratio" in result:
        metric_family = "instruction_fixation"
        metrics.update(
            {
                "target_acquisition_ratio": _metric(
                    result,
                    "target_acquisition_ratio",
                ),
                "mean_first_target_entry_ms": _metric(
                    result,
                    "mean_first_target_entry_ms",
                ),
                "mean_first_target_acquired_ms": _metric(
                    result,
                    "mean_first_target_acquired_ms",
                ),
                "longest_continuous_target_fixation_ms": _metric(
                    result,
                    "longest_continuous_target_fixation_ms",
                ),
                "no_target_false_fixation_count": _safe_integer(
                    result.get("no_target_false_fixation_count")
                ),
                "distractor_fixation_count": _safe_integer(result.get("distractor_fixation_count")),
            }
        )

    is_binary = any(
        key in result
        for key in (
            "question",
            "selected_option_id",
            "selected_answer",
            "reaction_time_ms",
        )
    )

    if is_binary:
        metric_family = "binary"
        metrics.update(
            {
                "answered": (result.get("selected_option_id") is not None),
                "is_scored": (result.get("is_scored")),
                "correct": (result.get("correct")),
                "reaction_time_ms": (
                    _metric(
                        result,
                        "reaction_time_ms",
                    )
                ),
                "confirmation_dwell_ms": (
                    _metric(
                        result,
                        "confirmation_dwell_ms",
                    )
                ),
                "question_type": (result.get("question_type")),
                "selected_answer": (result.get("selected_answer")),
            }
        )

    warnings = _point_warnings(
        entry,
        result,
        sample_count=sample_count,
        valid_sample_ratio=(valid_sample_ratio),
    )
    completion_status = result.get("completion_status")
    usable_for_trend = (
        entry.status is ExperimentSessionStatus.COMPLETED
        and result.get("recording_failed") is not True
        and completion_status
        not in {
            "interrupted",
            "unanswered",
        }
    )

    return {
        "session_id": str(entry.session_id),
        "task_index": task_index,
        "module_id": entry.module_id,
        "task_kind": task_kind,
        "metric_family": metric_family,
        "created_at_utc": (entry.created_at.astimezone(UTC).isoformat()),
        "started_at_utc": (
            entry.started_at.astimezone(UTC).isoformat() if entry.started_at is not None else None
        ),
        "ended_at_utc": (
            entry.ended_at.astimezone(UTC).isoformat() if entry.ended_at is not None else None
        ),
        "session_status": (entry.status.value),
        "completion_status": (completion_status),
        "completion_reason": (result.get("completion_reason") or task_record.get("end_reason")),
        "metrics": metrics,
        "quality_warnings": warnings,
        "usable_for_trend": (usable_for_trend),
        "comparison": None,
    }


def _add_comparisons(
    points: list[dict[str, object]],
) -> None:
    previous_by_key: dict[
        tuple[str, str],
        dict[str, object],
    ] = {}

    for point in points:
        if not point.get("usable_for_trend"):
            continue

        key = (
            str(point.get("module_id")),
            str(point.get("metric_family")),
        )
        previous = previous_by_key.get(key)

        if previous is not None:
            current_metrics = point.get("metrics")
            previous_metrics = previous.get("metrics")

            if isinstance(
                current_metrics,
                dict,
            ) and isinstance(
                previous_metrics,
                dict,
            ):
                deltas: dict[
                    str,
                    float,
                ] = {}

                for metric_name in _COMPARABLE_METRICS:
                    current = _safe_number(current_metrics.get(metric_name))
                    prior = _safe_number(previous_metrics.get(metric_name))

                    if current is not None and prior is not None:
                        deltas[metric_name] = current - prior

                point["comparison"] = {
                    "previous_session_id": (previous.get("session_id")),
                    "delta": deltas,
                }

        previous_by_key[key] = point


def build_patient_trend_document(
    service: ExperimentSessionService,
    patient_id: UUID,
    *,
    anchor_session_id: UUID | None = None,
) -> dict[str, object]:
    """Build a neutral longitudinal summary.

    Deltas are descriptive only. The function does not label
    a change as clinical improvement or deterioration.
    """

    patient = service.get_patient(patient_id)

    entries = list(
        build_patient_session_history(
            service,
            patient_id,
        )
    )
    entries.reverse()
    points: list[dict[str, object]] = []

    for entry in entries:
        task_results = (
            entry.task_results
            if entry.task_results
            else (
                {
                    "task_kind": (entry.module_id),
                    "result": {},
                },
            )
        )

        for task_index, task_record in enumerate(
            task_results,
            start=1,
        ):
            points.append(
                _build_point(
                    entry,
                    dict(task_record),
                    task_index=task_index,
                )
            )

    _add_comparisons(points)

    modules: dict[
        str,
        dict[str, object],
    ] = {}

    for point in points:
        module_id = str(point["module_id"])
        module = modules.setdefault(
            module_id,
            {
                "module_id": module_id,
                "session_count": 0,
                "usable_point_count": 0,
                "points": [],
            },
        )
        module_points = module["points"]

        assert isinstance(
            module_points,
            list,
        )
        module_points.append(point)
        module["session_count"] = len({item["session_id"] for item in module_points})
        module["usable_point_count"] = sum(
            1 for item in module_points if item.get("usable_for_trend")
        )

    warning_counts: dict[
        str,
        int,
    ] = {}

    for point in points:
        warnings = point.get("quality_warnings")

        if not isinstance(
            warnings,
            list,
        ):
            continue

        for warning in warnings:
            if not isinstance(
                warning,
                dict,
            ):
                continue

            code = str(warning.get("code"))
            warning_counts[code] = (
                warning_counts.get(
                    code,
                    0,
                )
                + 1
            )

    return {
        "schema_version": "1.1",
        "generated_at_utc": (datetime.now(UTC).isoformat()),
        "patient_id": str(patient_id),
        "patient_name": patient.family_name,
        "patient_code": patient.patient_code,
        "patient_display_label": patient.display_label,
        "anchor_session_id": (str(anchor_session_id) if anchor_session_id is not None else None),
        "session_count": len(entries),
        "point_count": len(points),
        "usable_point_count": sum(1 for point in points if point.get("usable_for_trend")),
        "quality_warning_counts": (dict(sorted(warning_counts.items()))),
        "modules": modules,
        "interpretation_policy": {
            "delta_is_descriptive": True,
            "automatic_improvement_label": False,
            "cross_task_comparison": False,
        },
        "clinical_use_notice": (
            "本纵向摘要仅用于研究和临床辅助观察。"
            "不同任务参数、患者觉醒度、药物、疲劳、"
            "视听条件和设备质量均可能影响结果；"
            "变化值不能单独解释为意识水平改善或恶化。"
        ),
    }


def _flatten_points(
    document: dict[str, object],
) -> list[dict[str, object]]:
    modules = document.get("modules")

    if not isinstance(modules, dict):
        return []

    points: list[dict[str, object]] = []

    for module in modules.values():
        if not isinstance(
            module,
            dict,
        ):
            continue

        module_points = module.get("points")

        if not isinstance(
            module_points,
            list,
        ):
            continue

        points.extend(
            point
            for point in module_points
            if isinstance(
                point,
                dict,
            )
        )

    points.sort(key=lambda point: str(point.get("created_at_utc") or ""))
    return points


def _atomic_write_text(
    path: Path,
    content: str,
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        delete=False,
    ) as temporary:
        temporary.write(content)
        temporary_path = Path(temporary.name)

    os.replace(
        temporary_path,
        path,
    )


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
    output_path: Path,
) -> None:
    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with NamedTemporaryFile(
        prefix=f".{output_path.stem}.",
        suffix=output_path.suffix,
        dir=output_path.parent,
        delete=False,
    ) as temporary:
        temporary_path = Path(temporary.name)

    try:
        figure.savefig(
            temporary_path,
            dpi=150,
            bbox_inches="tight",
        )
        os.replace(
            temporary_path,
            output_path,
        )
    finally:
        plt.close(figure)
        temporary_path.unlink(missing_ok=True)


def _labels(
    points: list[dict[str, object]],
) -> list[str]:
    labels: list[str] = []

    for index, point in enumerate(points, start=1):
        created_at = str(point.get("created_at_utc") or "")
        labels.append(created_at[:10] if created_at else f"记录{index}")

    return labels


def _data_quality_plot(
    points: list[dict[str, object]],
    output_path: Path,
) -> bool:
    selected = []

    for point in points:
        metrics = point.get("metrics")

        if not isinstance(
            metrics,
            dict,
        ):
            continue

        ratio = _safe_number(metrics.get("valid_sample_ratio"))

        if ratio is not None:
            selected.append(
                (
                    point,
                    ratio,
                )
            )

    if not selected:
        return False

    figure, axis = plt.subplots(
        figsize=(
            max(
                8,
                len(selected) * 1.1,
            ),
            4.8,
        )
    )
    x_values = list(
        range(
            1,
            len(selected) + 1,
        )
    )
    axis.plot(
        x_values,
        [ratio for _, ratio in selected],
        marker="o",
        label="Valid sample ratio",
    )
    axis.axhline(
        LOW_VALID_SAMPLE_RATIO,
        linestyle="--",
        label="Quality threshold",
    )
    axis.set_ylim(
        0.0,
        1.05,
    )
    axis.set_xticks(
        x_values,
        _labels([point for point, _ in selected]),
        rotation=25,
        ha="right",
    )
    axis.set_ylabel("Ratio")
    axis.set_title("Data quality across sessions")
    axis.legend()
    _atomic_save_figure(
        figure,
        output_path,
    )
    return True


def _tracking_plot(
    points: list[dict[str, object]],
    output_path: Path,
) -> bool:
    selected = [
        point
        for point in points
        if (point.get("metric_family") == "tracking" and point.get("usable_for_trend"))
    ]

    if not selected:
        return False

    hit_values = []
    duration_values = []

    for point in selected:
        metrics = point.get("metrics")

        if not isinstance(
            metrics,
            dict,
        ):
            metrics = {}

        hit_values.append(_safe_number(metrics.get("target_hit_ratio")))
        duration_values.append(_safe_number(metrics.get("target_hit_duration_ratio")))

    if not any(value is not None for value in (hit_values + duration_values)):
        return False

    figure, axis = plt.subplots(
        figsize=(
            max(
                8,
                len(selected) * 1.1,
            ),
            4.8,
        )
    )
    x_values = list(
        range(
            1,
            len(selected) + 1,
        )
    )

    if any(value is not None for value in hit_values):
        axis.plot(
            x_values,
            [float("nan") if value is None else value for value in hit_values],
            marker="o",
            label="Target hit ratio",
        )

    if any(value is not None for value in duration_values):
        axis.plot(
            x_values,
            [float("nan") if value is None else value for value in duration_values],
            marker="o",
            label="Hit duration ratio",
        )

    axis.set_ylim(
        0.0,
        1.05,
    )
    axis.set_xticks(
        x_values,
        _labels(selected),
        rotation=25,
        ha="right",
    )
    axis.set_ylabel("Ratio")
    axis.set_title("Tracking task longitudinal ratios")
    axis.legend()
    _atomic_save_figure(
        figure,
        output_path,
    )
    return True


def _binary_plot(
    points: list[dict[str, object]],
    output_path: Path,
) -> bool:
    selected = [
        point
        for point in points
        if (point.get("metric_family") == "binary" and point.get("usable_for_trend"))
    ]

    if not selected:
        return False

    reaction_values = []
    dwell_values = []

    for point in selected:
        metrics = point.get("metrics")

        if not isinstance(
            metrics,
            dict,
        ):
            metrics = {}

        reaction_values.append(_safe_number(metrics.get("reaction_time_ms")))
        dwell_values.append(_safe_number(metrics.get("confirmation_dwell_ms")))

    if not any(value is not None for value in (reaction_values + dwell_values)):
        return False

    figure, axis = plt.subplots(
        figsize=(
            max(
                8,
                len(selected) * 1.1,
            ),
            4.8,
        )
    )
    x_values = list(
        range(
            1,
            len(selected) + 1,
        )
    )

    if any(value is not None for value in reaction_values):
        axis.plot(
            x_values,
            [float("nan") if value is None else value for value in reaction_values],
            marker="o",
            label="Reaction time",
        )

    if any(value is not None for value in dwell_values):
        axis.plot(
            x_values,
            [float("nan") if value is None else value for value in dwell_values],
            marker="o",
            label="Confirmation dwell",
        )

    axis.set_xticks(
        x_values,
        _labels(selected),
        rotation=25,
        ha="right",
    )
    axis.set_ylabel("Milliseconds")
    axis.set_title("Binary task timing across sessions")
    axis.legend()
    _atomic_save_figure(
        figure,
        output_path,
    )
    return True


def _ratio_text(
    value: object,
) -> str:
    number = _safe_number(value)

    if number is None:
        return "-"

    return f"{number:.1%}"


def _number_text(
    value: object,
    *,
    suffix: str = "",
) -> str:
    number = _safe_number(value)

    if number is None:
        return "-"

    return f"{number:.3f}{suffix}"


def _point_summary(
    point: dict[str, object],
) -> str:
    metrics = point.get("metrics")

    if not isinstance(
        metrics,
        dict,
    ):
        metrics = {}

    family = point.get("metric_family")

    if family == "tracking":
        return "；".join(
            (
                "命中率 " + _ratio_text(metrics.get("target_hit_ratio")),
                "命中时长 " + _ratio_text(metrics.get("target_hit_duration_ratio")),
                "首次稳定获得 "
                + _number_text(
                    metrics.get("first_target_acquired_ms"),
                    suffix=" ms",
                ),
                "最长连续追踪 "
                + _number_text(
                    metrics.get("longest_continuous_tracking_ms"),
                    suffix=" ms",
                ),
            )
        )

    if family == "binary":
        correct = metrics.get("correct")

        if metrics.get("is_scored") is False:
            score_text = "不评分"
        elif correct is True:
            score_text = "正确"
        elif correct is False:
            score_text = "错误"
        else:
            score_text = "-"

        return "；".join(
            (
                "选择 " + str(metrics.get("selected_answer") or "-"),
                "评分 " + score_text,
                "反应时间 "
                + _number_text(
                    metrics.get("reaction_time_ms"),
                    suffix=" ms",
                ),
            )
        )

    if family == "instruction_fixation":
        return "；".join(
            (
                "目标稳定注视 " + _ratio_text(metrics.get("target_acquisition_ratio")),
                "平均稳定注视潜伏期 "
                + _number_text(
                    metrics.get("mean_first_target_acquired_ms"),
                    suffix=" ms",
                ),
                "无目标试次干扰稳定注视 "
                + _number_text(metrics.get("no_target_false_fixation_count")),
            )
        )

    return "有效样本率 " + _ratio_text(metrics.get("valid_sample_ratio"))


def _comparison_text(
    point: dict[str, object],
) -> str:
    comparison = point.get("comparison")

    if not isinstance(
        comparison,
        dict,
    ):
        return "无同类前次记录"

    deltas = comparison.get("delta")

    if (
        not isinstance(
            deltas,
            dict,
        )
        or not deltas
    ):
        return "存在前次记录，但无可比较指标"

    parts = []

    for name, value in sorted(deltas.items()):
        number = _safe_number(value)

        if number is None:
            continue

        parts.append(f"{name}: {number:+.3f}")

    return "；".join(parts) or "-"


def _warning_text(
    point: dict[str, object],
) -> str:
    warnings = point.get("quality_warnings")

    if (
        not isinstance(
            warnings,
            list,
        )
        or not warnings
    ):
        return "无"

    messages = []

    for warning in warnings:
        if isinstance(
            warning,
            dict,
        ):
            messages.append(str(warning.get("message") or warning.get("code")))

    return "；".join(messages) or "无"


def _analysis_html(text: str) -> str:
    return f'<p class="analysis"><strong>简要分析：</strong>{html.escape(text)}</p>'


def _overview_analysis(document: dict[str, object]) -> str:
    session_count = _safe_integer(document.get("session_count")) or 0
    usable_count = _safe_integer(document.get("usable_point_count")) or 0
    modules = document.get("modules")
    module_count = len(modules) if isinstance(modules, dict) else 0
    return (
        f"本报告汇总该患者 {session_count} 次实验、{module_count} 类任务，"
        f"其中 {usable_count} 个结果点满足当前趋势展示条件。"
        "任务参数和患者当日状态不同会影响结果，跨任务数值不能直接互相比高低。"
    )


def _aggregate_gaze_analysis(document: dict[str, object]) -> str:
    value = document.get("aggregate_gaze")
    if not isinstance(value, dict):
        return "没有可读取的眼动采样文件，暂时无法生成该患者的综合热力图。"
    metrics = value.get("metrics")
    if not isinstance(metrics, dict):
        return "没有可读取的眼动采样文件，暂时无法生成该患者的综合热力图。"
    sample_count = _safe_integer(metrics.get("sample_count")) or 0
    valid_ratio = _safe_number(metrics.get("valid_sample_ratio")) or 0.0
    session_count = _safe_integer(value.get("session_count")) or 0
    return (
        f"综合图合并了 {session_count} 次实验的 {sample_count} 个样本，"
        f"整体有效率为 {valid_ratio:.1%}。"
        "底图表示实际视线累计密度，蓝点表示各任务目标位置；它适合看长期空间偏向，"
        "单次追踪准确性仍应查看对应任务报告。"
    )


def _quality_trend_analysis(points: list[dict[str, object]]) -> str:
    ratios = []
    for point in points:
        metrics = point.get("metrics")
        if isinstance(metrics, dict):
            ratio = _safe_number(metrics.get("valid_sample_ratio"))
            if ratio is not None:
                ratios.append(ratio)
    if not ratios:
        return "没有足够的有效率记录可比较。"
    low_count = sum(ratio < LOW_VALID_SAMPLE_RATIO for ratio in ratios)
    return (
        f"共有 {len(ratios)} 个结果点记录了有效率，其中 {low_count} 个低于 "
        f"{LOW_VALID_SAMPLE_RATIO:.0%}。"
        "若波动较大，应先统一摆位、校准、环境光和测试时段，再比较行为变化。"
    )


def _tracking_trend_analysis(points: list[dict[str, object]]) -> str:
    tracking = [point for point in points if point.get("metric_family") == "tracking"]
    if not tracking:
        return "没有可比较的追踪任务结果。"
    latest_metrics = tracking[-1].get("metrics")
    latest = latest_metrics if isinstance(latest_metrics, dict) else {}
    return (
        f"共纳入 {len(tracking)} 个追踪结果点；最近一次目标命中率为 "
        f"{_ratio_text(latest.get('target_hit_ratio'))}。"
        "同一任务设置下，命中率上升、误差下降和持续追踪延长可作为描述性变化，不能单独等同于意识改善。"
    )


def _binary_trend_analysis(points: list[dict[str, object]]) -> str:
    binary = [point for point in points if point.get("metric_family") == "binary"]
    if not binary:
        return "没有可比较的二分问答结果。"
    answered = 0
    for point in binary:
        metrics = point.get("metrics")
        if isinstance(metrics, dict) and metrics.get("answered") is True:
            answered += 1
    return (
        f"共汇总 {len(binary)} 个二分问答结果点，其中 {answered} 个形成了明确选择。"
        "应重点观察同类问题在多次测试中的一致性，而不是用单次对错直接下结论。"
    )


def _aggregate_gaze_rows(
    service: ExperimentSessionService,
    patient_id: UUID,
) -> tuple[list[dict[str, object]], int, int]:
    rows: list[dict[str, object]] = []
    included_session_count = 0
    unreadable_session_count = 0
    for session in service.list_sessions_for_patient(patient_id):
        directory = service.resolve_session_directory(session.session_id)
        try:
            session_rows = _load_rows(directory)
        except (OSError, ValueError):
            unreadable_session_count += 1
            continue
        if session_rows:
            rows.extend(session_rows)
            included_session_count += 1
    return rows, included_session_count, unreadable_session_count


def _html_document(
    document: dict[str, object],
    *,
    has_aggregate_heatmap: bool,
    has_quality_plot: bool,
    has_tracking_plot: bool,
    has_binary_plot: bool,
) -> str:
    points = _flatten_points(document)
    rows = []

    for point in points:
        rows.append(
            "<tr>"
            "<td>"
            + html.escape(str(point.get("created_at_utc") or "-")[:19])
            + "</td><td>"
            + html.escape(
                _MODULE_TITLES.get(
                    str(point.get("module_id") or "-"),
                    str(point.get("module_id") or "-"),
                )
            )
            + "</td><td>"
            + html.escape(str(point.get("completion_status") or point.get("session_status") or "-"))
            + "</td><td>"
            + html.escape(_point_summary(point))
            + "</td><td>"
            + html.escape(_comparison_text(point))
            + "</td><td>"
            + html.escape(_warning_text(point))
            + "</td></tr>"
        )

    chart_sections = []

    chart_sections.append(
        "<section><h2>全部任务综合热力图</h2>"
        + (
            '<img src="aggregate_heatmap.png" alt="Combined patient gaze heatmap">'
            if has_aggregate_heatmap
            else "<p>没有可读取的有效眼动采样。</p>"
        )
        + _analysis_html(_aggregate_gaze_analysis(document))
        + "</section>"
    )

    if has_quality_plot:
        chart_sections.append(
            "<section><h2>数据质量趋势</h2>"
            '<img src="data_quality.png" '
            'alt="Data quality trend">'
            + _analysis_html(_quality_trend_analysis(points))
            + "</section>"
        )

    if has_tracking_plot:
        chart_sections.append(
            "<section><h2>追踪任务趋势</h2>"
            '<img src="tracking_trend.png" '
            'alt="Tracking trend">'
            + _analysis_html(_tracking_trend_analysis(points))
            + "</section>"
        )

    if has_binary_plot:
        chart_sections.append(
            "<section><h2>二分任务时序趋势</h2>"
            '<img src="binary_timing.png" '
            'alt="Binary timing trend">'
            + _analysis_html(_binary_trend_analysis(points))
            + "</section>"
        )

    notice = html.escape(str(document.get("clinical_use_notice") or ""))
    warning_counts = html.escape(
        json.dumps(
            document.get("quality_warning_counts") or {},
            ensure_ascii=False,
        )
    )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>OculiDoC 患者综合报告</title>
<style>
body {{
    font-family: "Microsoft YaHei UI", Arial, sans-serif;
    margin: 32px auto;
    max-width: 1240px;
    color: #17324d;
    background: #eef3f8;
}}
header, section {{
    background: #ffffff;
    border: 1px solid #d9e3ec;
    border-radius: 12px;
    padding: 20px;
    margin-bottom: 18px;
}}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{
    border-bottom: 1px solid #d9e3ec;
    padding: 9px;
    text-align: left;
    vertical-align: top;
}}
img {{ width: 100%; height: auto; }}
.notice {{ color: #7a4e00; background: #fff7df; }}
.analysis {{
    margin-top: 14px;
    padding: 12px 14px;
    border-left: 4px solid #2e7d9a;
    background: #eef8fb;
    line-height: 1.7;
}}
code {{ word-break: break-all; }}
</style>
</head>
<body>
<header>
<h1>OculiDoC 患者全部任务综合报告</h1>
<p>患者：{html.escape(str(document["patient_display_label"]))}</p>
<p>会话数：{document["session_count"]}</p>
<p>可用于趋势的结果点：{document["usable_point_count"]}</p>
<p>质量警告计数：{warning_counts}</p>
{_analysis_html(_overview_analysis(document))}
</header>
<section>
<h2>全部任务结果与同类前次差值</h2>
<table>
<thead>
<tr>
<th>日期</th><th>任务</th><th>状态</th>
<th>本次结果</th><th>相对前次变化</th><th>质量提示</th>
</tr>
</thead>
<tbody>
{"".join(rows)}
</tbody>
</table>
{_analysis_html("表格按时间列出全部任务；“相对前次变化”只与同一任务、同一指标的前一次记录比较，正负号本身不等于变好或变差。")}
</section>
{"".join(chart_sections)}
<section class="notice">
<strong>解释限制：</strong>{notice}
</section>
</body>
</html>
"""


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
                source="clinical_trends",
                mime_type=(mimetypes.guess_type(path.name)[0]),
                size_bytes=(path.stat().st_size),
                sha256=_sha256(path),
            )
        )
    except DuplicateSessionArtifactError:
        return


def generate_patient_trend_report(
    service: ExperimentSessionService,
    anchor_session_id: UUID,
) -> PatientTrendArtifacts:
    """Generate a patient-level trend report.

    The report is stored below the selected anchor
    session and registered in that session manifest.
    """

    anchor = service.get_session(anchor_session_id)
    session_directory = service.resolve_session_directory(anchor_session_id)
    document = build_patient_trend_document(
        service,
        anchor.patient_id,
        anchor_session_id=(anchor_session_id),
    )
    generated_at = datetime.now(UTC)
    report_id = generated_at.strftime("%Y%m%dT%H%M%S%fZ")
    report_directory = session_directory / "reports" / f"patient_trends_{report_id}"
    report_directory.mkdir(
        parents=True,
        exist_ok=False,
    )
    report_json_path = report_directory / "trend_report.json"
    html_path = report_directory / "trend_report.html"
    aggregate_heatmap_path = report_directory / "aggregate_heatmap.png"
    data_quality_path = report_directory / "data_quality.png"
    tracking_path = report_directory / "tracking_trend.png"
    binary_path = report_directory / "binary_timing.png"
    points = _flatten_points(document)
    aggregate_rows, gaze_session_count, unreadable_gaze_session_count = _aggregate_gaze_rows(
        service,
        anchor.patient_id,
    )
    has_aggregate_heatmap = bool(aggregate_rows)
    if has_aggregate_heatmap:
        aggregate_metrics = _build_metrics(aggregate_rows)
        valid_points = aggregate_metrics.pop("_valid_points")
        aggregate_metrics.pop("_tracking_errors")
        tracking_series = aggregate_metrics.pop("_tracking_series")
        target_trajectory = aggregate_metrics.pop("_target_trajectory")
        assert isinstance(valid_points, list)
        assert isinstance(tracking_series, list)
        assert isinstance(target_trajectory, list)
        _screen_heatmap(
            valid_points,
            aggregate_heatmap_path,
            target_trajectory,
            tracking_series,
            connect_trajectories=False,
        )
    else:
        aggregate_metrics = None
    document["aggregate_gaze"] = {
        "session_count": gaze_session_count,
        "unreadable_session_count": unreadable_gaze_session_count,
        "metrics": aggregate_metrics,
    }
    has_quality_plot = _data_quality_plot(
        points,
        data_quality_path,
    )
    has_tracking_plot = _tracking_plot(
        points,
        tracking_path,
    )
    has_binary_plot = _binary_plot(
        points,
        binary_path,
    )

    _atomic_write_json(
        report_json_path,
        document,
    )
    _atomic_write_text(
        html_path,
        _html_document(
            document,
            has_aggregate_heatmap=has_aggregate_heatmap,
            has_quality_plot=(has_quality_plot),
            has_tracking_plot=(has_tracking_plot),
            has_binary_plot=(has_binary_plot),
        ),
    )

    produced = [
        report_json_path,
        html_path,
    ]

    for has_plot, path in (
        (
            has_aggregate_heatmap,
            aggregate_heatmap_path,
        ),
        (
            has_quality_plot,
            data_quality_path,
        ),
        (
            has_tracking_plot,
            tracking_path,
        ),
        (
            has_binary_plot,
            binary_path,
        ),
    ):
        if has_plot:
            produced.append(path)

    for path in produced:
        _register_artifact(
            service,
            session_id=anchor_session_id,
            session_directory=(session_directory),
            path=path,
        )

    return PatientTrendArtifacts(
        report_directory=report_directory,
        report_json_path=(report_json_path),
        html_path=html_path,
        data_quality_path=(data_quality_path if has_quality_plot else None),
        aggregate_heatmap_path=(aggregate_heatmap_path if has_aggregate_heatmap else None),
        tracking_path=(tracking_path if has_tracking_plot else None),
        binary_path=(binary_path if has_binary_plot else None),
    )
