"""Self-contained reports for independent neural-signal sessions."""

from __future__ import annotations

import html
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from oculidoc.signals.snapshot import SessionSignalSnapshot


def _atomic_text(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="\n",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as stream:
        temporary_path = Path(stream.name)
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return path


def write_signal_report(
    snapshot: SessionSignalSnapshot,
    task_result_path: str | Path,
) -> tuple[Path, Path]:
    """Write JSON and printable HTML beside a completed task result."""

    result_path = Path(task_result_path).expanduser().resolve()
    try:
        task_payload = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"Signal task result is invalid: {result_path}") from error
    if not isinstance(task_payload, dict) or not isinstance(task_payload.get("result"), dict):
        raise ValueError("Signal task result must contain a structured result object.")
    result = dict(task_payload["result"])
    simulated = bool(result.get("simulated"))
    report = {
        "schema_version": "1.0",
        "report_type": "engineering_signal_report" if simulated else "signal_session_report",
        "report_eligible": not simulated,
        "patient_id": snapshot.patient_id,
        "snapshot_sha256": snapshot.config_sha256,
        "snapshot": snapshot.to_dict(),
        "task": task_payload,
        "independence_boundary": (
            "Gaze, SSVEP, passive EEG, and MI results remain separate in v0.1.3."
        ),
    }
    json_path = result_path.with_name("signal_report.json")
    _atomic_text(json_path, json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    title = "OculiDoC 工程模拟信号报告" if simulated else "OculiDoC 神经信号会话报告"
    notice = (
        "工程模拟/模拟来源回放：不得进入真实患者报告、科研统计或临床决策。"
        if simulated
        else "本报告记录设备、通道、算法参数、频率、得分、置信度与拒绝状态。"
    )
    algorithm_value = result.get("algorithm")
    algorithm: dict[str, object] = (
        dict(algorithm_value) if isinstance(algorithm_value, dict) else {}
    )
    evaluation_value = result.get("evaluation")
    evaluation: dict[str, object] = (
        dict(evaluation_value) if isinstance(evaluation_value, dict) else {}
    )
    device_values = result.get("device_ids")
    devices = device_values if isinstance(device_values, list) else []
    frequency_values = snapshot.task_configuration.get("frequencies_hz")
    frequencies = frequency_values if isinstance(frequency_values, list) else []
    model_value = algorithm.get("model")
    model = dict(model_value) if isinstance(model_value, dict) else {}
    generated_model_value = result.get("calibration_model")
    generated_model = dict(generated_model_value) if isinstance(generated_model_value, dict) else {}
    rows = (
        ("任务", snapshot.task_kind),
        ("范式", "、".join(item.value for item in snapshot.paradigms)),
        ("信号源", snapshot.source_kind.value),
        ("设备", "、".join(str(item) for item in devices)),
        ("通道", "、".join(snapshot.channel_names)),
        ("采样率", f"{snapshot.sample_rate_hz:g} Hz"),
        ("算法", f"{algorithm.get('name', 'none')} · {algorithm.get('version', '-')}"),
        (
            "模型",
            (f"{model.get('file_name', '-')} · {model.get('sha256', '-')}" if model else "-"),
        ),
        (
            "配置频率",
            "、".join(f"{float(value):g} Hz" for value in frequencies),
        ),
        (
            "生成校准模型",
            (
                f"{generated_model.get('file_name', '-')} · {generated_model.get('sha256', '-')}"
                if generated_model
                else "-"
            ),
        ),
        ("试次数", str(evaluation.get("trial_count", "-"))),
        ("准确率", str(evaluation.get("accuracy", "-"))),
        (
            "拒绝数",
            str(evaluation.get("rejected_count", result.get("invalid_or_rejected_count", "-"))),
        ),
        ("配置哈希", snapshot.config_sha256),
    )
    row_html = "\n".join(
        f"<tr><th>{html.escape(label)}</th><td>{html.escape(value)}</td></tr>"
        for label, value in rows
    )
    trial_json = html.escape(
        json.dumps(
            result.get("trial_results", result.get("trials", [])), ensure_ascii=False, indent=2
        )
    )
    parameter_json = html.escape(
        json.dumps(algorithm.get("parameters", {}), ensure_ascii=False, indent=2)
    )
    document = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>
body{{font-family:"Microsoft YaHei UI",sans-serif;margin:40px;color:#17324d}}
h1{{color:#123d63}} .notice{{padding:14px;border:2px solid #b42318;background:#fff4f2}}
table{{border-collapse:collapse;width:100%;margin:20px 0}}
th,td{{border:1px solid #ccd9e4;padding:9px;text-align:left}}
th{{width:170px;background:#eef5fb}}pre{{white-space:pre-wrap;background:#f5f8fa;padding:14px}}
</style></head><body><h1>{html.escape(title)}</h1><p class="notice">{html.escape(notice)}</p>
<table>{row_html}</table><h2>算法参数</h2><pre>{parameter_json}</pre>
<h2>逐试次结果</h2><pre>{trial_json}</pre>
<p>v0.1.3 边界：眼动、SSVEP、被动 EEG 与运动想象分别运行、分别报告，不融合为控制输出。</p>
</body></html>"""
    html_path = result_path.with_name("signal_report.html")
    _atomic_text(html_path, document)
    return json_path, html_path
