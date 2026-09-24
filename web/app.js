// QC-Agent web: một client của API /api/v1. Không thư viện ngoài, không CDN.
// AN TOÀN: dữ liệu (tên project, intent của task, lỗi, log...) đến từ SUT/người dùng nên KHÔNG BAO GIỜ đưa vào innerHTML;
// mọi thứ dựng bằng DOM + textContent (h()). CSP của server cấm script/style inline.

const API = "/api/v1";
const PAGE = 20;
const STATUS_LABEL = {
  queued: "Đang chờ", running: "Đang chạy", succeeded: "Thành công", failed: "Thất bại", cancelled: "Đã huỷ", timed_out: "Quá thời gian",
  pass: "pass", fail: "fail", error: "error", skipped: "skipped",
};
const ACTIVE = new Set(["queued", "running"]);

const state = {
  user: null, projects: [], project: null, suites: [], suitesError: null,
  jobs: [], filters: { status: "", mode: "", source: "" }, offset: 0, selected: null, detail: null, report: null, log: "",
  formError: null, notice: null,
};

// ---------- tiện ích ----------
function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") el.className = value;
    else if (key === "style") el.style.cssText = value; // CSSOM: được phép dù CSP cấm thuộc tính style inline
    else if (key === "on") for (const [ev, fn] of Object.entries(value)) el.addEventListener(ev, fn);
    else if (key === "dataset") Object.assign(el.dataset, value);
    else if (key in el && key !== "list") el[key] = value;
    else el.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

// thêm con vào phần tử, bỏ qua null/undefined/false (Node.append(null) sẽ in chữ "null")
function add(el, ...children) {
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

function formatDetail(detail) {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((d) => (d.loc ? d.loc.slice(1).join(".") + ": " : "") + (d.msg || JSON.stringify(d))).join("; ");
  return JSON.stringify(detail);
}

async function api(path, options = {}) {
  const init = { credentials: "same-origin", ...options, headers: { ...(options.body ? { "Content-Type": "application/json" } : {}), ...(options.headers || {}) } };
  const response = await fetch(API + path, init);
  const isJson = (response.headers.get("content-type") || "").includes("application/json");
  const body = response.status === 204 ? null : isJson ? await response.json().catch(() => null) : await response.text();
  if (!response.ok) {
    if (response.status === 401 && state.user) { state.user = null; render(); }
    const error = new Error(body && body.detail ? formatDetail(body.detail) : `Lỗi ${response.status}`);
    error.status = response.status;
    throw error;
  }
  return body;
}

const fmtTime = (iso) => (iso ? new Date(iso).toLocaleString("vi-VN") : "—");
function duration(job) {
  if (!job.started_at) return "—";
  const end = job.finished_at ? new Date(job.finished_at) : new Date();
  const s = Math.max(0, Math.round((end - new Date(job.started_at)) / 1000));
  return s >= 60 ? `${Math.floor(s / 60)}m${String(s % 60).padStart(2, "0")}s` : `${s}s`;
}
const badge = (status, label) => h("span", { class: "badge", dataset: { status } }, label ?? STATUS_LABEL[status] ?? status);
const enc = (path) => path.split("/").map(encodeURIComponent).join("/");

function readHash() {
  const params = new URLSearchParams(location.hash.slice(1));
  return { project: params.get("project"), job: params.get("job") };
}
function writeHash() {
  const params = new URLSearchParams();
  if (state.project) params.set("project", state.project.slug);
  if (state.selected) params.set("job", state.selected);
  history.replaceState(null, "", "#" + params.toString());
}

// ---------- tải dữ liệu ----------
async function loadProjects() {
  state.projects = await api("/projects");
  const wanted = readHash().project;
  state.project = state.projects.find((p) => p.slug === wanted) || state.project && state.projects.find((p) => p.slug === state.project.slug) || state.projects[0] || null;
}

async function loadSuites() {
  state.suites = [];
  state.suitesError = null;
  if (!state.project) return;
  if (!state.project.runnable) { state.suitesError = "Project chưa có checkout SUT trên máy chủ nên không chạy thủ công được."; return; }
  try { state.suites = await api(`/projects/${encodeURIComponent(state.project.slug)}/suites`); }
  catch (error) { state.suitesError = error.message; }
}

async function loadJobs() {
  if (!state.project) { state.jobs = []; return; }
  const q = new URLSearchParams({ project: state.project.slug, limit: PAGE, offset: state.offset });
  for (const [key, value] of Object.entries(state.filters)) if (value) q.set(key, value);
  state.jobs = (await api("/jobs?" + q)).items;
}

async function loadDetail() {
  if (!state.selected) { state.detail = null; state.report = null; state.log = ""; return; }
  try {
    const detail = await api(`/jobs/${state.selected}`);
    state.detail = detail;
    if (!ACTIVE.has(detail.status) && detail.artifacts.some((a) => a.path === "report.json")) {
      state.report = await api(`/jobs/${state.selected}/report.json`).catch(() => null);
    } else state.report = null;
    state.log = await api(`/jobs/${state.selected}/log?tail=20000`).catch(() => "");
  } catch (error) {
    state.detail = null; state.report = null; state.log = "";
    state.notice = error.status === 404 ? "Không tìm thấy job này." : error.message;
  }
}

async function refreshAll() {
  await loadJobs();
  await loadDetail();
  render();
}

// ---------- hành động ----------
async function login(email, password) {
  const out = await api("/auth/login", { method: "POST", body: JSON.stringify({ email, password }) });
  state.user = out.user;
  await loadProjects();
  await loadSuites();
  state.selected = readHash().job;
  await refreshAll();
}

async function logout() {
  await api("/auth/logout", { method: "POST" }).catch(() => {});
  Object.assign(state, { user: null, projects: [], project: null, jobs: [], selected: null, detail: null });
  render();
}

async function selectProject(slug) {
  state.project = state.projects.find((p) => p.slug === slug) || null;
  state.selected = null; state.offset = 0; state.formError = null;
  writeHash();
  await loadSuites();
  await refreshAll();
}

async function selectJob(id) {
  state.selected = id;
  writeHash();
  await loadDetail();
  render();
}

async function submitJob(form) {
  const data = new FormData(form);
  const mode = data.get("mode");
  const chosenSuites = [...form.querySelectorAll("input[name=suite]:checked")].map((el) => el.value);
  const chosenTasks = [...form.querySelectorAll("input[name=task]:checked")].map((el) => el.value);
  const body = { mode };
  if (chosenSuites.length) body.suites = chosenSuites;
  if (chosenTasks.length) body.task_ids = chosenTasks;
  if (data.get("environment")) body.environment = data.get("environment");
  if (data.get("timeout_s")) body.timeout_s = Number(data.get("timeout_s"));
  state.formError = null;
  try {
    const job = await api(`/projects/${encodeURIComponent(state.project.slug)}/jobs`, { method: "POST", body: JSON.stringify(body) });
    state.selected = job.id; state.offset = 0;
    writeHash();
    await refreshAll();
  } catch (error) { state.formError = error.message; render(); }
}

async function cancelJob() {
  try { await api(`/jobs/${state.selected}/cancel`, { method: "POST" }); state.notice = null; }
  catch (error) { state.notice = error.message; }
  await refreshAll();
}

// ---------- giao diện ----------
function loginView() {
  const error = h("div", { class: "alert", role: "alert", hidden: true });
  const form = h("form", { class: "panel login", on: { submit: async (event) => {
    event.preventDefault();
    error.hidden = true;
    try { await login(form.email.value, form.password.value); }
    catch (err) { error.textContent = err.status === 401 ? "Sai email hoặc mật khẩu." : err.message; error.hidden = false; }
  } } },
    h("h1", {}, "QC-Agent"), h("p", { class: "muted" }, "Đăng nhập bằng email công ty."),
    h("label", { for: "email" }, "Email"), h("input", { id: "email", name: "email", type: "email", autocomplete: "username", required: true }),
    h("label", { for: "password" }, "Mật khẩu"), h("input", { id: "password", name: "password", type: "password", autocomplete: "current-password", required: true }),
    error, h("p", {}, h("button", { class: "primary", type: "submit" }, "Đăng nhập")));
  return form;
}

function passwordDialog() {
  const message = h("div", { role: "alert" });
  const dialog = h("dialog", { id: "pw-dialog" },
    h("form", { on: { submit: async (event) => {
      event.preventDefault();
      const form = event.target;
      try {
        await api("/auth/password", { method: "POST", body: JSON.stringify({ current_password: form.current.value, new_password: form.next.value }) });
        dialog.close(); state.notice = "Đã đổi mật khẩu. Các phiên đăng nhập khác đã bị thu hồi."; render();
      } catch (err) { message.className = "alert"; message.textContent = err.message; }
    } } },
      h("h2", {}, "Đổi mật khẩu"),
      h("label", { for: "pw-current" }, "Mật khẩu hiện tại"), h("input", { id: "pw-current", name: "current", type: "password", autocomplete: "current-password", required: true }),
      h("label", { for: "pw-next" }, "Mật khẩu mới (10–128 ký tự)"), h("input", { id: "pw-next", name: "next", type: "password", autocomplete: "new-password", minLength: 10, required: true }),
      message,
      h("div", { class: "row", style: "margin-top:.8rem" }, h("button", { class: "primary", type: "submit" }, "Lưu"),
        h("button", { type: "button", on: { click: () => dialog.close() } }, "Đóng"))));
  return dialog;
}

function headerView() {
  const dialog = passwordDialog();
  return h("header", { class: "bar" },
    h("h1", {}, "QC-Agent"),
    h("label", { class: "row", style: "margin:0" }, h("span", { class: "muted" }, "Project"),
      h("select", { id: "project-select", "aria-label": "Project", on: { change: (e) => selectProject(e.target.value) } },
        state.projects.map((p) => h("option", { value: p.slug, selected: state.project && p.slug === state.project.slug }, p.name || p.slug)))),
    h("span", { class: "grow" }),
    h("span", { class: "muted", id: "whoami" }, state.user.email),
    h("button", { on: { click: () => dialog.showModal() } }, "Đổi mật khẩu"),
    h("button", { id: "logout", on: { click: logout } }, "Đăng xuất"), dialog);
}

function runFormView() {
  const project = state.project;
  const modes = Object.keys(project.modes);
  const environments = Object.entries(project.environments);
  const form = h("form", { id: "run-form", class: "panel", on: { submit: (event) => { event.preventDefault(); submitJob(form); } } }, h("h2", {}, "Chạy mới"));
  if (state.suitesError) { form.append(h("div", { class: "notice" }, state.suitesError)); return form; }
  const modeSelect = h("select", { id: "mode", name: "mode", on: { change: () => renderSuites() } }, modes.map((m) => h("option", { value: m, selected: m === "manual" }, m)));
  const suitesBox = h("div", { id: "suites-box" });
  function renderSuites() {
    suitesBox.replaceChildren();
    const mode = modeSelect.value;
    for (const suite of state.suites.filter((s) => s.modes[mode])) {
      suitesBox.append(h("div", {},
        h("label", { class: "check" }, h("input", { type: "checkbox", name: "suite", value: suite.name, checked: true }), h("span", {}, suite.name),
          h("span", { class: "muted" }, ` (${suite.modes[mode]})`)),
        h("details", { style: "margin-left:1.4rem" }, h("summary", {}, `${suite.tasks.length} task`),
          suite.tasks.map((t) => h("label", { class: "check" }, h("input", { type: "checkbox", name: "task", value: t.task_id }),
            h("span", { class: "mono" }, t.task_id), h("span", { class: "muted" }, ` ${t.capability} · ${t.lane}${t.intent ? " — " + t.intent : ""}`))))));
    }
    if (!suitesBox.children.length) suitesBox.append(h("p", { class: "muted" }, "Mode này không có suite nào."));
  }
  renderSuites();
  add(form,
    h("label", { for: "mode" }, "Mode"), modeSelect,
    h("label", {}, "Suite / task"), h("p", { class: "muted", style: "margin:0" }, "Không tick task nào = chạy toàn bộ task của suite đã chọn."), suitesBox,
    h("label", { for: "environment" }, "Môi trường"),
    h("select", { id: "environment", name: "environment" }, h("option", { value: "" }, "(không dùng)"),
      environments.map(([name, e]) => h("option", { value: name }, name + (e.exclusive ? " — 1 job/lần" : "") + (e.description ? ` · ${e.description}` : "")))),
    h("label", { for: "timeout_s" }, "Timeout (giây, tuỳ chọn)"), h("input", { id: "timeout_s", name: "timeout_s", type: "number", min: 1, step: 1 }),
    state.formError ? h("div", { class: "alert", role: "alert", id: "form-error" }, state.formError) : null,
    h("p", {}, h("button", { class: "primary", id: "submit-job", type: "submit" }, "Chạy")));
  return form;
}

function jobsView() {
  const select = (name, options) => h("select", { "aria-label": name, on: { change: (e) => { state.filters[name] = e.target.value; state.offset = 0; refreshAll(); } } },
    h("option", { value: "" }, `${name}: tất cả`), options.map((o) => h("option", { value: o, selected: state.filters[name] === o }, o)));
  const rows = state.jobs.map((job) => h("tr", { class: "job" + (job.id === state.selected ? " sel" : ""), dataset: { jobId: job.id }, on: { click: () => selectJob(job.id) } },
    h("td", { class: "mono" }, job.id.slice(0, 8)), h("td", {}, job.mode), h("td", {}, job.source === "ci" ? `CI${job.pr_number ? " #" + job.pr_number : ""}` : "Web"),
    h("td", {}, badge(job.status)), h("td", {}, job.gate_verdict ? badge(job.gate_verdict) : "—"), h("td", {}, fmtTime(job.created_at)), h("td", {}, duration(job))));
  return h("div", { class: "panel" }, h("h2", {}, "Lịch sử"),
    h("div", { class: "row" }, select("status", ["queued", "running", "succeeded", "failed", "cancelled", "timed_out"]), select("mode", Object.keys(state.project.modes)), select("source", ["web", "ci"])),
    h("table", { id: "jobs-table" }, h("thead", {}, h("tr", {}, ["Job", "Mode", "Nguồn", "Trạng thái", "Gate", "Tạo lúc", "Thời gian"].map((t) => h("th", {}, t)))), h("tbody", {}, rows)),
    state.jobs.length ? null : h("p", { class: "muted" }, "Chưa có job nào."),
    h("div", { class: "row" },
      h("button", { disabled: state.offset === 0, on: { click: () => { state.offset = Math.max(0, state.offset - PAGE); refreshAll(); } } }, "← Mới hơn"),
      h("button", { disabled: state.jobs.length < PAGE, on: { click: () => { state.offset += PAGE; refreshAll(); } } }, "Cũ hơn →")));
}

function taskCard(t) {
  const metrics = Object.entries((t.summary && t.summary.metrics) || {}).slice(0, 4).map(([k, v]) => `${k}=${typeof v === "number" ? +v.toFixed(3) : v}`).join(" · ");
  return h("div", { class: "card", dataset: { status: t.status, taskId: t.task_id } },
    h("div", { class: "row" }, h("strong", { class: "mono grow" }, t.task_id), badge(t.status)),
    h("div", { class: "muted" }, [t.worker, t.capability, t.lane, t.duration_s != null ? `${(+t.duration_s).toFixed(1)}s` : null].filter(Boolean).join(" · ")),
    metrics ? h("div", { class: "mono" }, metrics) : null,
    ((t.summary && t.summary.findings) || []).slice(0, 3).map((f) => h("div", {}, "• " + f)),
    t.summary && t.summary.rationale ? h("div", { class: "muted" }, t.summary.rationale) : null);
}

function detailView() {
  const job = state.detail;
  if (!job) return h("div", { class: "panel muted" }, state.notice || "Chọn một job để xem chi tiết.");
  const report = state.report;
  const gate = job.tasks.filter((t) => t.gating), other = job.tasks.filter((t) => !t.gating);
  const box = h("div", { class: "panel", id: "detail", dataset: { status: job.status } },
    h("div", { class: "row" }, h("h2", { class: "grow mono" }, `Job ${job.id}`), badge(job.status), job.gate_verdict ? badge(job.gate_verdict) : null,
      ACTIVE.has(job.status) ? h("button", { id: "cancel-job", class: "danger", disabled: job.cancel_requested, on: { click: cancelJob } }, job.cancel_requested ? "Đang huỷ…" : "Huỷ") : null),
    h("div", { class: "muted" }, [`mode ${job.mode}`, job.source === "ci" ? "CI" : "Web", job.created_by, job.environment && `môi trường ${job.environment}`,
      job.sha && `sha ${job.sha.slice(0, 8)}`, job.branch, `tạo ${fmtTime(job.created_at)}`, `thời gian ${duration(job)}`, job.exit_code != null && `exit ${job.exit_code}`].filter(Boolean).join(" · ")),
    job.error ? h("div", { class: "alert", role: "alert" }, job.error) : null);
  if (job.progress) box.append(h("div", {}, h("div", { class: "bar-outer" }, h("div", { class: "bar-inner", style: `width:${job.progress.total ? Math.round(100 * job.progress.done / job.progress.total) : 3}%` })),
    h("span", { class: "muted" }, `${job.progress.done}/${job.progress.total || "?"} task`)));
  if (report && (report.banner || []).length) box.append(h("div", { class: "notice" }, h("strong", {}, "Skipped / error — đọc trước"), h("ul", { class: "plain" }, report.banner.map((b) => h("li", {}, `${b[0]}: ${b[1]}`)))));
  for (const c of (report && report.canary) || []) box.append(h("div", { class: c.ok ? "ok-note" : "alert" }, (c.ok ? "✅ " : "🚨 ") + c.message));
  add(box, h("h3", {}, "Task chặn gate (deterministic assert)"), h("div", { class: "grid" }, gate.length ? gate.map(taskCard) : h("span", { class: "muted" }, "không có")));
  if (other.length) add(box, h("h3", { style: "margin-top:.8rem" }, "Discovery / không chặn gate"), h("div", { class: "grid" }, other.map(taskCard)));
  const links = ["report.json", "report.md"].filter((n) => job.artifacts.some((a) => a.path === n));
  const MAIN = new Set(["report.md", "report.json", "executor.log"]);
  const main = job.artifacts.filter((a) => MAIN.has(a.path)), rest = job.artifacts.filter((a) => !MAIN.has(a.path));
  const artifactItem = (a) => h("li", {}, h("a", { href: `${API}/jobs/${job.id}/artifacts/${enc(a.path)}`, rel: "noopener" }, a.path),
    h("span", { class: "muted" }, ` ${a.size_bytes != null ? a.size_bytes + " B" : ""}${a.sha256 ? " · sha256 " + a.sha256.slice(0, 10) + "…" : ""}`));
  add(box, h("h3", { style: "margin-top:.8rem" }, "Report & artifact"),
    h("ul", { class: "plain", id: "artifacts" }, main.map(artifactItem)),
    rest.length ? h("details", {}, h("summary", {}, `Tất cả artifact (${job.artifacts.length})`), h("ul", { class: "plain" }, rest.map(artifactItem))) : null,
    links.length ? null : h("p", { class: "muted" }, "Chưa có report (job chưa xong)."),
    h("details", { open: ACTIVE.has(job.status) }, h("summary", {}, "Log của executor"), h("pre", { class: "log", id: "log" }, state.log || "(trống)")));
  return box;
}

function appView() {
  const main = state.project
    ? h("main", {}, h("div", {}, runFormView()), h("div", {}, state.notice ? h("div", { class: "notice", id: "notice" }, state.notice) : null, jobsView(), detailView()))
    : h("main", {}, h("div", { class: "panel" }, "Chưa có project nào được cấu hình."));
  return h("div", {}, headerView(), main);
}

function render() {
  const focus = document.activeElement && document.activeElement.id;
  const root = document.getElementById("root");
  const keep = document.getElementById("run-form");
  const draft = keep ? new FormData(keep) : null;
  root.replaceChildren(state.user ? appView() : loginView());
  if (draft && state.user && !state.suitesError) restoreDraft(draft);
  if (focus) document.getElementById(focus)?.focus();
}

// giữ lại lựa chọn của form khi render lại do polling
function restoreDraft(draft) {
  const form = document.getElementById("run-form");
  if (!form || !form.mode) return;
  const mode = draft.get("mode");
  if (mode && [...form.mode.options].some((o) => o.value === mode)) {
    form.mode.value = mode;
    form.mode.dispatchEvent(new Event("change"));
  }
  const suites = new Set(draft.getAll("suite")), tasks = new Set(draft.getAll("task"));
  form.querySelectorAll("input[name=suite]").forEach((el) => { el.checked = suites.size ? suites.has(el.value) : el.checked; });
  form.querySelectorAll("input[name=task]").forEach((el) => { el.checked = tasks.has(el.value); });
  if (draft.get("environment")) form.environment.value = draft.get("environment");
  if (draft.get("timeout_s")) form.timeout_s.value = draft.get("timeout_s");
}

// ---------- khởi động + polling ----------
async function boot() {
  try {
    state.user = (await api("/auth/me")).user;
    await loadProjects();
    await loadSuites();
    state.selected = readHash().job;
    await refreshAll();
  } catch (error) { state.user = null; render(); }
  setInterval(async () => {
    if (!state.user || document.hidden || document.querySelector("dialog[open]")) return; // không render đè lên hộp thoại đang mở
    const active = state.jobs.some((j) => ACTIVE.has(j.status)) || (state.detail && ACTIVE.has(state.detail.status));
    if (!active) return;
    try { await loadJobs(); await loadDetail(); render(); } catch (_) { /* lần sau thử lại */ }
  }, 2000);
}

window.addEventListener("hashchange", () => {
  const { job } = readHash();
  if (state.user && job !== state.selected) selectJob(job);
});
boot();
