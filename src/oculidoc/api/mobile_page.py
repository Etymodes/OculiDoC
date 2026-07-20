"""Single-page mobile controller served by the local API."""

# ruff: noqa: E501 -- the embedded HTML/JavaScript is intentionally kept readable.

from __future__ import annotations

import json

_PAGE = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>OculiDoC 手机管理员端</title>
  <style>
    :root { color-scheme: light; font-family: system-ui, "Microsoft YaHei", sans-serif; }
    body { margin: 0; background: #eef3f8; color: #17324d; }
    main { max-width: 760px; margin: 0 auto; padding: 18px; }
    .card { background: white; border: 1px solid #d9e3ec; border-radius: 16px;
            padding: 18px; margin-bottom: 14px; box-shadow: 0 4px 18px #17324d12; }
    h1 { font-size: 24px; margin: 0 0 6px; }
    h2 { font-size: 18px; margin: 0 0 12px; }
    .muted { color: #5a7184; font-size: 14px; }
    .status { font-weight: 700; }
    label { display: block; margin-top: 12px; font-weight: 700; }
    input, textarea, select { width: 100%; box-sizing: border-box; border: 1px solid #bfd3e4;
                              border-radius: 10px; padding: 11px; font-size: 16px; }
    input[type="checkbox"] { width: auto; margin-right: 8px; }
    input[type="color"] { height: 46px; padding: 4px; }
    textarea { min-height: 100px; resize: vertical; }
    .check-label { display: flex; align-items: center; font-weight: 600; }
    .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 12px; }
    .grid.three { grid-template-columns: 1fr 1fr 1fr; }
    button { border: 0; border-radius: 10px; padding: 13px 12px; font-size: 16px;
             font-weight: 700; background: #1565c0; color: white; }
    button.secondary { background: #edf4fb; color: #184e77; border: 1px solid #bfd3e4; }
    button.danger { background: #b42318; }
    pre { white-space: pre-wrap; word-break: break-word; background: #f5f8fb;
          border-radius: 10px; padding: 12px; min-height: 58px; }
    .command-pending, .command-accepted { color: #8a5a00; }
    .command-completed { color: #176b36; }
    .command-rejected, .conflict { color: #b42318; }
    @media (max-width: 560px) { .grid.three { grid-template-columns: 1fr; } }
  </style>
</head>
<body>
<main>
  <section class="card">
    <h1>OculiDoC 手机管理员端</h1>
    <div class="muted">同一局域网工程验证。桌面程序仍负责患者、设备、会话和任务安全校验。</div>
  </section>

  <section class="card">
    <h2>运行状态</h2>
    <div id="online" class="status">正在连接本地后台……</div>
    <div id="gaze" class="muted"></div>
    <pre id="display">读取患者显示端状态……</pre>
  </section>

  <section class="card">
    <h2>任务设置与启动</h2>
    <select id="run-module"></select>
    <div id="config-form"></div>
    <div id="config-status" class="muted" style="margin-top:12px">正在读取任务设置……</div>
    <div class="grid">
      <button id="save-config" class="secondary">保存设置</button>
      <button id="start-task">保存并直接启动</button>
    </div>
    <div class="grid three">
      <button id="open-display" class="secondary">打开患者端</button>
      <button id="reload-config" class="secondary">重新读取设置</button>
      <button id="stop-task" class="danger">终止当前任务</button>
    </div>
    <pre id="command-status">尚未发送桌面命令。</pre>
  </section>

  <section class="card">
    <h2>文字投屏</h2>
    <textarea id="text" maxlength="500" placeholder="输入需要显示给患者的文字"></textarea>
    <div class="grid">
      <button id="send">投到患者端</button>
      <button id="idle" class="secondary">恢复待机</button>
    </div>
  </section>

  <section class="card">
    <h2>任务预览</h2>
    <select id="preview-module"></select>
    <div class="grid">
      <button id="preview">投送任务预览</button>
      <button id="refresh" class="secondary">刷新状态</button>
    </div>
  </section>
</main>

<script>
const token = __TOKEN__;
const query = "?token=" + encodeURIComponent(token);
let currentRecord = null;
let formDirty = false;

const fields = {
  tracking_ball: [
    {name: "shape", label: "目标形状", type: "select", options: [["circle", "圆形"], ["square", "方形"], ["diamond", "菱形"], ["star", "星形"]]},
    {name: "path", label: "运动轨迹", type: "select", options: [["horizontal", "水平往返"], ["vertical", "垂直往返"], ["circle", "圆周"], ["z", "Z 型"], ["figure_eight", "8 字"], ["random", "平滑随机"]]},
    {name: "effect", label: "动画效果", type: "select", options: [["none", "无"], ["pulse", "呼吸缩放"], ["spin", "旋转"]]},
    {name: "diameter_px", label: "目标直径（px）", type: "number", min: 16, max: 600, step: 1},
    {name: "color", label: "目标颜色", type: "color"},
    {name: "image_path", label: "电脑上的目标图片路径（可留空）", type: "text", nullable: true},
    {name: "background_color", label: "背景颜色", type: "color"},
    {name: "period_seconds", label: "运动周期（秒）", type: "number", min: 1, max: 120, step: 0.5},
    {name: "duration_seconds", label: "总时长（秒）", type: "number", min: 5, max: 3600, step: 1},
    {name: "dwell_time_ms", label: "停留阈值（ms）", type: "number", min: 100, max: 10000, step: 100},
    {name: "dwell_hit_radius_scale", label: "命中范围倍率", type: "number", min: 0.5, max: 2.5, step: 0.05},
    {name: "dwell_feedback_color", label: "命中反馈颜色", type: "color"},
    {name: "dwell_outline_color", label: "目标轮廓颜色", type: "color"},
    {name: "show_gaze_cursor", label: "显示实时视线光标", type: "checkbox"}
  ],
  binary_horizontal: [
    {name: "question_type", label: "问题类型", type: "select", options: [["yes_no", "是否题"], ["question_answer", "问答题"], ["inquiry", "询问题"], ["other", "其他"]]},
    {name: "question", label: "问题文本", type: "textarea"},
    {name: "option_1", label: "选项 1", type: "text"},
    {name: "option_2", label: "选项 2", type: "text"},
    {name: "correct_option_id", label: "正确选项", type: "select", options: [["option_1", "选项 1"], ["option_2", "选项 2"]]},
    {name: "dwell_time_ms", label: "停留阈值（ms）", type: "number", min: 250, max: 10000, step: 100},
    {name: "duration_seconds", label: "任务时长（秒）", type: "number", min: 5, max: 600, step: 1},
    {name: "question_font_family", label: "字体", type: "text"},
    {name: "question_font_size_pt", label: "问题字号（pt）", type: "number", min: 12, max: 120, step: 1},
    {name: "option_font_size_pt", label: "选项字号（pt）", type: "number", min: 12, max: 120, step: 1},
    {name: "neutral_zone_width", label: "中央中性区（0–0.6）", type: "number", min: 0, max: 0.6, step: 0.01},
    {name: "randomize_sides", label: "随机交换左右位置", type: "checkbox"}
  ]
};

async function request(path, options = {}) {
  const response = await fetch(path + query, {
    headers: {"Content-Type": "application/json"},
    ...options
  });
  const text = await response.text();
  let payload = text;
  try { payload = text ? JSON.parse(text) : null; } catch (_) {}
  if (!response.ok) {
    const error = new Error(typeof payload === "string" ? payload : JSON.stringify(payload));
    error.status = response.status;
    error.payload = payload;
    throw error;
  }
  return payload;
}

function refillSelect(select, modules, predicate) {
  const selected = select.value;
  select.innerHTML = "";
  modules.filter(predicate).forEach((module) => {
    const option = document.createElement("option");
    option.value = module.module_id;
    option.textContent = module.title;
    select.appendChild(option);
  });
  if ([...select.options].some((option) => option.value === selected)) {
    select.value = selected;
  }
}

function refreshCorrectOptionState() {
  const questionType = document.querySelector('[data-field="question_type"]');
  const correct = document.querySelector('[data-field="correct_option_id"]');
  if (questionType && correct) {
    correct.disabled = !["yes_no", "question_answer"].includes(questionType.value);
  }
}

function renderConfig(record) {
  currentRecord = record;
  formDirty = false;
  const container = document.getElementById("config-form");
  container.innerHTML = "";
  (fields[record.module_id] || []).forEach((definition) => {
    const label = document.createElement("label");
    label.textContent = definition.label;
    const input = definition.type === "textarea" ? document.createElement("textarea") :
      definition.type === "select" ? document.createElement("select") : document.createElement("input");
    input.dataset.field = definition.name;
    input.dataset.kind = definition.type;
    input.dataset.nullable = definition.nullable ? "true" : "false";
    if (definition.type === "select") {
      definition.options.forEach(([value, text]) => {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = text;
        input.appendChild(option);
      });
    } else if (definition.type !== "textarea") {
      input.type = definition.type;
    }
    ["min", "max", "step"].forEach((name) => {
      if (definition[name] !== undefined) input[name] = definition[name];
    });
    const value = record.config[definition.name];
    if (definition.type === "checkbox") {
      input.checked = Boolean(value);
      label.className = "check-label";
      label.textContent = "";
      label.appendChild(input);
      label.appendChild(document.createTextNode(definition.label));
    } else {
      input.value = value === null || value === undefined ? "" : value;
      label.appendChild(input);
    }
    input.addEventListener("input", () => {
      formDirty = true;
      document.getElementById("config-status").textContent =
        "有尚未保存的修改 · 当前版本 " + currentRecord.revision;
      refreshCorrectOptionState();
    });
    container.appendChild(label);
  });
  refreshCorrectOptionState();
  document.getElementById("config-status").className = "muted";
  document.getElementById("config-status").textContent =
    "已同步设置版本 " + record.revision;
}

function collectConfig() {
  const config = {...currentRecord.config};
  document.querySelectorAll("#config-form [data-field]").forEach((input) => {
    const name = input.dataset.field;
    if (input.dataset.kind === "checkbox") {
      config[name] = input.checked;
    } else if (input.dataset.kind === "number") {
      config[name] = Number(input.value);
    } else if (input.dataset.nullable === "true" && !input.value.trim()) {
      config[name] = null;
    } else {
      config[name] = input.value;
    }
  });
  if (!["yes_no", "question_answer"].includes(config.question_type)) {
    config.correct_option_id = null;
  }
  return config;
}

async function loadTaskConfig(force = false) {
  if (formDirty && !force) return currentRecord;
  const moduleId = document.getElementById("run-module").value;
  if (!moduleId) return null;
  const record = await request("/api/v1/task-configs/" + encodeURIComponent(moduleId));
  renderConfig(record);
  return record;
}

async function saveTaskConfig() {
  if (!currentRecord) return null;
  try {
    const record = await request(
      "/api/v1/task-configs/" + encodeURIComponent(currentRecord.module_id),
      {method: "PUT", body: JSON.stringify({revision: currentRecord.revision, config: collectConfig()})}
    );
    renderConfig(record);
    return record;
  } catch (error) {
    if (error.status === 409 && error.payload) {
      renderConfig(error.payload);
      const status = document.getElementById("config-status");
      status.className = "muted conflict";
      status.textContent = "保存冲突：另一端已更新设置，已载入最新版本，请重新确认。";
    }
    throw error;
  }
}

function renderLatestCommand(commands) {
  const target = document.getElementById("command-status");
  if (!commands || commands.length === 0) {
    target.textContent = "尚未发送桌面命令。";
    target.className = "";
    return;
  }
  const command = commands[0];
  const labels = {pending: "等待桌面接收", accepted: "桌面已接收", completed: "执行完成", rejected: "已拒绝"};
  target.textContent = "命令：" + command.command_type +
    "\n状态：" + (labels[command.status] || command.status) + "\n说明：" + command.message;
  target.className = "command-" + command.status;
}

async function refresh() {
  try {
    const runtime = await request("/api/v1/runtime");
    document.getElementById("online").textContent = "本地后台在线";
    document.getElementById("gaze").textContent = "眼动源：" + runtime.gaze_source;
    document.getElementById("display").textContent =
      runtime.patient_display.text + "\n\n状态：" + runtime.patient_display.mode;
    refillSelect(document.getElementById("preview-module"), runtime.modules, () => true);
    refillSelect(
      document.getElementById("run-module"), runtime.modules,
      (module) => module.remote_start_available
    );
    const moduleId = document.getElementById("run-module").value;
    const selected = runtime.modules.find((module) => module.module_id === moduleId);
    if (!currentRecord || currentRecord.module_id !== moduleId) {
      await loadTaskConfig(true);
    } else if (selected && selected.config_revision !== currentRecord.revision) {
      if (formDirty) {
        const status = document.getElementById("config-status");
        status.className = "muted conflict";
        status.textContent = "另一端已更新设置；请保存以查看冲突，或点“重新读取设置”。";
      } else {
        await loadTaskConfig(true);
      }
    }
    renderLatestCommand(runtime.commands);
  } catch (error) {
    document.getElementById("online").textContent = "连接失败：" + error;
  }
}

async function submitDesktopCommand(commandType, moduleId = null, configRevision = null) {
  const payload = {command_type: commandType};
  if (moduleId) payload.module_id = moduleId;
  if (configRevision !== null) payload.config_revision = configRevision;
  const command = await request("/api/v1/commands", {
    method: "POST", body: JSON.stringify(payload)
  });
  renderLatestCommand([command]);
  await refresh();
}

document.getElementById("run-module").addEventListener("change", async () => {
  formDirty = false;
  await loadTaskConfig(true);
});
document.getElementById("save-config").addEventListener("click", saveTaskConfig);
document.getElementById("reload-config").addEventListener("click", async () => {
  formDirty = false;
  await loadTaskConfig(true);
});
document.getElementById("start-task").addEventListener("click", async () => {
  const record = await saveTaskConfig();
  if (record) await submitDesktopCommand("start_task", record.module_id, record.revision);
});
document.getElementById("open-display").addEventListener("click", async () => {
  await submitDesktopCommand("open_patient_display");
});
document.getElementById("stop-task").addEventListener("click", async () => {
  await submitDesktopCommand("stop_task", document.getElementById("run-module").value);
});
document.getElementById("send").addEventListener("click", async () => {
  const text = document.getElementById("text").value.trim();
  if (!text) { alert("请输入投屏文字。"); return; }
  await request("/api/v1/patient-display/text", {method: "POST", body: JSON.stringify({text})});
  await refresh();
});
document.getElementById("idle").addEventListener("click", async () => {
  await request("/api/v1/patient-display/idle", {method: "POST", body: "{}"});
  await refresh();
});
document.getElementById("preview").addEventListener("click", async () => {
  await request("/api/v1/tasks/preview", {
    method: "POST", body: JSON.stringify({module_id: document.getElementById("preview-module").value})
  });
  await refresh();
});
document.getElementById("refresh").addEventListener("click", refresh);
refresh();
setInterval(refresh, 1000);
</script>
</body>
</html>
"""


def mobile_control_html(token: str) -> str:
    """Return the authenticated mobile-control page."""
    return _PAGE.replace("__TOKEN__", json.dumps(token))
