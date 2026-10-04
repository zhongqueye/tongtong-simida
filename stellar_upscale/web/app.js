"use strict";

const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

let api = null;          // window.pywebview.api 或浏览器演示用的 mock
let env = null;
let settings = null;
let picked = [];         // 待处理的视频
let jobs = [];
let filter = "all";
let query = "";

/* ---------- 工具 ---------- */

function fmtTime(sec) {
  if (sec == null || !isFinite(sec)) return "--:--";
  sec = Math.max(0, Math.round(sec));
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}
function fmtSize(b) {
  if (!b) return "";
  return b > 1 << 30 ? (b / (1 << 30)).toFixed(1) + " GB" : (b / (1 << 20)).toFixed(1) + " MB";
}
function fmtDate(ts) {
  const d = new Date(ts * 1000), p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}
function toast(msg, isErr = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.toggle("error", isErr);
  t.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.remove("show"), isErr ? 5000 : 2600);
}
function modelOf(key) { return env.models.find((m) => m.key === key) || env.models[0]; }
function presetOf(key) { return env.presets.find((p) => p.key === key) || env.presets[0]; }
function targetLabel(t) { return t === "2k" ? "2K" : "1080P"; }
function styleOf(key) { return (env.styles || []).find((s) => s.key === key); }
// 手动改了画风会影响的参数，就切换成"自定义"
function markCustom() { settings.style = "custom"; }

/* ---------- 设置面板 ---------- */

function renderSeg(el, items, value, onPick) {
  el.innerHTML = items.map((i) => `<button data-v="${esc(i.v)}" class="${i.v === value ? "on" : ""}" title="${esc(i.title || "")}">${esc(i.label)}</button>`).join("");
  el.onclick = (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    onPick(b.dataset.v);
  };
}
function bindStaticSeg(el, key) {
  el.onclick = (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    settings[key] = b.dataset.v;
    renderSettings();
    persist();
  };
}
function setRange(id, v, fmt) {
  const r = $(id);
  r.value = v;
  const p = ((v - r.min) / (r.max - r.min)) * 100;
  r.style.setProperty("--p", p + "%");
  return fmt(v);
}

function renderSettings() {
  const styles = [...(env.styles || []).map((s) => ({ v: s.key, label: s.label, title: s.desc })),
                  { v: "custom", label: "自定义", title: "保留当前的手动设置" }];
  renderSeg($("#seg-style"), styles, settings.style || "custom", (v) => {
    const st = styleOf(v);
    if (st) {
      const s = { ...st.settings };
      if (!env.models.some((m) => m.key === s.model)) s.model = env.models[0].key;  // 模型不可用时退回
      Object.assign(settings, s);
    }
    settings.style = v;
    renderSettings();
    persist();
  });
  $("#style-note").textContent = styleOf(settings.style) ? styleOf(settings.style).desc : "已手动调整参数";
  const m = modelOf(settings.model);
  renderSeg($("#seg-model"), env.models.map((x) => ({ v: x.key, label: x.label, title: x.note })), m.key, (v) => {
    settings.model = v;
    settings.strength = modelOf(v).strength;
    markCustom();
    renderSettings();
    persist();
  });
  $("#model-note").textContent = m.note || "";
  $("#field-strength").style.opacity = m.ai ? 1 : 0.45;
  $("#r-strength").disabled = !m.ai;
  $("#v-strength").textContent = m.ai ? setRange("#r-strength", settings.strength, (v) => Math.round(v * 100) + "%") : "—";

  $$("#seg-target button").forEach((b) => b.classList.toggle("on", b.dataset.v === settings.target));
  $$("#seg-quality button").forEach((b) => b.classList.toggle("on", b.dataset.v === settings.quality));
  $$("#seg-codec button").forEach((b) => b.classList.toggle("on", b.dataset.v === (settings.codec || "hevc")));

  renderSeg($("#seg-preset"), env.presets.map((p) => ({ v: p.key, label: p.label, title: p.desc })), settings.preset, (v) => {
    settings.preset = v;
    markCustom();
    renderSettings();
    persist();
  });
  $("#preset-note").textContent = presetOf(settings.preset).desc;
  $("#v-pstrength").textContent = setRange("#r-pstrength", settings.preset_strength, (v) => Math.round(v * 100) + "%");
  $("#r-pstrength").disabled = settings.preset === "original";
  $("#v-grain").textContent = setRange("#r-grain", settings.grain, (v) => (v == 0 ? "关" : Math.round(v * 100) + "%"));
  $("#out-text").textContent = settings.output_dir || "与原视频相同目录";
}

let persistTimer;
function persist() {
  clearTimeout(persistTimer);
  persistTimer = setTimeout(() => api.save_settings(settings), 400);
}

function bindRanges() {
  const bind = (id, key) => $(id).addEventListener("input", (e) => {
    settings[key] = parseFloat(e.target.value);
    markCustom();
    renderSettings();
    persist();
  });
  bind("#r-strength", "strength");
  bind("#r-pstrength", "preset_strength");
  bind("#r-grain", "grain");
}

/* ---------- 选择视频 ---------- */

function addPicked(items) {
  for (const it of items || []) {
    if (!picked.some((p) => p.path === it.path)) picked.push(it);
  }
  renderPicked();
}
function renderPicked() {
  $("#dz-empty").hidden = picked.length > 0;
  $("#pick-count").textContent = picked.length ? `${picked.length} 个` : "";
  $("#dz-list").innerHTML = picked.map((p, i) => `
    <div class="dz-item ${p.error ? "bad" : ""}">
      <span class="n" title="${esc(p.path)}">${esc(p.name)}</span>
      <span class="d">${p.error ? "无法读取" : `${p.width}×${p.height} · ${Math.round(p.fps)}fps · ${p.duration.toFixed(1)}s`}</span>
      <button class="x" data-i="${i}" title="移除">✕</button>
    </div>`).join("");
}
window.onNativeDrop = (items) => {
  $("#drop-overlay").hidden = true;
  if (!items.length) return toast("没有识别到视频文件", true);
  addPicked(items);
  toast(`已添加 ${items.length} 个视频`);
};

async function pickVideos() {
  const items = await api.pick_videos();
  if (items && items.length) addPicked(items);
}

/* ---------- 任务列表 ---------- */

const STATUS_TEXT = { queued: "排队中", running: "处理中", done: "已完成", failed: "失败", paused: "已暂停" };

function jobCard(j) {
  const s = j.settings || {}, info = j.info || {};
  const m = env.models.find((x) => x.key === s.model);
  const pct = j.total ? Math.min(100, (j.done / j.total) * 100) : 0;
  const st = styleOf(s.style);
  const tags = [
    st ? `<span class="tag cyan">${esc(st.label)}</span>` : "",
    `<span class="tag pink">${esc(m ? m.label : s.model)}</span>`,
    `<span class="tag cyan">${targetLabel(s.target)}</span>`,
    `<span class="tag">${esc(presetOf(s.preset).label)}</span>`,
    m && m.ai ? `<span class="tag">AI ${Math.round(s.strength * 100)}%</span>` : "",
    s.codec === "h264" ? `<span class="tag">H.264</span>` : "",
  ].join("");

  let midTitle, midSub, progress;
  if (j.status === "done") {
    midTitle = esc(j.output_name);
    const t = j.timing || {};
    const aiPart = t.ai ? ` · AI ${fmtTime(t.ai)}${t.frames ? `（${(t.ai / t.frames).toFixed(2)} 秒/帧）` : ""}` : "";
    const waitPart = t.wait > 5 ? ` · 等编码 ${fmtTime(t.wait)}` : "";
    midSub = `用时 ${fmtTime(j.elapsed)}${aiPart}${waitPart} · 完成于 ${fmtDate(j.finished)}${j.output_exists ? "" : " · 文件已移动或删除"}`;
    progress = `<div class="progress"><div class="bar"><i style="width:100%"></i></div>已处理 ${j.total} / ${j.total} 帧</div>`;
  } else if (j.status === "running") {
    midTitle = esc(j.message || "处理中");
    midSub = j.stage === "extract" ? "正在拆帧…" : j.stage === "mux" ? "正在合成音视频…" : `剩余 ${fmtTime(j.eta)} · 已用 ${fmtTime(j.elapsed)}`;
    progress = `<div class="progress"><div class="bar"><i style="width:${pct.toFixed(1)}%"></i></div>已处理 ${j.done} / ${j.total} 帧 · ${pct.toFixed(0)}%</div>`;
  } else {
    midTitle = j.status === "failed" ? "处理失败" : j.status === "paused" ? (j.done ? "已暂停，可从断点继续" : "已暂停") : "等待处理";
    midSub = `输出：${esc(j.output_name)}`;
    progress = `<div class="progress"><div class="bar"><i style="width:${pct.toFixed(1)}%"></i></div>已处理 ${j.done} / ${j.total} 帧</div>`;
  }

  const ops = [];
  if (j.status === "done" && j.output_exists) ops.push(["play", "播放"], ["reveal", "显示位置"]);
  if (j.status === "running" || j.status === "queued") ops.push(["pause", "暂停"]);
  if (j.status === "paused") ops.push(["resume", "继续"]);
  if (j.status === "failed") ops.push(["resume", "重试"]);
  ops.push(["preview", "预览"]);
  ops.push(["remove", j.status === "done" ? "删除记录" : "删除", "danger"]);

  return `
  <div class="job ${j.status}" data-id="${j.id}">
    <div>
      <div class="job-name" title="${esc(j.src)}">${esc(j.name)}</div>
      <div class="tags">${tags}</div>
      <div class="meta">${info.width || "?"}×${info.height || "?"} · ${info.fps ? Math.round(info.fps) : "?"}fps · ${info.duration ? info.duration.toFixed(1) + "s" : ""}</div>
    </div>
    <div class="job-mid">
      <div class="mid-line">
        <div class="mid-icon"><svg viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="14" rx="3"/><path d="M10 9.5v5l4.5-2.5z" class="fill"/></svg></div>
        <div style="min-width:0"><div class="mid-title">${midTitle}</div><div class="mid-sub">${midSub}</div></div>
      </div>
      ${progress}
      ${j.error ? `<div class="err">${esc(j.error)}</div>` : ""}
    </div>
    <div class="job-right">
      <span class="status ${j.status}">${STATUS_TEXT[j.status] || j.status}</span>
      <div class="ops">${ops.map(([a, t, c]) => `<button class="op ${c || ""}" data-act="${a}">${t}</button>`).join("")}</div>
    </div>
  </div>`;
}

function renderJobs() {
  const groups = {
    all: jobs,
    active: jobs.filter((j) => ["running", "queued", "paused"].includes(j.status)),
    done: jobs.filter((j) => j.status === "done"),
    failed: jobs.filter((j) => j.status === "failed"),
  };
  for (const k in groups) $("#c-" + k).textContent = groups[k].length;
  let list = groups[filter];
  if (query) list = list.filter((j) => j.name.toLowerCase().includes(query));
  const titles = { all: "全部任务", active: "处理中", done: "已完成", failed: "失败" };
  $("#list-title").textContent = titles[filter];
  $("#list-count").textContent = `${list.length} 个`;
  $("#empty").hidden = list.length > 0 || (jobs.length > 0 && filter !== "all");
  // 只重绘变化了的那张卡片（处理中每秒只有进度那张在变），减少界面占用的 GPU
  const box = $("#job-list");
  const cards = list.map((j) => [j.id, jobCard(j)]);
  const ids = cards.map((c) => c[0]).join(",");
  if (ids !== renderJobs._ids) {
    box.innerHTML = cards.map((c) => c[1]).join("");
    renderJobs._ids = ids;
    renderJobs._cache = Object.fromEntries(cards);
  } else {
    for (const [id, html] of cards) {
      if (renderJobs._cache[id] === html) continue;
      const el = box.querySelector(`.job[data-id="${id}"]`);
      if (el) el.outerHTML = html;
      renderJobs._cache[id] = html;
    }
  }
  const recent = groups.done.slice().sort((a, b) => b.finished - a.finished).slice(0, 4);
  const recentHtml = recent.length ? recent.map((j) => `
    <div class="recent-item" data-id="${j.id}">
      <div class="info"><div class="n" title="${esc(j.output)}">${esc(j.output_name)}</div>
      <div class="s">${targetLabel(j.settings.target)} · 用时 ${fmtTime(j.elapsed)} · ${fmtDate(j.finished)}</div></div>
      <button class="op" data-act="reveal">打开</button>
    </div>`).join("") : `<div class="recent-empty">还没有完成的视频</div>`;
  if (recentHtml !== renderJobs._recent) {
    $("#recent-list").innerHTML = recentHtml;
    renderJobs._recent = recentHtml;
  }
}

async function poll() {
  try {
    jobs = await api.jobs();
    const busy = jobs.some((j) => j.status === "running");
    document.body.classList.toggle("busy", busy);
    if (!document.hidden) renderJobs();  // 窗口最小化/被挡住时不重绘
  } catch (e) { /* 忽略 */ }
  setTimeout(poll, 1500);
}
document.addEventListener("visibilitychange", () => { if (!document.hidden && jobs.length) renderJobs(); });

async function jobAction(id, act) {
  const j = jobs.find((x) => x.id === id);
  if (!j) return;
  if (act === "play") return api.open_file(j.output);
  if (act === "reveal") return api.reveal(j.status === "done" ? j.output : j.src);
  if (act === "pause") await api.pause(id);
  if (act === "resume") await api.resume(id);
  if (act === "remove") await api.remove(id);
  if (act === "preview") {
    openPreview([{ path: j.src, name: j.name, duration: (j.info || {}).duration || 0 }], j.settings);
    return;
  }
  jobs = await api.jobs();
  renderJobs();
}

/* ---------- 开始 ---------- */

async function start() {
  const ok = picked.filter((p) => !p.error);
  if (!ok.length) {
    toast("先拖入或选择要超分的视频");
    return pickVideos();
  }
  $("#btn-start").disabled = true;
  const res = await api.add_jobs(ok.map((p) => p.path), settings);
  $("#btn-start").disabled = false;
  if (!res.ok) return toast(res.error, true);
  toast(`已加入队列：${res.ids.length} 个视频`);
  picked = [];
  renderPicked();
  filter = "all";
  $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.filter === "all"));
  jobs = await api.jobs();
  renderJobs();
}

/* ---------- 预览 ---------- */

let pv = { files: [], settings: null, busy: false, pos: 50 };

function openPreview(files, s) {
  files = files.filter((f) => !f.error);
  if (!files.length) {
    toast("先拖入或选择一个视频，再预览效果");
    return pickVideos();
  }
  pv.files = files;
  pv.settings = { ...s };
  $("#pv-file").innerHTML = files.map((f, i) => `<option value="${i}">${esc(f.name)}</option>`).join("");
  $("#pv-file").hidden = files.length < 2;
  setPvFile(0);
  $("#modal").hidden = false;
  renderPreview();
}
function setPvFile(i) {
  const f = pv.files[i];
  pv.file = f;
  const t = $("#pv-time");
  t.max = Math.max(0.1, (f.duration || 0.1) - 0.1).toFixed(1);
  t.value = ((f.duration || 0) / 2).toFixed(1);
  $("#pv-time-v").textContent = t.value + "s";
}
async function renderPreview() {
  if (pv.busy) return;
  pv.busy = true;
  const m = modelOf(pv.settings.model);
  $("#pv-loading").hidden = false;
  $("#pv-loading-text").textContent = m.ai ? "正在 AI 超分这一帧（首次需加载模型，约几秒）…" : "正在渲染预览…";
  $("#pv-render").disabled = true;
  const res = await api.preview(pv.file.path, pv.settings, parseFloat($("#pv-time").value));
  pv.busy = false;
  $("#pv-render").disabled = false;
  $("#pv-loading").hidden = true;
  if (!res.ok) return toast(res.error, true);
  $("#pv-before").src = res.before;
  $("#pv-after").src = res.after;
  const s = pv.settings;
  $("#pv-meta").textContent = `${res.width}×${res.height} · ${res.model}${m.ai ? " " + Math.round(s.strength * 100) + "%" : ""} · ${presetOf(s.preset).label} · 第 ${res.at}s · 渲染 ${res.seconds}s`;
  setCompare(pv.pos);
}
function setCompare(pct) {
  pv.pos = Math.max(0, Math.min(100, pct));
  $("#cmp-before").style.clipPath = `inset(0 ${100 - pv.pos}% 0 0)`;
  $("#cmp-handle").style.left = pv.pos + "%";
}
function bindCompare() {
  const inner = $("#cmp-inner");
  let drag = false;
  const move = (e) => {
    if (!drag) return;
    const r = inner.getBoundingClientRect();
    setCompare(((e.clientX - r.left) / r.width) * 100);
  };
  inner.addEventListener("pointerdown", (e) => { drag = true; inner.setPointerCapture(e.pointerId); move(e); });
  inner.addEventListener("pointermove", move);
  inner.addEventListener("pointerup", () => { drag = false; });
  $("#seg-zoom").onclick = (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    $$("#seg-zoom button").forEach((x) => x.classList.toggle("on", x === b));
    $("#compare").classList.toggle("zoom", b.dataset.v === "1");
  };
  $("#pv-close").onclick = () => { $("#modal").hidden = true; };
  $("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") $("#modal").hidden = true; });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("#modal").hidden = true; });
  $("#pv-time").addEventListener("input", (e) => { $("#pv-time-v").textContent = parseFloat(e.target.value).toFixed(1) + "s"; });
  $("#pv-time").addEventListener("change", renderPreview);
  $("#pv-file").addEventListener("change", (e) => { setPvFile(+e.target.value); renderPreview(); });
  $("#pv-render").onclick = () => { pv.settings = { ...settings }; renderPreview(); };
}

/* ---------- 外观 ---------- */

function applyTheme(t) {
  document.documentElement.dataset.theme = t;
}
function applyBackground(url) {
  const bg = $("#bg");
  bg.style.backgroundImage = url ? `url("${url}")` : "";
  bg.classList.toggle("has-image", !!url);
}

/* ---------- 启动 ---------- */

function bindUI() {
  $("#tabs").onclick = (e) => {
    const b = e.target.closest(".tab");
    if (!b) return;
    filter = b.dataset.filter;
    $$(".tab").forEach((t) => t.classList.toggle("active", t === b));
    renderJobs();
  };
  $("#search").addEventListener("input", (e) => { query = e.target.value.trim().toLowerCase(); renderJobs(); });
  $("#btn-theme").onclick = () => {
    const t = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    applyTheme(t);
    api.set_theme(t);
  };
  $("#btn-bg").onclick = async () => { const url = await api.pick_background(); if (url) applyBackground(url); };
  $("#btn-bg").oncontextmenu = (e) => { e.preventDefault(); api.clear_background(); applyBackground(""); toast("已恢复默认背景"); };
  $("#btn-resume-all").onclick = async () => { await api.resume_all(); toast("已继续所有暂停的任务"); };
  $("#btn-clear").onclick = async () => { await api.clear_finished(); jobs = await api.jobs(); renderJobs(); };

  $("#dropzone").addEventListener("click", (e) => {
    const x = e.target.closest(".x");
    if (x) { picked.splice(+x.dataset.i, 1); renderPicked(); return; }
    if (!e.target.closest(".dz-item")) pickVideos();
  });
  $("#empty").onclick = pickVideos;
  // 拖放的视觉反馈；真正的文件路径由 Python 端在 drop 时回传（window.onNativeDrop）
  // 拖入文件时显示一个空的全屏拖放层，Python 端只监听这一层的 drop（见 app.py）
  const overlay = $("#drop-overlay");
  const hasFiles = (e) => e.dataTransfer && [...e.dataTransfer.types].includes("Files");
  document.addEventListener("dragenter", (e) => { if (hasFiles(e)) { e.preventDefault(); overlay.hidden = false; } });
  document.addEventListener("dragover", (e) => e.preventDefault());
  overlay.addEventListener("dragleave", (e) => { if (e.target === overlay) overlay.hidden = true; });
  document.addEventListener("drop", (e) => { e.preventDefault(); setTimeout(() => { overlay.hidden = true; }, 0); });

  bindStaticSeg($("#seg-target"), "target");
  bindStaticSeg($("#seg-quality"), "quality");
  bindStaticSeg($("#seg-codec"), "codec");
  bindRanges();
  $("#btn-out").onclick = async () => {
    const dir = await api.pick_folder();
    settings.output_dir = dir || "";
    renderSettings();
    persist();
  };
  $("#btn-out").oncontextmenu = (e) => { e.preventDefault(); settings.output_dir = ""; renderSettings(); persist(); };
  $("#btn-preview").onclick = () => openPreview(picked, settings);
  $("#btn-start").onclick = start;
  $("#job-list").onclick = (e) => {
    const b = e.target.closest("[data-act]");
    if (b) jobAction(b.closest(".job").dataset.id, b.dataset.act);
  };
  $("#recent-list").onclick = (e) => {
    const b = e.target.closest("[data-act]");
    if (b) jobAction(b.closest(".recent-item").dataset.id, b.dataset.act);
  };
  bindCompare();
}

async function boot(theApi) {
  api = theApi;
  env = await api.env();
  settings = env.settings;
  if (!env.models.some((m) => m.key === settings.model)) settings.model = env.models[0].key;
  // 参数和画风预设对不上（比如模型不可用被替换了）就显示为"自定义"
  const st0 = styleOf(settings.style);
  if (st0 && Object.entries(st0.settings).some(([k, v]) => settings[k] !== v)) settings.style = "custom";
  applyTheme(env.theme);
  applyBackground(env.background);
  const warn = [];
  if (!env.ffmpeg) warn.push('<span class="warn">未找到 ffmpeg，请运行 setup.sh</span>');
  if (!env.realesrgan) warn.push('<span class="warn">未找到 AI 超分组件，目前只能用"无 AI"模式，请运行 setup.sh</span>');
  $("#env").innerHTML = [...warn, `编码器：${esc(env.encoder)} · v${esc(env.version)}`].join("<br>");
  bindUI();
  renderSettings();
  renderPicked();
  poll();
}

window.addEventListener("pywebviewready", () => boot(window.pywebview.api));
// 直接用浏览器打开 index.html 时，使用演示数据（便于调界面）
setTimeout(() => { if (!api && !window.pywebview) boot(window.mockApi ? window.mockApi() : null); }, 600);
