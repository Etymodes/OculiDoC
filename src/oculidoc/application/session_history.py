"""Patient-scoped experiment-session history and export."""

from __future__ import annotations

import json
import os
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import UUID

from oculidoc.application.experiment_session_service import (
    ExperimentSessionService,
)
from oculidoc.domain.experiment_session import (
    ExperimentSession,
    ExperimentSessionStatus,
)


@dataclass(frozen=True, slots=True)
class SessionHistoryEntry:
    """Display-ready summary for one experiment session."""

    session_id: UUID
    patient_id: UUID
    module_id: str
    status: ExperimentSessionStatus
    created_at: datetime
    started_at: datetime | None
    ended_at: datetime | None
    duration_seconds: float | None
    artifact_count: int
    sample_count: int | None
    valid_sample_ratio: float | None
    dwell_by_role_ms: Mapping[str, float]
    failure_reason: str | None
    session_directory: Path

    task_results: tuple[
        dict[str, object],
        ...,
    ]

    @property
    def has_task_result(self) -> bool:
        return any(self.session_directory.glob("tasks/*/task_result.json"))


def _safe_number(
    value: object,
) -> float | None:
    if isinstance(value, bool):
        return None

    if isinstance(value, (int, float)):
        return float(value)

    return None


def _task_summary(
    session_directory: Path,
) -> tuple[
    int | None,
    float | None,
    dict[str, float],
    tuple[
        dict[str, object],
        ...,
    ],
]:
    sample_count_total = 0
    valid_sample_total = 0.0
    summary_count = 0
    dwell_by_role_ms: dict[str, float] = {}
    task_results: list[dict[str, object]] = []

    for result_path in sorted(session_directory.glob("tasks/*/task_result.json")):
        try:
            payload = json.loads(result_path.read_text(encoding="utf-8"))
        except (
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ):
            continue

        if not isinstance(payload, dict):
            continue

        result_value = payload.get("result")
        result = dict(result_value) if isinstance(result_value, dict) else {}
        event_counts: dict[str, int] = {}
        event_path = result_path.with_name("task_events.jsonl")

        if event_path.is_file():
            try:
                event_lines = event_path.read_text(encoding="utf-8").splitlines()
            except (
                OSError,
                UnicodeDecodeError,
            ):
                event_lines = []

            for line in event_lines:
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
                    event_counts[event_type] = (
                        event_counts.get(
                            event_type,
                            0,
                        )
                        + 1
                    )

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
                "result": result,
                "event_counts": dict(sorted(event_counts.items())),
            }
        )

        summary = payload.get("summary")

        if not isinstance(summary, dict):
            continue

        raw_sample_count = _safe_number(summary.get("sample_count"))
        sample_count = (
            max(
                0,
                int(raw_sample_count),
            )
            if raw_sample_count is not None
            else 0
        )
        raw_valid_ratio = _safe_number(summary.get("valid_sample_ratio"))

        sample_count_total += sample_count

        if raw_valid_ratio is not None:
            valid_sample_total += sample_count * max(
                0.0,
                min(
                    1.0,
                    raw_valid_ratio,
                ),
            )

        raw_dwell = summary.get("dwell_by_role_ms")

        if isinstance(raw_dwell, dict):
            for role, duration in raw_dwell.items():
                normalized_duration = _safe_number(duration)

                if normalized_duration is None:
                    continue

                role_name = str(role)
                dwell_by_role_ms[role_name] = dwell_by_role_ms.get(
                    role_name,
                    0.0,
                ) + max(
                    0.0,
                    normalized_duration,
                )

        summary_count += 1

    if summary_count == 0 and not task_results:
        return None, None, {}, ()

    valid_sample_ratio = valid_sample_total / sample_count_total if sample_count_total > 0 else None

    return (
        (sample_count_total if summary_count > 0 else None),
        valid_sample_ratio,
        dwell_by_role_ms,
        tuple(task_results),
    )


def format_task_result_lines(
    task_results: tuple[
        dict[str, object],
        ...,
    ],
) -> tuple[str, ...]:
    """Format structured outcomes for the history UI."""

    lines: list[str] = []

    def ratio_text(value: object) -> str:
        number = _safe_number(value)

        if number is None:
            return "-"

        return f"{number:.1%}"

    def milliseconds_text(
        value: object,
    ) -> str:
        number = _safe_number(value)

        if number is None:
            return "-"

        return f"{number:.0f} ms"

    for index, task_record in enumerate(
        task_results,
        start=1,
    ):
        result_value = task_record.get("result")
        result = result_value if isinstance(result_value, dict) else {}
        task_kind = str(task_record.get("task_kind") or "unknown")
        lines.append(f"任务结果 {index}（{task_kind}）")

        completion_status = result.get("completion_status")

        if completion_status is not None:
            lines.append(f"完成状态：{completion_status}")

        is_multiple_choice = task_kind == "multiple_choice" or "selected_answers" in result

        if is_multiple_choice:
            selected_answers = result.get("selected_answers")
            answer_text = (
                "、".join(str(value) for value in selected_answers)
                if isinstance(selected_answers, list) and selected_answers
                else "未选择"
            )
            lines.append(f"患者选择：{answer_text}")
            lines.append("评分结果：不评分")
            lines.append(
                "首次选择反应时间："
                + milliseconds_text(result.get("first_selection_reaction_time_ms"))
            )
            lines.append(f"选择/取消次数：{result.get('toggle_count', 0)}")

        is_binary = not is_multiple_choice and any(
            key in result
            for key in (
                "question",
                "selected_answer",
                "selected_option_id",
            )
        )

        if is_binary:
            answer = result.get("selected_answer")
            lines.append("患者选择：" + (str(answer) if answer is not None else "未作答"))

            if result.get("is_scored") is False:
                score_text = "不评分"
            elif result.get("correct") is True:
                score_text = "正确"
            elif result.get("correct") is False:
                score_text = "错误"
            else:
                score_text = "-"

            lines.append(f"评分结果：{score_text}")
            lines.append("反应时间：" + milliseconds_text(result.get("reaction_time_ms")))
            lines.append("确认停留：" + milliseconds_text(result.get("confirmation_dwell_ms")))

        is_tracking = task_kind == "tracking_ball" or "target_hit_ratio" in result

        if is_tracking:
            lines.extend(
                (
                    "目标命中率：" + ratio_text(result.get("target_hit_ratio")),
                    "命中时长占比：" + ratio_text(result.get("target_hit_duration_ratio")),
                    "首次稳定获得：" + milliseconds_text(result.get("first_target_acquired_ms")),
                    "最长连续追踪："
                    + milliseconds_text(result.get("longest_continuous_tracking_ms")),
                    "目标丢失/重新获得："
                    + str(
                        result.get(
                            "target_loss_count",
                            0,
                        )
                    )
                    + "/"
                    + str(
                        result.get(
                            "target_reacquisition_count",
                            0,
                        )
                    ),
                )
            )

    return tuple(lines)


def _duration_seconds(
    session: ExperimentSession,
) -> float | None:
    start = session.started_at or session.created_at
    end = session.ended_at

    if end is None:
        return None

    return max(
        0.0,
        (end - start).total_seconds(),
    )


def build_patient_session_history(
    service: ExperimentSessionService,
    patient_id: UUID,
) -> tuple[SessionHistoryEntry, ...]:
    """Return newest-first session summaries."""

    entries: list[SessionHistoryEntry] = []

    for session in service.list_sessions_for_patient(patient_id):
        session_directory = service.resolve_session_directory(session.session_id)
        artifacts = service.list_artifacts(session.session_id)
        (
            sample_count,
            valid_sample_ratio,
            dwell_by_role_ms,
            task_results,
        ) = _task_summary(session_directory)

        entries.append(
            SessionHistoryEntry(
                session_id=session.session_id,
                patient_id=session.patient_id,
                module_id=session.module_id,
                status=session.status,
                created_at=session.created_at,
                started_at=session.started_at,
                ended_at=session.ended_at,
                duration_seconds=(_duration_seconds(session)),
                artifact_count=len(artifacts),
                sample_count=sample_count,
                valid_sample_ratio=(valid_sample_ratio),
                dwell_by_role_ms=(dwell_by_role_ms),
                task_results=(task_results),
                failure_reason=(session.failure_reason),
                session_directory=(session_directory),
            )
        )

    entries.sort(
        key=lambda entry: entry.created_at,
        reverse=True,
    )
    return tuple(entries)


def export_session_zip(
    service: ExperimentSessionService,
    session_id: UUID,
    destination: str | Path,
) -> Path:
    """Create an atomic ZIP archive for one session."""

    session_directory = service.resolve_session_directory(session_id)
    destination_path = Path(destination).expanduser().resolve()

    if destination_path.suffix.lower() != ".zip":
        destination_path = destination_path.with_suffix(".zip")

    destination_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with NamedTemporaryFile(
        prefix=f".{destination_path.stem}.",
        suffix=".tmp",
        dir=destination_path.parent,
        delete=False,
    ) as temporary_file:
        temporary_path = Path(temporary_file.name)

    try:
        with zipfile.ZipFile(
            temporary_path,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
        ) as archive:
            for path in sorted(session_directory.rglob("*")):
                if not path.is_file():
                    continue

                resolved_path = path.resolve()

                if resolved_path in {
                    destination_path,
                    temporary_path,
                }:
                    continue

                archive.write(
                    resolved_path,
                    arcname=(resolved_path.relative_to(session_directory).as_posix()),
                )

        os.replace(
            temporary_path,
            destination_path,
        )
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise

    return destination_path
