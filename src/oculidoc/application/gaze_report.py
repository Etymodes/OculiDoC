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
from oculidoc.modules.registry import DEFAULT_MODULES

_MODULE_TITLES = {module.module_id: module.title for module in DEFAULT_MODULES}
CLINICAL_USE_NOTICE = (
    "本结果描述患者在本次任务、当前配置和可用眼动数据中的观察表现。"
    "眼动任务不能单独诊断意识状态，也不能替代 CRS-R 等重复标准化行为评估。"
    "低有效采样率、视觉/眼肌障碍、疲劳、药物和警觉波动均可能造成阴性或不稳定结果。"
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
    tracking_error_timeline_path: Path | None
    task_detail_path: Path | None


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
        show_gaze_cursor: bool | None = None
        config_path = result_path.parent / "task_config.json"

        try:
            config_payload = json.loads(config_path.read_text(encoding="utf-8"))
        except (
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ):
            config_payload = None

        if isinstance(config_payload, dict):
            config = config_payload.get("config")

            if isinstance(config, dict) and isinstance(config.get("show_gaze_cursor"), bool):
                show_gaze_cursor = config["show_gaze_cursor"]

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
                "show_gaze_cursor": show_gaze_cursor,
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
    show_gaze_cursor = task_record.get("show_gaze_cursor")

    if show_gaze_cursor is True:
        gaze_cursor_text = "开"
    elif show_gaze_cursor is False:
        gaze_cursor_text = "关"
    else:
        gaze_cursor_text = "旧记录未记载"

    rows.extend(
        (
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
            (
                "患者屏幕视线光标",
                gaze_cursor_text,
            ),
        )
    )

    sequence_questions = result.get("questions")

    if isinstance(sequence_questions, list):
        rows.extend(
            (
                (
                    "连续题完成",
                    f"{result.get('completed_question_count', 0)}/"
                    f"{result.get('question_count', len(sequence_questions))}",
                ),
                ("答对题数", _display_value(result.get("correct_count"))),
            )
        )

        for question in sequence_questions:
            if not isinstance(question, dict):
                continue

            correct = question.get("correct")
            correctness = "正确" if correct is True else "错误" if correct is False else "不评分"
            rows.append(
                (
                    f"第 {question.get('question_number', '-')} 题",
                    f"{question.get('question', '-')} · "
                    f"选择 {question.get('selected_answer') or '未作答'} · {correctness}",
                )
            )

    is_multiple_choice = task_kind == "multiple_choice" or "selected_answers" in result

    if is_multiple_choice:
        selected_answers = result.get("selected_answers")
        selected_option_ids = result.get("selected_option_ids")
        rows.extend(
            (
                ("问题", _display_value(result.get("question"))),
                (
                    "患者选择",
                    "、".join(str(value) for value in selected_answers)
                    if isinstance(selected_answers, list) and selected_answers
                    else "-",
                ),
                (
                    "逻辑选项",
                    ", ".join(str(value) for value in selected_option_ids)
                    if isinstance(selected_option_ids, list) and selected_option_ids
                    else "-",
                ),
                ("当前选择数", _display_value(result.get("selected_count"))),
                ("选择/取消次数", _display_value(result.get("toggle_count"))),
                ("排列方式", _display_value(result.get("layout"))),
                ("评分结果", "不评分"),
                (
                    "首次选择反应时间",
                    _display_ms(result.get("first_selection_reaction_time_ms")),
                ),
            )
        )

    is_binary = not is_multiple_choice and any(
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
                    "显示位置",
                    _display_value(result.get("selected_position", result.get("selected_side"))),
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

    game_mode = str(result.get("game_mode") or "")

    if task_kind == "gaze_games" and game_mode == "garden":
        rows.extend(
            (
                ("游戏模式", "点亮花园"),
                ("有效样本率", _display_ratio(result.get("valid_sample_ratio"))),
                ("实际随机种子", _display_value(result.get("randomization_seed"))),
                ("回放来源", _display_value(result.get("replay_source"))),
                ("花朵探索覆盖", _display_ratio(result.get("aoi_exploration_coverage"))),
                ("联动触发次数", _display_value(result.get("contingent_activation_count"))),
                (
                    "联动目标停留占比",
                    _display_ratio(result.get("contingent_target_dwell_ratio")),
                ),
                (
                    "回放目标停留占比",
                    _display_ratio(result.get("replay_target_dwell_ratio")),
                ),
                (
                    "丢失后重新获得",
                    _display_value(result.get("loss_and_reacquisition_count")),
                ),
                (
                    "解释边界",
                    "仅描述本次联动与回放中的注视表现，不用于意识分类",
                ),
            )
        )
        blocks = result.get("blocks")

        if isinstance(blocks, list):
            block_labels = {
                "baseline": "基线",
                "contingent_1": "联动 1",
                "replay": "回放",
                "contingent_2": "联动 2",
            }

            for block in blocks:
                if not isinstance(block, dict):
                    continue

                sample_count = _finite_float(block.get("sample_count")) or 0.0
                valid_count = _finite_float(block.get("valid_sample_count")) or 0.0
                ratio = valid_count / sample_count if sample_count > 0 else None
                rows.append(
                    (
                        f"{block_labels.get(str(block.get('block_type')), '区块')}明细",
                        f"有效率 {_display_ratio(ratio)} · "
                        f"有效时长 {_display_ms(block.get('valid_duration_ms'))} · "
                        f"目标停留 {_display_ms(block.get('target_dwell_ms'))} · "
                        f"触发 {len(block.get('activation_latencies_ms', []))}",
                    )
                )

    if task_kind == "gaze_games" and game_mode == "treasure_hunt":
        by_condition = result.get("target_acquisition_ratio_by_condition")

        if not isinstance(by_condition, dict):
            by_condition = {}

        rows.extend(
            (
                ("游戏模式", "视觉寻宝"),
                ("有效样本率", _display_ratio(result.get("valid_sample_ratio"))),
                (
                    "目标存在试次成功",
                    f"{result.get('successful_trial_count', 0)}/"
                    f"{result.get('target_present_trial_count', 0)}",
                ),
                (
                    "未成功或超时",
                    _display_value(result.get("failed_or_timeout_trial_count")),
                ),
                (
                    "预览搜索获得率",
                    _display_ratio(by_condition.get("preview_search")),
                ),
                ("突现获得率", _display_ratio(by_condition.get("popout"))),
                (
                    "目标获得中位潜伏期",
                    _display_ms(result.get("median_target_acquisition_ms")),
                ),
                ("干扰区停留占比", _display_ratio(result.get("distractor_dwell_ratio"))),
                (
                    "目标缺失试次假选择率",
                    _display_ratio(result.get("catch_false_selection_ratio")),
                ),
                (
                    "解释边界",
                    "未成功、超时和目标缺失试次均保留在分母；结果不用于意识分类",
                ),
            )
        )
        hunt_trials = result.get("trials")

        if isinstance(hunt_trials, list):
            for trial in hunt_trials:
                if not isinstance(trial, dict):
                    continue

                outcome = (
                    "获得目标"
                    if trial.get("target_acquired") is True
                    else "目标缺失"
                    if trial.get("target_present") is False
                    else "未成功/超时"
                )
                rows.append(
                    (
                        f"试次 {trial.get('trial_number', '-')} · {trial.get('condition', '-')}",
                        f"{outcome} · 获得潜伏期 "
                        f"{_display_ms(trial.get('target_acquisition_ms'))} · "
                        f"干扰停留 {_display_ms(trial.get('distractor_dwell_ms'))}",
                    )
                )

    if task_kind == "gaze_games" and game_mode == "starlight_route":
        region = result.get("reachable_region")

        if not isinstance(region, dict):
            region = {}

        rows.extend(
            (
                ("游戏模式", "星光航线"),
                ("得分", _display_value(result.get("score"))),
                ("最终等级", _display_value(result.get("final_level"))),
                (
                    "有效轮次命中率",
                    _display_ratio(result.get("hit_ratio_valid_rounds")),
                ),
                (
                    "命中 / 未命中 / 无效",
                    f"{result.get('hit_count', 0)} / {result.get('miss_count', 0)} / "
                    f"{result.get('invalid_round_count', 0)}",
                ),
                (
                    "学习到的可达区域",
                    f"左 {_display_ratio(region.get('left'))} · "
                    f"上 {_display_ratio(region.get('top'))} · "
                    f"右 {_display_ratio(region.get('right'))} · "
                    f"下 {_display_ratio(region.get('bottom'))}",
                ),
                (
                    "解释边界",
                    "无效轮次不触发降级；可达区域仅描述本次游戏，不等同于临床视野检查",
                ),
            )
        )

    if task_kind == "visual_preference":
        rows.extend(
            (
                ("有效样本率", _display_ratio(result.get("valid_sample_ratio"))),
                (
                    "可用试次",
                    f"{result.get('usable_trial_count', 0)}/{result.get('trial_count', 0)}",
                ),
                ("任一图片进入率", _display_ratio(result.get("any_image_entry_ratio"))),
                ("图片 A 停留占比", _display_ratio(result.get("image_dwell_share_a"))),
                ("图片 B 停留占比", _display_ratio(result.get("image_dwell_share_b"))),
                ("固定左侧停留占比", _display_ratio(result.get("left_dwell_share"))),
                (
                    "换边后一致性",
                    _display_ratio(result.get("side_swap_consistency"))
                    + f"（分母 {result.get('side_swap_pair_denominator', 0)} 对）",
                ),
                (
                    "解释边界",
                    "图片关注与固定左右侧偏分别统计；停留不等同于识别、选择或诊断",
                ),
            )
        )
        preference_trials = result.get("trials")

        if isinstance(preference_trials, list):
            for trial in preference_trials:
                if not isinstance(trial, dict):
                    continue

                rows.append(
                    (
                        f"刺激对 {trial.get('pair_id', '-')} · "
                        f"呈现 {trial.get('side_presentation_index', '-')}",
                        f"质量 {trial.get('quality', '-')} · "
                        f"A {_display_ms(trial.get('dwell_a_ms'))} · "
                        f"B {_display_ms(trial.get('dwell_b_ms'))} · "
                        f"首次进入 {trial.get('first_entry') or '-'}",
                    )
                )

    if task_kind == "instruction_fixation" or (
        task_kind not in {"gaze_games", "visual_preference"}
        and "target_acquisition_ratio" in result
    ):
        rows.extend(
            (
                (
                    "已完成试次",
                    f"{result.get('completed_trial_count', 0)}/{result.get('trial_count', 0)}",
                ),
                (
                    "目标稳定注视",
                    f"{result.get('target_acquired_trial_count', 0)}/"
                    f"{result.get('target_present_trial_count', 0)}",
                ),
                (
                    "目标稳定注视比例",
                    _display_ratio(result.get("target_acquisition_ratio")),
                ),
                (
                    "平均首次进入目标",
                    _display_ms(result.get("mean_first_target_entry_ms")),
                ),
                (
                    "平均稳定注视潜伏期",
                    _display_ms(result.get("mean_first_target_acquired_ms")),
                ),
                (
                    "最长连续目标注视",
                    _display_ms(result.get("longest_continuous_target_fixation_ms")),
                ),
                (
                    "无目标试次干扰稳定注视",
                    f"{result.get('no_target_false_fixation_count', 0)}/"
                    f"{result.get('no_target_trial_count', 0)}",
                ),
                (
                    "全部干扰稳定注视次数",
                    _display_value(result.get("distractor_fixation_count")),
                ),
                (
                    "有效样本率",
                    _display_ratio(result.get("valid_sample_ratio")),
                ),
                (
                    "解释边界",
                    "仅描述指令后目标与干扰区注视证据，不自动判定意识状态",
                ),
            )
        )

        fixation_trials = result.get("trials")

        if isinstance(fixation_trials, list):
            for trial in fixation_trials:
                if not isinstance(trial, dict):
                    continue

                rows.append(
                    (
                        f"试次 {trial.get('trial_number', '-')} · {trial.get('condition', '-')}",
                        f"结果 {trial.get('outcome', '-')} · "
                        f"首次进入 {_display_ms(trial.get('first_target_entry_ms'))} · "
                        f"稳定注视 {_display_ms(trial.get('first_target_acquired_ms'))}",
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
        return (
            "<section><h2>结构化任务结果</h2>"
            "<p>没有可读取的 task_result.json。</p>"
            + _analysis_html(
                "本次缺少结构化任务结果，只能查看眼动采样图，不能解释具体回答或任务完成情况。"
            )
            + "</section>"
        )

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
            f"{_analysis_html(_task_result_analysis(task_record))}"
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
    tracking_series: list[tuple[float, float, float, float, float, float]] = []
    target_trajectory: list[tuple[float, float, float]] = []
    tracking_inside_count = 0
    tracking_sample_count = 0
    tracking_time_origin_ns: int | None = None
    previous_tracking_time_seconds = 0.0
    question_ids: set[str] = set()

    for row in rows:
        question_id = row.get("question_id")

        if question_id is not None:
            question_ids.add(str(question_id))

        duration_ms = max(
            0.0,
            _finite_float(row.get("duration_ms")) or 0.0,
        )
        left = _finite_float(row.get("reference_aoi_left"))
        top = _finite_float(row.get("reference_aoi_top"))
        right = _finite_float(row.get("reference_aoi_right"))
        bottom = _finite_float(row.get("reference_aoi_bottom"))
        reference_center: tuple[float, float] | None = None
        elapsed_seconds: float | None = None

        if (
            left is not None
            and top is not None
            and right is not None
            and bottom is not None
            and 0.0 <= left <= right <= 1.0
            and 0.0 <= top <= bottom <= 1.0
        ):
            center_x = (left + right) / 2.0
            center_y = (top + bottom) / 2.0
            reference_center = (center_x, center_y)
            timestamp_value = row.get("monotonic_timestamp_ns")
            timestamp_ns = (
                int(timestamp_value)
                if isinstance(timestamp_value, int) and not isinstance(timestamp_value, bool)
                else None
            )

            if timestamp_ns is not None and timestamp_ns >= 0:
                if tracking_time_origin_ns is None:
                    tracking_time_origin_ns = timestamp_ns

                elapsed_seconds = max(
                    previous_tracking_time_seconds,
                    (timestamp_ns - tracking_time_origin_ns) / 1_000_000_000.0,
                )
            else:
                elapsed_seconds = previous_tracking_time_seconds + max(duration_ms, 1.0) / 1_000.0

            previous_tracking_time_seconds = elapsed_seconds
            target_trajectory.append((elapsed_seconds, center_x, center_y))

        x = _finite_float(row.get("gaze_x_normalized"))
        y = _finite_float(row.get("gaze_y_normalized"))
        valid = bool(row.get("analysis_valid"))

        if not valid or x is None or y is None or not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            continue

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

        if reference_center is None or elapsed_seconds is None:
            continue

        assert left is not None and top is not None and right is not None and bottom is not None
        center_x, center_y = reference_center
        tracking_error = float(np.hypot(x - center_x, y - center_y))
        tracking_errors.append(tracking_error)
        tracking_series.append(
            (
                elapsed_seconds,
                x,
                y,
                center_x,
                center_y,
                tracking_error,
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
            "target_reference_sample_count": len(target_trajectory),
            "mean_error_normalized": float(np.mean(errors)),
            "median_error_normalized": float(np.median(errors)),
            "rmse_normalized": float(np.sqrt(np.mean(np.square(errors)))),
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
        "_tracking_series": tracking_series,
        "_target_trajectory": target_trajectory,
    }


def _screen_heatmap(
    valid_points: list[tuple[float, float, float]],
    output_path: Path,
    target_trajectory: list[tuple[float, float, float]] | None = None,
    tracking_series: list[tuple[float, float, float, float, float, float]] | None = None,
    *,
    connect_trajectories: bool = True,
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

    if target_trajectory and not connect_trajectories:
        target_step = max(1, int(np.ceil(len(target_trajectory) / 4_000)))
        plotted_target = target_trajectory[::target_step]
        axis.scatter(
            [sample[1] for sample in plotted_target],
            [sample[2] for sample in plotted_target],
            color="#00b7ff",
            s=7,
            alpha=0.18,
            label="Target positions",
            zorder=4,
        )
    elif target_trajectory:
        target_step = max(1, int(np.ceil(len(target_trajectory) / 2_000)))
        plotted_target = target_trajectory[::target_step]
        target_x = [sample[1] for sample in plotted_target]
        target_y = [sample[2] for sample in plotted_target]
        axis.plot(
            target_x,
            target_y,
            color="#00b7ff",
            linewidth=2.4,
            linestyle="--",
            label="Target trajectory",
            zorder=4,
        )
        axis.scatter(
            [target_x[0]],
            [target_y[0]],
            color="#00b7ff",
            marker="o",
            s=42,
            zorder=6,
        )
        axis.scatter(
            [target_x[-1]],
            [target_y[-1]],
            color="#00b7ff",
            marker="X",
            s=54,
            zorder=6,
        )

    if tracking_series and connect_trajectories:
        gaze_step = max(1, int(np.ceil(len(tracking_series) / 2_000)))
        plotted_gaze = tracking_series[::gaze_step]
        gaze_x = [sample[1] for sample in plotted_gaze]
        gaze_y = [sample[2] for sample in plotted_gaze]
        axis.plot(
            gaze_x,
            gaze_y,
            color="#ff8c42",
            linewidth=1.6,
            alpha=0.9,
            label="Gaze trajectory",
            zorder=5,
        )
        axis.scatter(
            [gaze_x[0]],
            [gaze_y[0]],
            color="#ff8c42",
            marker="o",
            s=42,
            zorder=6,
        )
        axis.scatter(
            [gaze_x[-1]],
            [gaze_y[-1]],
            color="#ff8c42",
            marker="X",
            s=54,
            zorder=6,
        )

    if target_trajectory or (tracking_series and connect_trajectories):
        axis.legend(loc="upper right")

    if not connect_trajectories and target_trajectory:
        title = "Combined gaze density with target positions"
    elif target_trajectory or tracking_series:
        title = "Screen-space gaze density with target/gaze trajectories"
    else:
        title = "Screen-space gaze density"
    axis.set_title(title)
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


def _tracking_error_timeline_plot(
    series: list[tuple[float, float, float, float, float, float]],
    output_path: Path,
) -> None:
    figure, axis = plt.subplots(figsize=(10, 5))
    times = np.array([sample[0] for sample in series], dtype=float)
    errors = np.array([sample[5] for sample in series], dtype=float)
    axis.scatter(
        times,
        errors,
        s=10,
        alpha=0.28,
        color="#315f8c",
        label="Sample error",
    )

    if len(series) > 1:
        positive_intervals = np.diff(times)
        positive_intervals = positive_intervals[positive_intervals > 0]
        median_interval = float(np.median(positive_intervals)) if positive_intervals.size else 1.0
        window = min(len(errors), max(1, int(round(1.0 / median_interval))))
        kernel = np.ones(window, dtype=float)
        smoothed = np.convolve(errors, kernel, mode="same") / np.convolve(
            np.ones(len(errors), dtype=float),
            kernel,
            mode="same",
        )
        axis.plot(
            times,
            smoothed,
            linewidth=2.2,
            color="#e56b1f",
            label="1-second moving average",
        )

    axis.axhline(0.0, color="#5b6770", linewidth=0.8)
    axis.set_title("Gaze-target position error over time")
    axis.set_xlabel("Elapsed time (seconds)")
    axis.set_ylabel("Normalized gaze-target distance")
    axis.set_ylim(bottom=0.0)
    axis.legend(loc="upper right")
    _atomic_save_figure(figure, output_path)


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
            _display_ratio(metrics["valid_sample_ratio"]),
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
                    "Tracking RMSE",
                    (f"{float(tracking['rmse_normalized']):.4f}"),
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


def _analysis_html(text: str) -> str:
    return f'<p class="analysis"><strong>简要分析：</strong>{html.escape(text)}</p>'


def _quality_analysis(metrics: dict[str, object]) -> str:
    sample_count = int(_finite_float(metrics.get("sample_count")) or 0.0)
    valid_count = int(_finite_float(metrics.get("valid_sample_count")) or 0.0)
    valid_ratio = _finite_float(metrics.get("valid_sample_ratio")) or 0.0
    if sample_count == 0:
        return "本次没有可读取的眼动样本，不能解释患者表现。"
    if valid_ratio < 0.35:
        quality = "有效率偏低，本次图表更容易受遮挡、头位、校准或疲劳影响"
    elif valid_ratio < 0.60:
        quality = "有效率中等，主要趋势可以参考，但细小差异应谨慎解释"
    else:
        quality = "多数样本有效，数据完整性相对较好"
    return (
        f"共记录 {sample_count} 个样本，其中 {valid_count} 个有效（{valid_ratio:.1%}）；{quality}。"
    )


def _task_result_analysis(task_record: dict[str, object]) -> str:
    if task_record.get("load_error") is not None:
        return "该任务结果文件无法完整读取，请先检查原始文件，暂不作行为解释。"
    value = task_record.get("result")
    result = value if isinstance(value, dict) else {}
    status = str(result.get("completion_status") or task_record.get("end_reason") or "未知")
    task_kind = str(task_record.get("task_kind") or result.get("task_kind") or "")
    game_mode = str(result.get("game_mode") or "")

    if task_kind == "gaze_games" and game_mode == "garden":
        return (
            f"本轮状态为“{status}”，联动区块共记录 "
            f"{result.get('contingent_activation_count', 0)} 次达到注视阈值的触发，"
            f"回放来源为 {result.get('replay_source') or '未形成'}。"
            "联动与回放差异只描述本次观察，不单独判断意识状态。"
        )

    if task_kind == "gaze_games" and game_mode == "treasure_hunt":
        return (
            f"本轮状态为“{status}”，目标存在试次获得 "
            f"{result.get('successful_trial_count', 0)}/"
            f"{result.get('target_present_trial_count', 0)}；"
            f"另有 {result.get('failed_or_timeout_trial_count', 0)} 个未成功或超时试次。"
            "所有失败、超时和目标缺失试次均保留，单次结果不用于意识分类。"
        )

    if task_kind == "visual_preference":
        return (
            f"本轮状态为“{status}”，可用试次 "
            f"{result.get('usable_trial_count', 0)}/{result.get('trial_count', 0)}。"
            "图片 A/B 与固定左/右侧分别统计，避免把侧偏误写成图片偏好；"
            "注视时长不等同于识别、选择或诊断。"
        )

    questions = result.get("questions")
    if isinstance(questions, list):
        completed = int(result.get("completed_question_count") or 0)
        skipped = int(result.get("skipped_question_count") or 0)
        correct = result.get("correct_count")
        scored = f"，其中记录答对 {correct} 题" if isinstance(correct, int) else ""
        return (
            f"本轮状态为“{status}”，共完成 {completed}/{len(questions)} 题，跳过 {skipped} 题"
            f"{scored}。跳题和未作答不应直接理解为患者没有意识。"
        )
    selected = result.get("selected_answer") or result.get("selected_answers")
    if selected:
        return (
            f"本轮状态为“{status}”，系统记录了患者选择：{selected}。"
            "应结合重复测试判断选择是否稳定。"
        )
    return f"本轮状态为“{status}”。本区只复述任务记录，不单独判断意识水平或临床改善。"


def _heatmap_analysis(metrics: dict[str, object]) -> str:
    centroid = metrics.get("gaze_centroid_normalized")
    if not isinstance(centroid, dict):
        return "没有足够的有效视线点形成稳定热区；优先检查摆位、遮挡、校准和患者状态。"
    x = float(centroid.get("x") or 0.0)
    y = float(centroid.get("y") or 0.0)
    return (
        f"视线停留重心约在屏幕横向 {x:.0%}、纵向 {y:.0%} 处。"
        "颜色越集中表示该区域累计停留越多；蓝色为任务目标，橙色为实际视线，二者越接近表示追踪越贴合。"
    )


def _aoi_analysis(metrics: dict[str, object]) -> str:
    value = metrics.get("dwell_by_role_ms")
    if not isinstance(value, dict) or not value:
        return "未记录到可汇总的选项或目标区域停留，不能比较患者更关注哪个区域。"
    role, duration = max(value.items(), key=lambda item: float(item[1]))
    labels = {
        "correct_option": "正确选项",
        "incorrect_option": "其他选项",
        "non_option": "非选项区域",
        "target": "目标区域",
        "distractor": "干扰区域",
    }
    return (
        f"累计停留最多的是“{labels.get(str(role), str(role))}”，"
        f"约 {float(duration) / 1000:.1f} 秒。"
        "停留较多表示视线更常落在该区域，但不一定等同于主动选择。"
    )


def _tracking_analysis(metrics: dict[str, object]) -> str:
    value = metrics.get("tracking")
    if not isinstance(value, dict):
        return "本次没有同时具备目标位置和有效视线的样本，因此不能计算目标—视线误差。"
    rmse = float(value.get("rmse_normalized") or 0.0)
    hit_ratio = float(value.get("target_hit_ratio") or 0.0)
    return (
        f"目标区域命中率为 {hit_ratio:.1%}，归一化 RMSE 为 {rmse:.3f}。"
        "命中率越高、误差越低通常表示视线更贴近任务目标；仍需结合有效率和重复测试。"
    )


def _task_detail_plot(
    task_results: list[dict[str, object]],
    output_path: Path,
) -> str | None:
    """Render one neutral task-family figure from structured block/trial results."""
    for task_record in task_results:
        result_value = task_record.get("result")
        result = result_value if isinstance(result_value, dict) else {}
        task_kind = str(task_record.get("task_kind") or "")
        game_mode = str(result.get("game_mode") or "")

        if task_kind == "gaze_games" and game_mode == "garden":
            blocks_value = result.get("blocks")
            blocks = (
                [block for block in blocks_value if isinstance(block, dict)]
                if isinstance(blocks_value, list)
                else []
            )

            if not blocks:
                return None

            labels = {
                "baseline": "Baseline",
                "contingent_1": "Contingent 1",
                "replay": "Replay",
                "contingent_2": "Contingent 2",
            }
            x_values = list(range(len(blocks)))
            valid_ratios = []

            for block in blocks:
                sample_count = _finite_float(block.get("sample_count")) or 0.0
                valid_count = _finite_float(block.get("valid_sample_count")) or 0.0
                valid_ratios.append(valid_count / sample_count if sample_count > 0 else 0.0)

            figure, (quality_axis, latency_axis) = plt.subplots(
                2,
                1,
                figsize=(9, 7),
                constrained_layout=True,
            )
            quality_axis.bar(x_values, valid_ratios, color="#4f8fb5")
            quality_axis.set_ylim(0.0, 1.0)
            quality_axis.set_ylabel("Valid sample ratio")
            quality_axis.set_xticks(
                x_values,
                [labels.get(str(block.get("block_type")), "Block") for block in blocks],
            )
            latency_found = False

            for index, block in enumerate(blocks):
                raw = block.get("activation_latencies_ms")
                latencies = (
                    [number for value in raw if (number := _finite_float(value)) is not None]
                    if isinstance(raw, list)
                    else []
                )

                if latencies:
                    latency_found = True
                    latency_axis.scatter(
                        [index] * len(latencies),
                        latencies,
                        color="#d77a28",
                    )

            latency_axis.set_xticks(
                x_values,
                [labels.get(str(block.get("block_type")), "Block") for block in blocks],
            )
            latency_axis.set_ylabel("Activation latency (ms)")
            latency_axis.set_title(
                "Threshold events by block" if latency_found else "No threshold event recorded"
            )
            figure.suptitle("Garden block quality and gaze-contingent events")
            _atomic_save_figure(figure, output_path)
            return "garden"

        if task_kind == "gaze_games" and game_mode == "treasure_hunt":
            trials_value = result.get("trials")
            trials = (
                [trial for trial in trials_value if isinstance(trial, dict)]
                if isinstance(trials_value, list)
                else []
            )

            if not trials:
                return None

            figure, axis = plt.subplots(figsize=(10, 5.5))
            colors = {
                "preview_search": "#2e7d9a",
                "popout": "#8a5aa9",
                "catch": "#7a7a7a",
            }

            for trial in trials:
                trial_number = _finite_float(trial.get("trial_number"))
                latency = _finite_float(trial.get("target_acquisition_ms"))
                condition = str(trial.get("condition") or "unknown")

                if trial_number is None:
                    continue

                if latency is not None:
                    axis.scatter(
                        trial_number,
                        latency,
                        color=colors.get(condition, "#2e7d9a"),
                        marker="o",
                    )
                else:
                    axis.scatter(
                        trial_number,
                        0.0,
                        color="#b42318" if trial.get("target_present") is not False else "#7a7a7a",
                        marker="x",
                        s=70,
                    )

            axis.set_xlabel("Trial number")
            axis.set_ylabel("Target acquisition latency (ms); x = no acquisition")
            axis.set_title("Visual-hunt outcomes with failures and catch trials retained")
            axis.grid(alpha=0.2)
            _atomic_save_figure(figure, output_path)
            return "treasure_hunt"

        if task_kind == "visual_preference":
            values = (
                _finite_float(result.get("image_dwell_share_a")),
                _finite_float(result.get("image_dwell_share_b")),
                _finite_float(result.get("left_dwell_share")),
            )

            if not any(value is not None for value in values):
                return None

            image_a, image_b, left = values
            right = None if left is None else 1.0 - left
            figure, axis = plt.subplots(figsize=(8.5, 5.2))
            bar_labels = ("Image A", "Image B", "Left side", "Right side")
            heights = [
                0.0 if image_a is None else image_a,
                0.0 if image_b is None else image_b,
                0.0 if left is None else left,
                0.0 if right is None else right,
            ]
            axis.bar(
                bar_labels,
                heights,
                color=("#4f8fb5", "#70ad74", "#c6904b", "#c6904b"),
            )
            axis.set_ylim(0.0, 1.0)
            axis.set_ylabel("Share of usable image dwell")
            axis.set_title("Image-specific attention separated from fixed screen side")
            _atomic_save_figure(figure, output_path)
            return "visual_preference"

    return None


def _task_detail_analysis(kind: str) -> str:
    if kind == "garden":
        return (
            "上图按四个区块分别保留有效率与达到注视阈值的时间点；"
            "回放块不是患者触发，不能与联动块作简单因果判断。"
        )

    if kind == "treasure_hunt":
        return (
            "圆点表示形成目标获得的试次，叉号保留未成功、超时和目标缺失试次。"
            "不同条件应分层复核，不能只报告成功试次。"
        )

    return (
        "图片 A/B 的停留份额与固定左/右侧份额分开展示；两者差异不能直接解释为识别、偏好或诊断结论。"
    )


def _html_document(
    *,
    patient_label: str,
    module_id: str,
    record_time: str,
    generated_at: str,
    metrics: dict[str, object],
    task_results: list[dict[str, object]],
    has_tracking_plot: bool,
    has_tracking_timeline: bool,
    task_detail_kind: str | None,
) -> str:
    task_result_html = _task_result_sections(task_results)
    valid_sample_ratio = _finite_float(metrics.get("valid_sample_ratio"))
    quality_warning = (
        '<section class="notice"><strong>数据质量不足，谨慎解释：</strong>'
        "本次有效采样率低于 60%，原始记录仍完整保留；"
        "请优先复核摆位、校准、眼睑/眼肌情况、疲劳、药物与警觉波动。</section>"
        if valid_sample_ratio is not None and valid_sample_ratio < 0.60
        else ""
    )
    tracking_image = (
        "<section><h2>目标—视线误差分布</h2>"
        '<img src="tracking_error.png" '
        'alt="Tracking error">'
        f"{_analysis_html(_tracking_analysis(metrics))}</section>"
        if has_tracking_plot
        else ""
    )
    tracking_timeline = (
        "<section><h2>目标—视线误差随时间变化</h2>"
        '<img src="tracking_error_timeline.png" '
        'alt="Gaze-target error over time">'
        + _analysis_html(
            "散点表示每个有效采样的目标—视线距离，橙线表示约 1 秒平均趋势；"
            "曲线持续较低说明这一时段跟随更贴近，突然升高可能对应脱靶、眨眼或头位变化。"
        )
        + "</section>"
        if has_tracking_timeline
        else ""
    )
    task_detail = (
        "<section><h2>任务专属指标图</h2>"
        '<img src="task_detail.png" alt="Task-specific descriptive metrics">'
        f"{_analysis_html(_task_detail_analysis(task_detail_kind))}</section>"
        if task_detail_kind is not None
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
<h1>OculiDoC 眼动结果报告</h1>
<p>患者：{html.escape(patient_label)}</p>
<p>任务：{html.escape(_MODULE_TITLES.get(module_id, module_id))}</p>
<p>记录时间：{html.escape(record_time)}</p>
<p>生成时间：{html.escape(generated_at)}</p>
</header>
{quality_warning}
<section>
<h2>核心指标</h2>
<table>{_metric_rows(metrics)}</table>
{_analysis_html(_quality_analysis(metrics))}
</section>
{task_result_html}
{task_detail}
<section>
<h2>屏幕空间热图</h2>
<img src="screen_heatmap.png" alt="Screen heatmap">
{_analysis_html(_heatmap_analysis(metrics))}
</section>
<section>
<h2>语义 AOI 汇总</h2>
<img src="semantic_aoi.png" alt="Semantic AOI dwell">
{_analysis_html(_aoi_analysis(metrics))}
</section>
{tracking_image}
{tracking_timeline}
<section class="notice">
<strong>用途声明：</strong>
{html.escape(CLINICAL_USE_NOTICE)}
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
    patient = service.get_patient(session.patient_id)

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
    tracking_series = metrics.pop("_tracking_series")
    target_trajectory = metrics.pop("_target_trajectory")

    assert isinstance(
        valid_points,
        list,
    )
    assert isinstance(
        tracking_errors,
        list,
    )
    assert isinstance(tracking_series, list)
    assert isinstance(target_trajectory, list)
    dwell_by_role_ms = metrics["dwell_by_role_ms"]
    assert isinstance(dwell_by_role_ms, dict)

    screen_heatmap_path = report_directory / "screen_heatmap.png"
    semantic_aoi_path = report_directory / "semantic_aoi.png"
    tracking_error_path = report_directory / "tracking_error.png" if tracking_errors else None
    tracking_error_timeline_path = (
        report_directory / "tracking_error_timeline.png" if tracking_series else None
    )
    task_detail_path = report_directory / "task_detail.png"
    report_json_path = report_directory / "report.json"
    html_path = report_directory / "report.html"

    _screen_heatmap(
        valid_points,
        screen_heatmap_path,
        target_trajectory,
        tracking_series,
    )
    _semantic_aoi_plot(
        dwell_by_role_ms,
        semantic_aoi_path,
    )

    if tracking_error_path is not None:
        _tracking_error_plot(
            tracking_errors,
            tracking_error_path,
        )

    if tracking_error_timeline_path is not None:
        _tracking_error_timeline_plot(
            tracking_series,
            tracking_error_timeline_path,
        )

    task_detail_kind = _task_detail_plot(task_results, task_detail_path)

    generated_at_text = generated_at.isoformat()
    report_document: dict[str, object] = {
        "schema_version": "1.2",
        "task_results": task_results,
        "generated_at_utc": (generated_at_text),
        "patient_id": str(session.patient_id),
        "patient_name": patient.family_name,
        "patient_code": patient.patient_code,
        "patient_display_label": patient.display_label,
        "session_id": str(session.session_id),
        "module_id": session.module_id,
        "metrics": metrics,
        "task_metric_families": ([task_detail_kind] if task_detail_kind is not None else []),
        "clinical_use_notice": CLINICAL_USE_NOTICE,
    }
    _atomic_write_json(
        report_json_path,
        report_document,
    )
    _atomic_write_text(
        html_path,
        _html_document(
            patient_label=patient.display_label,
            module_id=(session.module_id),
            record_time=session.created_at.astimezone().strftime("%Y-%m-%d %H:%M:%S"),
            generated_at=(generated_at_text),
            metrics=metrics,
            task_results=task_results,
            has_tracking_plot=(tracking_error_path is not None),
            has_tracking_timeline=(tracking_error_timeline_path is not None),
            task_detail_kind=task_detail_kind,
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

    if tracking_error_timeline_path is not None:
        produced_paths.append(tracking_error_timeline_path)

    if task_detail_kind is not None:
        produced_paths.append(task_detail_path)

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
        tracking_error_timeline_path=(tracking_error_timeline_path),
        task_detail_path=(task_detail_path if task_detail_kind is not None else None),
    )
