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
    .grid.four { grid-template-columns: 1fr 1fr 1fr 1fr; }
    button { border: 0; border-radius: 10px; padding: 13px 12px; font-size: 16px;
             font-weight: 700; background: #1565c0; color: white; }
    button.secondary { background: #edf4fb; color: #184e77; border: 1px solid #bfd3e4; }
    button.danger { background: #b42318; }
    pre { white-space: pre-wrap; word-break: break-word; background: #f5f8fb;
          border-radius: 10px; padding: 12px; min-height: 58px; }
    .command-pending, .command-accepted { color: #8a5a00; }
    .command-completed { color: #176b36; }
    .command-rejected, .conflict { color: #b42318; }
    @media (max-width: 680px) { .grid.four { grid-template-columns: 1fr 1fr; } }
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
    <div class="grid four">
      <button id="open-display" class="secondary">打开患者端</button>
      <button id="reload-config" class="secondary">重新读取设置</button>
      <button id="replay-speech" class="secondary">重播语音</button>
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
let multipleChoiceTemplates = [];
let imageAssets = [];

const binaryFields = [
  {name: "fixed_form_size", label: "标准模式卷", type: "select", options: [[0, "自定义题组 / 单题"], [6, "固定 6 题（前半客观、后半开放）"], [8, "固定 8 题（前半客观、后半开放）"], [10, "固定 10 题（前半客观、后半开放）"]]},
  {name: "question_template_ids", label: "连续题目（可多选；不选则只运行下方单题）", type: "multi-select", options: []},
  {name: "question_count", label: "随机抽取题数（0 = 全部已选）", type: "number", min: 0, max: 200, step: 1},
  {name: "randomize_question_order", label: "每次任务随机抽题并排列", type: "checkbox"},
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
  {name: "randomize_sides", label: "随机交换选项位置", type: "checkbox"},
  {name: "show_gaze_cursor", label: "患者屏幕显示实时视线光标", type: "checkbox"}
];

const fields = {
  tracking_ball: [
    {name: "shape", label: "目标形状", type: "select", options: [["circle", "圆形"], ["square", "方形"], ["diamond", "菱形"], ["star", "星形"]]},
    {name: "path", label: "运动轨迹", type: "select", options: [["horizontal", "水平往返"], ["vertical", "垂直往返"], ["circle", "圆周"], ["z", "Z 型"], ["figure_eight", "8 字"], ["random", "平滑随机"]]},
    {name: "horizontal_position", label: "水平轨迹高度", type: "select", options: [["top", "屏幕上方"], ["middle", "屏幕中间"], ["bottom", "屏幕下方"]]},
    {name: "vertical_position", label: "垂直轨迹位置", type: "select", options: [["left", "屏幕左侧"], ["center", "屏幕中间"], ["right", "屏幕右侧"]]},
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
  binary_horizontal: binaryFields,
  binary_vertical: binaryFields,
  multiple_choice: [
    {name: "template_id", label: "固定多选题库", type: "select", nullable: true, options: [["", "自定义多选题"]]},
    {name: "question", label: "问题文字", type: "textarea"},
    {name: "option_count", label: "选项数量", type: "number", min: 2, max: 12, step: 1},
    {name: "option_1", label: "选项 1", type: "text"},
    {name: "option_2", label: "选项 2", type: "text"},
    {name: "option_3", label: "选项 3", type: "text"},
    {name: "option_4", label: "选项 4", type: "text"},
    {name: "option_5", label: "选项 5", type: "text"},
    {name: "option_6", label: "选项 6", type: "text"},
    {name: "option_7", label: "选项 7", type: "text"},
    {name: "option_8", label: "选项 8", type: "text"},
    {name: "option_9", label: "选项 9", type: "text"},
    {name: "option_10", label: "选项 10", type: "text"},
    {name: "option_11", label: "选项 11", type: "text"},
    {name: "option_12", label: "选项 12", type: "text"},
    {name: "layout", label: "排列方式", type: "select", options: [["grid", "分区宫格"], ["ring", "环形排列（最多 6 项）"]]},
    {name: "grid_shape", label: "宫格行列", type: "select", options: [["auto", "按选项数自动选择"], ["2x2", "2×2"], ["2x3", "2×3"], ["2x4", "2×4"], ["3x2", "3×2"], ["3x3", "3×3"], ["3x4", "3×4"]]},
    {name: "dwell_time_ms", label: "停留阈值（ms）", type: "number", min: 250, max: 10000, step: 100},
    {name: "duration_seconds", label: "最长任务时长（秒）", type: "number", min: 5, max: 3600, step: 1},
    {name: "question_font_size_pt", label: "问题字号（pt）", type: "number", min: 20, max: 120, step: 1},
    {name: "option_font_size_pt", label: "选项字号（pt）", type: "number", min: 20, max: 120, step: 1},
    {name: "randomize_positions", label: "每次呈现随机交换选项位置", type: "checkbox"},
    {name: "show_gaze_cursor", label: "患者屏幕显示实时视线光标", type: "checkbox"}
  ],
  image_choice: [
    {name: "category_filters", label: "图片类别（可多选；不选 = 全部）", type: "multi-select", options: []},
    {name: "style_filters", label: "图片风格（可多选；不选 = 全部）", type: "multi-select", options: []},
    {name: "question_count", label: "本次随机题数", type: "number", min: 1, max: 100, step: 1},
    {name: "dwell_time_ms", label: "停留阈值（ms）", type: "number", min: 250, max: 10000, step: 100},
    {name: "duration_seconds", label: "每题最长时长（秒）", type: "number", min: 5, max: 600, step: 1},
    {name: "question_font_size_pt", label: "问题字号（pt）", type: "number", min: 20, max: 120, step: 1},
    {name: "randomize_sides", label: "每题随机交换左右图片", type: "checkbox"},
    {name: "show_gaze_cursor", label: "患者屏幕显示实时视线光标", type: "checkbox"}
  ],
  instruction_fixation: [
    {name: "target_description", label: "指令中的目标描述", type: "text"},
    {name: "target_shape", label: "目标形状", type: "select", options: [["circle", "圆形"], ["square", "方形"], ["diamond", "菱形"], ["star", "星形"]]},
    {name: "target_color", label: "目标颜色", type: "color"},
    {name: "distractor_shape", label: "干扰形状", type: "select", options: [["circle", "圆形"], ["square", "方形"], ["diamond", "菱形"], ["star", "星形"]]},
    {name: "distractor_color", label: "干扰颜色", type: "color"},
    {name: "background_color", label: "背景颜色", type: "color"},
    {name: "position_ids", label: "可用屏幕 AOI（可多选）", type: "multi-select", options: [["top_left", "左上"], ["top_center", "上中"], ["top_right", "右上"], ["middle_left", "左中"], ["center", "中央"], ["middle_right", "右中"], ["bottom_left", "左下"], ["bottom_center", "下中"], ["bottom_right", "右下"]]},
    {name: "target_only_trial_count", label: "仅目标试次数", type: "number", min: 0, max: 100, step: 1},
    {name: "distractor_trial_count", label: "目标 + 干扰试次数", type: "number", min: 0, max: 100, step: 1},
    {name: "no_target_trial_count", label: "无目标试次数", type: "number", min: 0, max: 100, step: 1},
    {name: "distractor_count", label: "每试次干扰数", type: "number", min: 1, max: 6, step: 1},
    {name: "target_size_px", label: "刺激大小（px）", type: "number", min: 40, max: 600, step: 1},
    {name: "dwell_time_ms", label: "持续注视阈值（ms）", type: "number", min: 250, max: 10000, step: 100},
    {name: "trial_duration_seconds", label: "每试次最长时长（秒）", type: "number", min: 3, max: 120, step: 1},
    {name: "instruction_font_size_pt", label: "指令字号（pt）", type: "number", min: 20, max: 120, step: 1},
    {name: "randomize_trial_order", label: "随机试次顺序并平衡目标位置", type: "checkbox"},
    {name: "show_gaze_cursor", label: "患者屏幕显示实时视线光标", type: "checkbox"}
  ],
  gaze_games: [
    {name: "default_mode", label: "本次游戏模式", type: "select", options: [["garden", "点亮花园"], ["treasure_hunt", "视觉寻宝"], ["starlight_route", "星光航线"]]},
    {name: "garden.object_count", label: "花园 · 花朵数量", type: "number", min: 2, max: 6, step: 1},
    {name: "garden.object_diameter_px", label: "花园 · 花朵直径（px）", type: "number", min: 160, max: 480, step: 1},
    {name: "garden.dwell_time_ms", label: "花园 · 持续注视阈值（ms）", type: "number", min: 250, max: 3000, step: 50},
    {name: "garden.baseline_seconds", label: "花园 · 基线块（秒）", type: "number", min: 5, max: 30, step: 1},
    {name: "garden.contingent_block_seconds", label: "花园 · 联动块（秒）", type: "number", min: 10, max: 120, step: 1},
    {name: "garden.replay_block_seconds", label: "花园 · 回放块（秒）", type: "number", min: 10, max: 120, step: 1},
    {name: "garden.reward_animation_ms", label: "花园 · 奖励动画（ms）", type: "number", min: 500, max: 3000, step: 100},
    {name: "garden.sound_enabled", label: "花园 · 启用温和语音反馈", type: "checkbox"},
    {name: "garden.show_gaze_cursor", label: "花园 · 显示实时视线光标", type: "checkbox"},
    {name: "treasure_hunt.preview_trial_count", label: "寻宝 · 预览搜索试次", type: "number", min: 0, max: 30, step: 1},
    {name: "treasure_hunt.popout_trial_count", label: "寻宝 · 突现试次", type: "number", min: 0, max: 30, step: 1},
    {name: "treasure_hunt.catch_trial_count", label: "寻宝 · 目标缺失试次", type: "number", min: 0, max: 10, step: 1},
    {name: "treasure_hunt.distractor_count", label: "寻宝 · 干扰图片数量", type: "number", min: 1, max: 5, step: 1},
    {name: "treasure_hunt.target_preview_ms", label: "寻宝 · 目标预览（ms）", type: "number", min: 500, max: 5000, step: 100},
    {name: "treasure_hunt.interstimulus_ms", label: "寻宝 · 预览后间隔（ms）", type: "number", min: 250, max: 2000, step: 50},
    {name: "treasure_hunt.dwell_time_ms", label: "寻宝 · 持续注视阈值（ms）", type: "number", min: 250, max: 5000, step: 50},
    {name: "treasure_hunt.trial_duration_seconds", label: "寻宝 · 每试次最长呈现（秒）", type: "number", min: 3, max: 60, step: 1},
    {name: "treasure_hunt.reward_animation_ms", label: "寻宝 · 奖励动画（ms）", type: "number", min: 500, max: 3000, step: 100},
    {name: "treasure_hunt.category_filters", label: "寻宝 · 图片类别（可多选）", type: "multi-select", options: []},
    {name: "treasure_hunt.style_filters", label: "寻宝 · 图片风格（可多选）", type: "multi-select", options: []},
    {name: "treasure_hunt.randomize_trial_order", label: "寻宝 · 随机试次顺序", type: "checkbox"},
    {name: "treasure_hunt.sound_enabled", label: "寻宝 · 启用温和语音反馈", type: "checkbox"},
    {name: "treasure_hunt.show_gaze_cursor", label: "寻宝 · 显示实时视线光标", type: "checkbox"},
    {name: "starlight_route.round_count", label: "星光 · 总轮数", type: "number", min: 6, max: 120, step: 1},
    {name: "starlight_route.initial_level", label: "星光 · 起始等级", type: "number", min: 1, max: 10, step: 1},
    {name: "starlight_route.dwell_time_ms", label: "星光 · 持续注视阈值（ms）", type: "number", min: 250, max: 3000, step: 50},
    {name: "starlight_route.trial_duration_seconds", label: "星光 · 每轮最长（秒）", type: "number", min: 3, max: 30, step: 1},
    {name: "starlight_route.edge_probe_interval", label: "星光 · 边缘试探间隔（轮）", type: "number", min: 2, max: 10, step: 1},
    {name: "starlight_route.sound_enabled", label: "星光 · 启用温和语音反馈", type: "checkbox"},
    {name: "starlight_route.show_gaze_cursor", label: "星光 · 显示实时视线光标", type: "checkbox"}
  ],
  visual_preference: [
    {name: "presentation_seconds", label: "单次图片呈现（秒）", type: "number", min: 3, max: 15, step: 1},
    {name: "center_cue_ms", label: "中央提示（ms）", type: "number", min: 0, max: 3000, step: 100},
    {name: "intertrial_ms", label: "试次间隔（ms）", type: "number", min: 500, max: 5000, step: 100},
    {name: "minimum_trial_valid_ratio", label: "最低试次有效率", type: "number", min: 0.2, max: 0.9, step: 0.05},
    {name: "randomize_pair_order", label: "随机排列刺激对", type: "checkbox"},
    {name: "sound_intro_enabled", label: "启用开始语音", type: "checkbox"},
    {name: "show_gaze_cursor", label: "患者屏幕显示实时视线光标", type: "checkbox"}
  ],
  screen_keyboard: [
    {name: "input_mode", label: "输入模式", type: "select", options: [["direct", "直观直选模式"], ["advanced", "进阶模式（拼音）"]]},
    {name: "dwell_time_ms", label: "停留阈值（ms）", type: "number", min: 250, max: 10000, step: 100},
    {name: "duration_seconds", label: "任务时长（秒）", type: "number", min: 5, max: 3600, step: 1},
    {name: "enable_tone_step", label: "启用声调选择步骤", type: "checkbox"},
    {name: "output_font_size_pt", label: "上半屏输出字号（pt）", type: "number", min: 20, max: 120, step: 1},
    {name: "instruction_font_size_pt", label: "指示文字字号（pt）", type: "number", min: 20, max: 120, step: 1},
    {name: "option_font_size_pt", label: "下半屏选项字号（pt）", type: "number", min: 20, max: 120, step: 1},
    {name: "show_gaze_cursor", label: "患者屏幕显示实时视线光标", type: "checkbox"}
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
  const choices = modules.filter(predicate);
  const currentSignature = [...select.options]
    .map((option) => option.value + "\u0000" + option.textContent).join("\u0001");
  const nextSignature = choices
    .map((module) => module.module_id + "\u0000" + module.title).join("\u0001");
  if (currentSignature === nextSignature) return;
  select.innerHTML = "";
  choices.forEach((module) => {
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

function applyMultipleChoiceTemplate(templateId) {
  const template = multipleChoiceTemplates.find((item) => item.template_id === templateId);
  if (!template) return;
  const values = {
    question: template.question,
    option_count: template.options.length,
    layout: "grid",
    grid_shape: template.grid_shape
  };
  for (let index = 1; index <= 12; index += 1) {
    values["option_" + index] = template.options[index - 1] || "";
  }
  Object.entries(values).forEach(([name, value]) => {
    const input = document.querySelector('[data-field="' + name + '"]');
    if (input) input.value = value;
  });
}

function getConfigValue(config, path) {
  return path.split(".").reduce((value, key) =>
    value && typeof value === "object" ? value[key] : undefined, config);
}

function setConfigValue(config, path, value) {
  const keys = path.split(".");
  let target = config;
  keys.slice(0, -1).forEach((key) => {
    if (!target[key] || typeof target[key] !== "object") target[key] = {};
    target = target[key];
  });
  target[keys[keys.length - 1]] = value;
}

function markConfigDirty() {
  formDirty = true;
  document.getElementById("config-status").textContent =
    "有尚未保存的修改 · 当前版本 " + currentRecord.revision;
}

function renderPreferencePairs(record, container) {
  const heading = document.createElement("label");
  heading.textContent = "刺激对（勾选 2–12 组；每组会自动换边呈现）";
  container.appendChild(heading);
  const pairs = Array.isArray(record.config.pairs) ? record.config.pairs : [];
  const selected = new Set(Array.isArray(record.config.pair_ids) ? record.config.pair_ids : []);
  pairs.forEach((pair) => {
    const row = document.createElement("label");
    row.className = "check-label";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = selected.has(pair.pair_id);
    checkbox.addEventListener("change", () => {
      const ids = new Set(currentRecord.config.pair_ids || []);
      if (checkbox.checked) ids.add(pair.pair_id); else ids.delete(pair.pair_id);
      currentRecord.config.pair_ids = [...ids];
      markConfigDirty();
    });
    const byId = Object.fromEntries(imageAssets.map((asset) => [asset.image_id, asset.label]));
    row.appendChild(checkbox);
    row.appendChild(document.createTextNode(
      (pair.pair_label || "未命名刺激对") + " · " +
      (byId[pair.image_a_id] || pair.image_a_id) + " ↔ " +
      (byId[pair.image_b_id] || pair.image_b_id)
    ));
    container.appendChild(row);
  });

  const imageA = document.createElement("select");
  const imageB = document.createElement("select");
  imageAssets.forEach((asset) => {
    [imageA, imageB].forEach((select) => {
      const option = document.createElement("option");
      option.value = asset.image_id;
      option.textContent = asset.label + " · " + asset.category + " · " + asset.style;
      select.appendChild(option);
    });
  });
  if (imageB.options.length > 1) imageB.selectedIndex = 1;
  const pairLabel = document.createElement("input");
  pairLabel.type = "text";
  pairLabel.placeholder = "刺激对标签（可留空）";
  const add = document.createElement("button");
  add.type = "button";
  add.className = "secondary";
  add.textContent = "新增并选中刺激对";
  add.addEventListener("click", () => {
    if (!imageA.value || !imageB.value || imageA.value === imageB.value) {
      alert("请选择两张不同的图片。");
      return;
    }
    currentRecord.config = collectConfig();
    const byId = Object.fromEntries(imageAssets.map((asset) => [asset.image_id, asset.label]));
    const pairId = "mobile-pair-" + Date.now() + "-" + Math.random().toString(16).slice(2);
    currentRecord.config.pairs = [...(currentRecord.config.pairs || []), {
      pair_id: pairId,
      image_a_id: imageA.value,
      image_b_id: imageB.value,
      pair_label: pairLabel.value.trim() ||
        (byId[imageA.value] || "A") + " / " + (byId[imageB.value] || "B"),
      comparison_type: "generic_interest",
      matching_note: ""
    }];
    currentRecord.config.pair_ids = [...(currentRecord.config.pair_ids || []), pairId];
    markConfigDirty();
    renderConfig(currentRecord, true);
  });
  container.appendChild(imageA);
  container.appendChild(imageB);
  container.appendChild(pairLabel);
  container.appendChild(add);
}

function renderConfig(record, preserveDirty = false) {
  currentRecord = record;
  if (!preserveDirty) formDirty = false;
  const container = document.getElementById("config-form");
  container.innerHTML = "";
  if (record.module_id === "visual_preference") {
    renderPreferencePairs(record, container);
  }
  (fields[record.module_id] || []).forEach((definition) => {
    const label = document.createElement("label");
    label.textContent = definition.label;
    const input = definition.type === "textarea" ? document.createElement("textarea") :
      ["select", "multi-select"].includes(definition.type) ? document.createElement("select") :
      document.createElement("input");
    input.dataset.field = definition.name;
    input.dataset.kind = definition.type;
    input.dataset.nullable = definition.nullable ? "true" : "false";
    if (["select", "multi-select"].includes(definition.type)) {
      input.multiple = definition.type === "multi-select";
      if (input.multiple) input.size = Math.min(8, Math.max(3, definition.options.length));
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
    const value = getConfigValue(record.config, definition.name);
    if (definition.type === "checkbox") {
      input.checked = Boolean(value);
      label.className = "check-label";
      label.textContent = "";
      label.appendChild(input);
      label.appendChild(document.createTextNode(definition.label));
    } else if (definition.type === "multi-select") {
      const selectedValues = Array.isArray(value) ? value : [];
      [...input.options].forEach((option) => {
        option.selected = selectedValues.includes(option.value);
      });
      label.appendChild(input);
    } else {
      input.value = value === null || value === undefined ? "" : value;
      label.appendChild(input);
    }
    input.addEventListener("input", () => {
      if (record.module_id === "multiple_choice" && definition.name === "template_id") {
        applyMultipleChoiceTemplate(input.value);
      }
      markConfigDirty();
      refreshCorrectOptionState();
    });
    container.appendChild(label);
  });
  refreshCorrectOptionState();
  document.getElementById("config-status").className = "muted";
  document.getElementById("config-status").textContent = preserveDirty
    ? "有尚未保存的修改 · 当前版本 " + record.revision
    : "已同步设置版本 " + record.revision;
}

function collectConfig() {
  const config = JSON.parse(JSON.stringify(currentRecord.config));
  document.querySelectorAll("#config-form [data-field]").forEach((input) => {
    const name = input.dataset.field;
    if (input.dataset.kind === "checkbox") {
      setConfigValue(config, name, input.checked);
    } else if (input.dataset.kind === "multi-select") {
      setConfigValue(config, name, [...input.selectedOptions].map((option) => option.value));
    } else if (input.dataset.kind === "number" || name === "fixed_form_size") {
      setConfigValue(config, name, Number(input.value));
    } else if (input.dataset.nullable === "true" && !input.value.trim()) {
      setConfigValue(config, name, null);
    } else {
      setConfigValue(config, name, input.value);
    }
  });
  if ("question_type" in config && !["yes_no", "question_answer"].includes(config.question_type)) {
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
    const sequenceField = binaryFields.find((definition) =>
      definition.name === "question_template_ids"
    );
    sequenceField.options = (runtime.question_bank || []).map((question) => [
      question.template_id, question.display_label
    ]);
    multipleChoiceTemplates = runtime.multiple_choice_templates || [];
    const multipleTemplateField = fields.multiple_choice.find((definition) =>
      definition.name === "template_id"
    );
    multipleTemplateField.options = [["", "自定义多选题"]].concat(
      multipleChoiceTemplates.map((template) => [
        template.template_id, template.display_label
      ])
    );
    imageAssets = runtime.image_library || [];
    const imageFields = fields.image_choice;
    const categoryField = imageFields.find((definition) => definition.name === "category_filters");
    const styleField = imageFields.find((definition) => definition.name === "style_filters");
    categoryField.options = [...new Set(imageAssets.map((asset) => asset.category))]
      .sort().map((value) => [value, value]);
    styleField.options = [...new Set(imageAssets.map((asset) => asset.style))]
      .sort().map((value) => [value, value]);
    const huntFields = fields.gaze_games;
    const huntCategory = huntFields.find((definition) =>
      definition.name === "treasure_hunt.category_filters"
    );
    const huntStyle = huntFields.find((definition) =>
      definition.name === "treasure_hunt.style_filters"
    );
    huntCategory.options = categoryField.options;
    huntStyle.options = styleField.options;
    const displayLabels = {
      closed: "已关闭", idle: "待机", ready: "准备", preview: "提示",
      running: "任务进行中", paused: "已暂停", result: "任务结束", error: "异常"
    };
    document.getElementById("online").textContent = "本地后台在线";
    const preflight = runtime.gaze_preflight;
    const gazeLabels = {
      auto: "硬件自动检测", mock: "工程模拟测试",
      gaze_collect_legacy: "第三方兼容",
      just_need_to_see_bundle: "Tobii DLL兼容",
      tobii_hospital_bridge: "原监听兼容",
      tobii_stream_engine: "Tobii 原生 Stream",
      tobii_legacy_bridge: "第三方兼容"
    };
    let gazeText = "眼动源：" + (gazeLabels[runtime.gaze_source] || runtime.gaze_source);
    if (preflight && preflight.source === runtime.gaze_source) {
      gazeText += " · " + Math.round(preflight.sample_rate_hz) + " Hz" +
        " · 有效率 " + Math.round(preflight.valid_ratio * 100) + "%";
      if (preflight.device_url) gazeText += "\n设备 URL：" + preflight.device_url;
    }
    document.getElementById("gaze").textContent = gazeText;
    document.getElementById("display").textContent =
      runtime.patient_display.text + "\n\n状态：" +
      (displayLabels[runtime.patient_display.mode] || runtime.patient_display.mode);
    document.getElementById("idle").disabled =
      ["ready", "running", "paused"].includes(runtime.patient_display.mode);
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

async function submitDesktopCommand(
  commandType, moduleId = null, configRevision = null, gameMode = null
) {
  const payload = {command_type: commandType};
  if (moduleId) payload.module_id = moduleId;
  if (configRevision !== null) payload.config_revision = configRevision;
  if (gameMode !== null) payload.game_mode = gameMode;
  const command = await request("/api/v1/commands", {
    method: "POST", body: JSON.stringify(payload)
  });
  renderLatestCommand([command]);
  await refresh();
}

function launchSummary(record) {
  const config = record.config;
  if (record.module_id === "gaze_games" && config.default_mode === "garden") {
    const garden = config.garden;
    const seconds = garden.baseline_seconds +
      garden.contingent_block_seconds * 2 + garden.replay_block_seconds;
    return "一级入口：眼动游戏\n游戏模式：点亮花园\n总协议时长：" + seconds +
      " 秒\n持续注视阈值：" + garden.dwell_time_ms + " ms\n" +
      "区块：基线 → 联动 1 → 回放 → 联动 2\n声音：" +
      (garden.sound_enabled ? "开启" : "关闭") + "\n随机种子：" +
      (garden.randomization_seed === null ? "运行时生成" : garden.randomization_seed);
  }
  if (record.module_id === "gaze_games" && config.default_mode === "starlight_route") {
    const route = config.starlight_route;
    return "一级入口：眼动游戏\n游戏模式：星光航线\n总轮数：" + route.round_count +
      "\n起始等级：" + route.initial_level + "\n每轮最长：" +
      route.trial_duration_seconds + " 秒\n边缘试探：每 " + route.edge_probe_interval +
      " 轮\n低质量数据：不降级";
  }
  if (record.module_id === "gaze_games") {
    const hunt = config.treasure_hunt;
    const trials = hunt.preview_trial_count + hunt.popout_trial_count + hunt.catch_trial_count;
    return "一级入口：眼动游戏\n游戏模式：视觉寻宝\n试次数：" + trials +
      "\n每试次最长：" + hunt.trial_duration_seconds + " 秒\n持续注视阈值：" +
      hunt.dwell_time_ms + " ms\n声音：" + (hunt.sound_enabled ? "开启" : "关闭") +
      "\n随机种子：" +
      (hunt.randomization_seed === null ? "运行时生成" : hunt.randomization_seed);
  }
  if (record.module_id === "visual_preference") {
    const pairs = Array.isArray(config.pair_ids) ? config.pair_ids.length : 0;
    return "一级入口：视觉偏好\n刺激对：" + pairs + " 组（共 " + pairs * 2 +
      " 试次，逐对换边）\n单次呈现：" + config.presentation_seconds +
      " 秒\n声音：" + (config.sound_intro_enabled ? "开启" : "关闭") +
      "\n随机种子：" +
      (config.randomization_seed === null ? "运行时生成" : config.randomization_seed);
  }
  return "任务：" + record.module_id + "\n设置版本：" + record.revision;
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
  if (record) {
    const gameMode = record.module_id === "gaze_games" ? record.config.default_mode : null;
    if (confirm(launchSummary(record) + "\n\n确认按以上设置直接启动？")) {
      await submitDesktopCommand("start_task", record.module_id, record.revision, gameMode);
    }
  }
});
document.getElementById("open-display").addEventListener("click", async () => {
  await submitDesktopCommand("open_patient_display");
});
document.getElementById("replay-speech").addEventListener("click", async () => {
  await submitDesktopCommand("replay_speech");
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
