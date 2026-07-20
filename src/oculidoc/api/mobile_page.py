"""Single-page mobile controller served by the local API."""

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
    textarea, select { width: 100%; box-sizing: border-box; border: 1px solid #bfd3e4;
                       border-radius: 10px; padding: 12px; font-size: 16px; }
    textarea { min-height: 120px; resize: vertical; }
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
    .command-rejected { color: #b42318; }
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
    <h2>桌面管理员命令</h2>
    <select id="run-module"></select>
    <div class="grid three">
      <button id="open-display" class="secondary">打开患者端</button>
      <button id="start-task">请求启动</button>
      <button id="stop-task" class="danger">终止当前任务</button>
    </div>
    <div class="muted" style="margin-top:10px">
      本轮启动后仍在电脑端显示原设置窗口；下一阶段同步全部任务参数并支持手机直接确认。
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

async function request(path, options = {}) {
  const response = await fetch(path + query, {
    headers: {"Content-Type": "application/json"},
    ...options
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return await response.json();
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
  if (selected) {
    select.value = selected;
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
  const labels = {
    pending: "等待桌面接收",
    accepted: "桌面已接收",
    completed: "执行完成",
    rejected: "已拒绝"
  };
  target.textContent =
    "命令：" + command.command_type +
    "\n状态：" + (labels[command.status] || command.status) +
    "\n说明：" + command.message;
  target.className = "command-" + command.status;
}

async function refresh() {
  try {
    const runtime = await request("/api/v1/runtime");
    document.getElementById("online").textContent = "本地后台在线";
    document.getElementById("gaze").textContent =
      "眼动源：" + runtime.gaze_source;
    document.getElementById("display").textContent =
      runtime.patient_display.text + "\n\n状态：" + runtime.patient_display.mode;

    refillSelect(
      document.getElementById("preview-module"),
      runtime.modules,
      () => true
    );
    refillSelect(
      document.getElementById("run-module"),
      runtime.modules,
      (module) => module.remote_start_available
    );
    renderLatestCommand(runtime.commands);
  } catch (error) {
    document.getElementById("online").textContent = "连接失败：" + error;
  }
}

async function submitDesktopCommand(commandType, moduleId = null) {
  const payload = {command_type: commandType};
  if (moduleId) {
    payload.module_id = moduleId;
  }
  const command = await request("/api/v1/commands", {
    method: "POST",
    body: JSON.stringify(payload)
  });
  renderLatestCommand([command]);
  await refresh();
}

document.getElementById("open-display").addEventListener("click", async () => {
  await submitDesktopCommand("open_patient_display");
});

document.getElementById("start-task").addEventListener("click", async () => {
  const moduleId = document.getElementById("run-module").value;
  await submitDesktopCommand("start_task", moduleId);
});

document.getElementById("stop-task").addEventListener("click", async () => {
  const moduleId = document.getElementById("run-module").value;
  await submitDesktopCommand("stop_task", moduleId);
});

document.getElementById("send").addEventListener("click", async () => {
  const text = document.getElementById("text").value.trim();
  if (!text) {
    alert("请输入投屏文字。");
    return;
  }
  await request("/api/v1/patient-display/text", {
    method: "POST",
    body: JSON.stringify({text})
  });
  await refresh();
});

document.getElementById("idle").addEventListener("click", async () => {
  await request("/api/v1/patient-display/idle", {
    method: "POST",
    body: "{}"
  });
  await refresh();
});

document.getElementById("preview").addEventListener("click", async () => {
  const moduleId = document.getElementById("preview-module").value;
  await request("/api/v1/tasks/preview", {
    method: "POST",
    body: JSON.stringify({module_id: moduleId})
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
