"use strict";

const state = {
  tasks: [],
  taskId: null,
  task: null,
  report: null,
  patch: "",
  events: [],
  seenEvents: new Set(),
  stream: null,
  tab: "summary",
};

const byId = (id) => document.getElementById(id);
const terminalStates = new Set([
  "succeeded", "failed", "cancelled", "budget_exceeded", "needs_verification", "interrupted",
]);
const statusNames = {
  queued: "排队中",
  preparing: "准备中",
  running: "执行中",
  cancel_requested: "取消中",
  cancelled: "已取消",
  succeeded: "已完成",
  failed: "失败",
  budget_exceeded: "预算耗尽",
  needs_verification: "待验证",
  interrupted: "已中断",
};
const phaseNames = { baseline: "基线测试", agent: "Agent 测试", final: "最终测试", acceptance: "独立验收" };

function node(tag, className, content) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (content !== undefined) element.textContent = String(content);
  return element;
}

function showToast(message) {
  const toast = byId("toast");
  toast.textContent = message;
  toast.hidden = false;
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => { toast.hidden = true; }, 6000);
}

async function request(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let message = `请求失败（${response.status}）`;
    try {
      const body = await response.json();
      message = typeof body.detail === "string" ? body.detail : message;
    } catch { /* Keep the HTTP status message. */ }
    throw new Error(message);
  }
  return response;
}

function formatNumber(value) {
  return typeof value === "number" ? value.toLocaleString("zh-CN") : "—";
}

function shortPath(path) {
  if (!path) return "未知仓库";
  const parts = path.replaceAll("\\", "/").split("/").filter(Boolean);
  return parts.at(-1) || path;
}

function renderTasks() {
  const list = byId("task-list");
  list.replaceChildren();
  byId("task-count").textContent = state.tasks.length;
  if (state.tasks.length === 0) {
    list.append(node("p", "sidebar-empty", "还没有任务。创建一个任务开始运行。"));
    return;
  }
  for (const task of state.tasks) {
    const item = node("button", `task-item${task.task_id === state.taskId ? " active" : ""}`);
    item.type = "button";
    item.title = task.instruction || task.task_id;
    item.append(node("span", "task-item-title", task.instruction || task.task_id));
    const meta = node("span", "task-item-meta");
    meta.append(node("span", "", shortPath(task.source_repo)));
    meta.append(node("span", "", statusNames[task.status] || task.status));
    item.append(meta);
    item.addEventListener("click", () => selectTask(task.task_id));
    list.append(item);
  }
}

async function loadTasks(selectFirst = false) {
  const response = await request("/tasks");
  state.tasks = await response.json();
  renderTasks();
  if (selectFirst && !state.taskId && state.tasks.length > 0) {
    await selectTask(state.tasks[0].task_id);
  }
}

function closeStream() {
  if (state.stream) state.stream.close();
  state.stream = null;
}

function connectStream(taskId) {
  closeStream();
  const stream = new EventSource(`/tasks/${encodeURIComponent(taskId)}/events`);
  state.stream = stream;
  stream.onmessage = (message) => {
    if (state.taskId !== taskId) return;
    try {
      const event = JSON.parse(message.data);
      if (!state.seenEvents.has(event.seq)) {
        state.seenEvents.add(event.seq);
        state.events.push(event);
        renderEvents();
        renderMetrics();
      }
      if (event.type === "run_finished") refreshCurrentTask();
    } catch { showToast("有一条执行记录无法解析。"); }
  };
  stream.onerror = () => {
    if (state.taskId === taskId) refreshCurrentTask();
  };
}

async function selectTask(taskId) {
  if (state.taskId === taskId) return;
  closeStream();
  state.taskId = taskId;
  state.task = null;
  state.report = null;
  state.patch = "";
  state.events = [];
  state.seenEvents = new Set();
  state.tab = "summary";
  byId("empty-state").hidden = true;
  byId("task-view").hidden = false;
  document.body.classList.add("has-task");
  byId("task-form").hidden = true;
  byId("toggle-form").hidden = false;
  byId("toggle-form").setAttribute("aria-expanded", "false");
  byId("toggle-form").textContent = "展开表单 ↓";
  for (const tab of document.querySelectorAll(".tab")) {
    tab.classList.toggle("active", tab.dataset.tab === "summary");
    tab.setAttribute("aria-selected", tab.dataset.tab === "summary" ? "true" : "false");
  }
  renderTasks();
  renderEvents();
  renderResult();
  await refreshCurrentTask();
}

async function loadReport(taskId) {
  try {
    const reportResponse = await request(`/tasks/${encodeURIComponent(taskId)}/artifacts/report.json`);
    const report = await reportResponse.json();
    if (state.taskId !== taskId) return;
    state.report = report;
    state.events = report.events || [];
    state.seenEvents = new Set(state.events.map((event) => event.seq));
    try {
      const patchResponse = await request(`/tasks/${encodeURIComponent(taskId)}/artifacts/patch.diff`);
      state.patch = await patchResponse.text();
    } catch { state.patch = ""; }
    if (state.taskId !== taskId) return;
    renderEvents();
    renderMetrics();
    renderResult();
  } catch { /* Preparation failures may have no report. */ }
}

async function refreshCurrentTask() {
  const taskId = state.taskId;
  if (!taskId) return;
  try {
    const response = await request(`/tasks/${encodeURIComponent(taskId)}`);
    const task = await response.json();
    if (state.taskId !== taskId) return;
    state.task = task;
    renderTaskHeader();
    renderMetrics();
    if (terminalStates.has(task.status)) {
      closeStream();
      if (!state.report) await loadReport(taskId);
      renderResult();
    } else if (!state.stream) {
      connectStream(taskId);
    }
  } catch (error) { showToast(error.message); }
}

function renderTaskHeader() {
  const task = state.task;
  if (!task) return;
  byId("task-id-label").textContent = `#${task.task_id}`;
  byId("task-heading").textContent = task.instruction || "任务详情";
  byId("task-repo").textContent = task.source_repo || "准备工作区中";
  const badge = byId("status-badge");
  badge.textContent = statusNames[task.status] || task.status;
  badge.className = `status-badge ${task.status}`;
  byId("cancel-button").hidden = !["queued", "running"].includes(task.status);
  byId("resume-button").hidden = task.status !== "interrupted";
}

function renderMetrics() {
  const task = state.task;
  const report = state.report;
  const steps = report?.steps ?? state.events.filter((event) => event.type === "model_action" || event.type === "model_error").length;
  byId("metric-status").textContent = statusNames[task?.status] || "—";
  byId("metric-steps").textContent = formatNumber(steps);
  byId("metric-input").textContent = formatNumber(report?.input_tokens);
  byId("metric-output").textContent = formatNumber(report?.output_tokens);
}

function eventInfo(event) {
  const payload = event.payload || {};
  if (event.type === "tool_started") return ["开始执行", payload.name || "工具", "", ""];
  if (event.type === "tool_result") {
    const outcome = payload.ok ? (payload.exit_code && payload.exit_code !== 0 ? `退出码 ${payload.exit_code}` : "执行完成") : (payload.error_code || "工具失败");
    return [payload.tool_name || "工具结果", outcome, payload.output_excerpt || "", payload.ok && (!payload.exit_code || payload.exit_code === 0) ? "success" : "error"];
  }
  if (event.type === "verification") {
    const name = phaseNames[payload.phase] || "验证";
    const counts = payload.counts || {};
    const result = payload.passed ? `通过 ${counts.passed ?? 0} 项` : (payload.error_code || "未通过");
    return [name, result, payload.output_excerpt || "", payload.passed ? "success" : "error"];
  }
  if (event.type === "model_error") return ["模型动作错误", payload.error_code || "解析失败", payload.message || "", "error"];
  if (event.type === "run_finished") return ["任务结束", statusNames[payload.status] || payload.status || "结束", payload.reason || "", payload.status === "succeeded" ? "success" : "error"];
  if (event.type === "model_action" && payload.kind === "finish") return ["Agent 请求结束", "正在进行最终验证", "", ""];
  return null;
}

function renderEvents() {
  const timeline = byId("timeline");
  timeline.replaceChildren();
  const visible = state.events.filter((event) => eventInfo(event));
  byId("event-count").textContent = `${visible.length} 条记录`;
  if (visible.length === 0) {
    timeline.append(node("p", "timeline-empty", "等待任务开始，执行步骤将实时出现。"));
    return;
  }
  for (const event of visible.slice(-120)) {
    const [title, description, output, style] = eventInfo(event);
    const row = node("div", `event ${style}`);
    const heading = node("div", "event-title");
    heading.append(node("span", "", title));
    heading.append(node("span", "event-seq", `#${event.seq}`));
    row.append(heading);
    if (description) row.append(node("p", "event-description", description));
    if (output) {
      const details = node("details");
      details.append(node("summary", "", "查看输出"));
      details.append(node("pre", "", output));
      row.append(details);
    }
    timeline.append(row);
  }
}

function renderVerification(label, verification) {
  const row = node("div", `verification-row${verification && !verification.passed ? " failed" : ""}`);
  row.append(node("strong", "", label));
  if (!verification) {
    row.append(node("span", "", "未执行"));
  } else {
    const counts = verification.counts || {};
    const text = verification.passed
      ? `通过 · ${counts.passed ?? 0} 项`
      : `失败 · ${verification.error_code || "测试未通过"}`;
    row.append(node("span", "", text));
  }
  return row;
}

function renderResult() {
  const area = byId("result-content");
  area.replaceChildren();
  byId("download-patch").hidden = !state.report;
  if (state.report) byId("download-patch").href = `/tasks/${encodeURIComponent(state.taskId)}/artifacts/patch.diff`;

  if (state.tab === "diff") {
    if (!state.report) {
      area.append(node("p", "result-empty", "任务结束后，这里会显示生成的补丁。"));
      return;
    }
    if (!state.patch.trim()) {
      area.append(node("p", "result-empty", "当前任务没有生成补丁。"));
      return;
    }
    const code = node("pre", "code-block");
    for (const line of state.patch.split("\n")) {
      let style = "";
      if (line.startsWith("+") && !line.startsWith("+++")) style = "added";
      else if (line.startsWith("-") && !line.startsWith("---")) style = "removed";
      else if (line.startsWith("diff ") || line.startsWith("@@") || line.startsWith("+++")) style = "header";
      code.append(node("span", `diff-line ${style}`, line || " "));
    }
    area.append(code);
    return;
  }

  if (state.tab === "report") {
    area.append(state.report
      ? node("pre", "code-block", JSON.stringify(state.report, null, 2))
      : node("p", "result-empty", "任务结束后，这里会显示完整报告。"));
    return;
  }

  if (state.report) {
    area.append(node("p", "summary-text", state.report.summary || "没有结果说明。"));
    if (state.report.failure_reason) area.append(node("p", "summary-error", `失败原因：${state.report.failure_reason}`));
    const checks = node("div", "verification-list");
    checks.append(renderVerification("基线测试", state.report.baseline));
    checks.append(renderVerification("最终测试", state.report.verification));
    checks.append(renderVerification("独立验收", state.report.acceptance));
    area.append(checks);
  } else if (state.task?.error) {
    area.append(node("p", "summary-error", state.task.error));
  } else {
    area.append(node("p", "result-empty", "Agent 正在工作。测试和补丁结果将在完成后显示。"));
  }
}

async function submitTask(event) {
  event.preventDefault();
  const button = byId("submit-button");
  const body = {
    repo: byId("repo-input").value.trim(),
    task: byId("instruction-input").value.trim(),
    profile: byId("profile-input").value,
    max_steps: Number(byId("steps-input").value),
    base_commit: byId("commit-input").value.trim() || "HEAD",
  };
  const acceptance = byId("acceptance-input").value.trim();
  if (acceptance) body.acceptance = acceptance;
  button.disabled = true;
  try {
    const response = await request("/tasks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const created = await response.json();
    byId("instruction-input").value = "";
    await loadTasks();
    await selectTask(created.task_id);
  } catch (error) { showToast(error.message); }
  finally { button.disabled = false; }
}

async function taskAction(name) {
  if (!state.taskId) return;
  try {
    await request(`/tasks/${encodeURIComponent(state.taskId)}/${name}`, { method: "POST" });
    await refreshCurrentTask();
    await loadTasks();
  } catch (error) { showToast(error.message); }
}

async function initialize() {
  byId("task-form").addEventListener("submit", submitTask);
  byId("toggle-form").addEventListener("click", () => {
    const form = byId("task-form");
    form.hidden = !form.hidden;
    byId("toggle-form").setAttribute("aria-expanded", String(!form.hidden));
    byId("toggle-form").textContent = form.hidden ? "展开表单 ↓" : "收起表单 ↑";
  });
  byId("cancel-button").addEventListener("click", () => taskAction("cancel"));
  byId("resume-button").addEventListener("click", () => taskAction("resume"));
  byId("profile-input").addEventListener("change", () => {
    const acceptance = byId("acceptance-input");
    acceptance.disabled = byId("profile-input").value !== "python";
    if (acceptance.disabled) acceptance.value = "";
  });
  for (const tab of document.querySelectorAll(".tab")) {
    tab.addEventListener("click", () => {
      state.tab = tab.dataset.tab;
      for (const other of document.querySelectorAll(".tab")) {
        const active = other === tab;
        other.classList.toggle("active", active);
        other.setAttribute("aria-selected", String(active));
      }
      renderResult();
    });
  }
  try {
    await loadTasks(true);
    byId("connection-state").textContent = "服务已连接";
    byId("connection-state").classList.add("online");
  } catch (error) {
    byId("connection-state").textContent = "服务连接失败";
    byId("connection-state").classList.add("offline");
    showToast(error.message);
  }
  setInterval(() => refreshCurrentTask(), 2500);
  setInterval(() => loadTasks().catch(() => {}), 6000);
}

initialize();
