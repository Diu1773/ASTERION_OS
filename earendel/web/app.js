/* Earendel 통합 관측 플랫폼 대시보드 — WebSocket 실시간 + REST 액션 + 캔버스 시각화 */
"use strict";

const $ = (id) => document.getElementById(id);
const TAU = Math.PI * 2;

// ---------- 유틸 ----------

function fmt(v, d = 1, suffix = "") {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return Number(v).toFixed(d) + suffix;
}

async function post(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (e) { /* noop */ }
    logLine({ ts: nowts(), source: "ui", level: "error", msg: `요청 거부: ${detail}` });
    throw new Error(detail);
  }
  return res.json().catch(() => ({}));
}

function nowts() { return new Date().toTimeString().slice(0, 8); }
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }
function lerp(a, b, t) { return a + (b - a) * t; }
function mix(c1, c2, t) { return `rgb(${Math.round(lerp(c1[0], c2[0], t))},${Math.round(lerp(c1[1], c2[1], t))},${Math.round(lerp(c1[2], c2[2], t))})`; }

// HiDPI 캔버스 컨텍스트
function hidpi(cv) {
  const dpr = window.devicePixelRatio || 1;
  const r = cv.getBoundingClientRect();
  const w = Math.max(1, r.width), h = Math.max(1, r.height);
  if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) {
    cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr);
  }
  const ctx = cv.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { ctx, w, h };
}

// ---------- 라이브 로그 ----------

const logEl = $("log");
function logLine(e) {
  const div = document.createElement("div");
  div.className = `l-${e.level || "info"}`;
  div.innerHTML = `<span class="l-ts">${e.ts}</span> <span class="l-src">[${e.source}]</span> ${escapeHtml(e.msg)}`;
  logEl.appendChild(div);
  while (logEl.childNodes.length > 300) logEl.removeChild(logEl.firstChild);
  logEl.scrollTop = logEl.scrollHeight;
}

// ---------- 스파크라인 ----------

const sparkBuf = { temp: [], hum: [], wind: [] };
function pushSpark(key, v) {
  if (v === null || v === undefined) return;
  const buf = sparkBuf[key]; buf.push(v);
  if (buf.length > 600) buf.shift();
}
function drawSpark(id, buf, color) {
  const cv = $(id); if (!cv) return;
  const { ctx, w, h } = hidpi(cv);
  ctx.clearRect(0, 0, w, h);
  if (buf.length < 2) return;
  const min = Math.min(...buf), max = Math.max(...buf), span = (max - min) || 1;
  const x = (i) => (i / (buf.length - 1)) * (w - 4) + 2;
  const y = (v) => h - 3 - ((v - min) / span) * (h - 6);
  // fill
  ctx.beginPath(); ctx.moveTo(x(0), h);
  buf.forEach((v, i) => ctx.lineTo(x(i), y(v)));
  ctx.lineTo(x(buf.length - 1), h); ctx.closePath();
  ctx.fillStyle = color + "22"; ctx.fill();
  // line
  ctx.beginPath();
  buf.forEach((v, i) => i ? ctx.lineTo(x(i), y(v)) : ctx.moveTo(x(i), y(v)));
  ctx.strokeStyle = color; ctx.lineWidth = 1.3; ctx.stroke();
  // last dot
  ctx.beginPath(); ctx.arc(x(buf.length - 1), y(buf[buf.length - 1]), 1.8, 0, TAU);
  ctx.fillStyle = color; ctx.fill();
}

// ---------- 하늘 돔 ----------

function drawSky(s) {
  const cv = $("sky-canvas"); if (!cv) return;
  const { ctx, w, h } = hidpi(cv);
  ctx.clearRect(0, 0, w, h);
  const cx = w / 2, cy = h / 2, R = Math.min(w, h) / 2 - 12;
  const sun = s.sun || {}, tw = s.twilight_sim || {}, m = s.mount || {};

  // 하늘 밝기 0(밤)~1(낮)
  let b = tw.enabled ? clamp(tw.factor || 0, 0, 1)
                     : clamp(((sun.alt ?? -18) + 18) / 30, 0, 1);
  const g = ctx.createRadialGradient(cx, cy - R * 0.15, R * 0.1, cx, cy, R);
  g.addColorStop(0, mix([12, 18, 33], [96, 150, 214], b));
  g.addColorStop(0.7, mix([9, 13, 26], [60, 100, 165], b));
  g.addColorStop(1, mix([5, 8, 16], [150, 110, 70], b * 0.7)); // 지평선 노을빛
  ctx.beginPath(); ctx.arc(cx, cy, R, 0, TAU); ctx.fillStyle = g; ctx.fill();

  // 고도 링
  ctx.lineWidth = 1;
  [30, 60].forEach((alt) => {
    const r = (90 - alt) / 90 * R;
    ctx.beginPath(); ctx.arc(cx, cy, r, 0, TAU);
    ctx.strokeStyle = "rgba(150,180,225,.14)"; ctx.stroke();
  });
  // 십자선
  ctx.strokeStyle = "rgba(150,180,225,.10)";
  ctx.beginPath(); ctx.moveTo(cx - R, cy); ctx.lineTo(cx + R, cy);
  ctx.moveTo(cx, cy - R); ctx.lineTo(cx, cy + R); ctx.stroke();
  // 지평선 테두리
  ctx.beginPath(); ctx.arc(cx, cy, R, 0, TAU);
  ctx.strokeStyle = "rgba(150,180,225,.4)"; ctx.lineWidth = 1.5; ctx.stroke();
  // 방위 라벨
  ctx.fillStyle = "rgba(190,210,240,.65)"; ctx.font = "11px monospace";
  ctx.textAlign = "center"; ctx.textBaseline = "middle";
  ctx.fillText("N", cx, cy - R + 11); ctx.fillText("S", cx, cy + R - 11);
  ctx.fillText("E", cx + R - 11, cy); ctx.fillText("W", cx - R + 11, cy);

  const proj = (alt, az) => {
    const r = (90 - clamp(alt, 0, 90)) / 90 * R, a = az * Math.PI / 180;
    return [cx + r * Math.sin(a), cy - r * Math.cos(a)];
  };

  // 태양
  if (sun.az !== undefined && sun.az !== null) {
    const below = (sun.alt ?? -1) < 0;
    const [sx, sy] = proj(below ? 0 : sun.alt, sun.az);
    if (!below) { ctx.shadowColor = "#ffce6b"; ctx.shadowBlur = 20; }
    ctx.beginPath(); ctx.arc(sx, sy, below ? 5 : 8, 0, TAU);
    ctx.fillStyle = below ? "rgba(255,170,90,.4)" : "#ffd884"; ctx.fill();
    ctx.shadowBlur = 0;
  }

  // 망원경 포인팅
  if (m.alt !== null && m.alt !== undefined && m.az !== null && m.az !== undefined) {
    const [tx, ty] = proj(m.alt, m.az);
    const col = m.slewing ? "#fbbf24" : "#4cc9f0";
    ctx.strokeStyle = col; ctx.lineWidth = 1.5;
    ctx.beginPath(); ctx.arc(tx, ty, 7, 0, TAU); ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(tx - 12, ty); ctx.lineTo(tx - 3, ty); ctx.moveTo(tx + 3, ty); ctx.lineTo(tx + 12, ty);
    ctx.moveTo(tx, ty - 12); ctx.lineTo(tx, ty - 3); ctx.moveTo(tx, ty + 3); ctx.lineTo(tx, ty + 12);
    ctx.stroke();
    ctx.beginPath(); ctx.arc(tx, ty, 2, 0, TAU); ctx.fillStyle = col; ctx.fill();
  }
}

// ---------- ADU 게이지 ----------

function drawGauge(af, aduMin, aduMax) {
  const cv = $("af-gauge"); if (!cv) return;
  const { ctx, w, h } = hidpi(cv);
  ctx.clearRect(0, 0, w, h);
  const cx = w / 2, cy = h * 0.9, R = Math.min(w / 2, h * 0.86) - 10;
  const A0 = Math.PI, A1 = TAU;
  const scaleMax = Math.max(aduMax * 2, 40000);
  const val = af.last_adu || 0;
  const ang = (v) => A0 + (clamp(v, 0, scaleMax) / scaleMax) * (A1 - A0);
  ctx.lineCap = "round"; ctx.lineWidth = 13;

  // 트랙
  ctx.beginPath(); ctx.arc(cx, cy, R, A0, A1);
  ctx.strokeStyle = "rgba(130,170,220,.13)"; ctx.stroke();
  // 목표 밴드
  ctx.beginPath(); ctx.arc(cx, cy, R, ang(aduMin), ang(aduMax));
  ctx.strokeStyle = "rgba(52,211,153,.75)"; ctx.stroke();
  // 값 호
  const inBand = val >= aduMin && val <= aduMax;
  if (val > 0) {
    ctx.beginPath(); ctx.arc(cx, cy, R, A0, ang(val));
    ctx.strokeStyle = inBand ? "#34d399" : "#fbbf24"; ctx.stroke();
  }
  // 바늘
  if (val > 0) {
    const a = ang(val);
    ctx.beginPath(); ctx.moveTo(cx, cy);
    ctx.lineTo(cx + (R - 3) * Math.cos(a), cy + (R - 3) * Math.sin(a));
    ctx.strokeStyle = "#e6eefb"; ctx.lineWidth = 2; ctx.stroke();
  }
  ctx.beginPath(); ctx.arc(cx, cy, 4, 0, TAU); ctx.fillStyle = "#e6eefb"; ctx.fill();
  // 중앙 텍스트
  ctx.textAlign = "center"; ctx.textBaseline = "alphabetic";
  ctx.fillStyle = val ? (inBand ? "#34d399" : "#fbbf24") : "#8194b4";
  ctx.font = "bold 23px monospace";
  ctx.fillText(val ? Math.round(val).toLocaleString() : "—", cx, cy - R * 0.30);
  ctx.fillStyle = "#4b5d7c"; ctx.font = "10px monospace";
  ctx.fillText(`ADU · 목표 ${Math.round(aduMin / 1000)}–${Math.round(aduMax / 1000)}k`, cx, cy - R * 0.30 + 17);
}

function aduRange() {
  return [Number($("af-adumin").value) || 20000, Number($("af-adumax").value) || 25000];
}

// ---------- 상태 반영 ----------

let lastStatus = null;
let filterOptionsReady = false;

function applyStatus(s) {
  lastStatus = s;

  // 시계 / 배지
  $("t-utc").textContent = (s.time?.utc || "").slice(11) || "--:--:--";
  $("t-kst").textContent = s.time?.kst || "--:--:--";
  $("t-lst").textContent = s.time?.lst || "--:--:--";
  if (s.site) $("site-name").textContent = `${s.site} 통합 관측 OS`;

  const modeBadge = $("badge-mode");
  modeBadge.textContent = (s.mode || "--").toUpperCase();
  modeBadge.className = `badge ${s.mode}`;

  const saf = s.safety || {};
  const safBadge = $("badge-safety");
  safBadge.textContent = saf.state || "--";
  safBadge.className = `badge safety s-${saf.state}`;
  safBadge.title = (saf.reasons || []).join(", ");

  // 망원경
  const m = s.mount || {};
  $("m-alt").textContent = fmt(m.alt, 2, "°");
  $("m-az").textContent = fmt(m.az, 2, "°");
  $("m-ra").textContent = m.ra_str || "—";
  $("m-dec").textContent = m.dec_str || "—";
  $("m-track").classList.toggle("on", !!m.tracking);
  $("m-slew").classList.toggle("on", !!m.slewing);

  // 카메라
  const c = s.camera || {};
  $("c-temp").textContent = fmt(c.ccd_temp, 1, " ℃");
  $("c-cooler").textContent = c.cooler_on ? "ON" : "OFF";
  $("c-state").textContent = c.state || "—";
  const f = s.filter || {};
  $("c-filter").textContent = f.name || "—";
  if (!filterOptionsReady && Array.isArray(f.names) && f.names.length) {
    $("sel-filter").innerHTML = f.names.map((n, i) => `<option value="${i}">${n}</option>`).join("");
    filterOptionsReady = true;
  }

  // 기상
  const w = s.weather || {};
  $("w-temp").textContent = fmt(w.temp, 1, "℃");
  $("w-hum").textContent = fmt(w.humidity, 0, "%");
  $("w-wind").textContent = fmt(w.wind, 1, " m/s");
  $("w-dew").textContent = fmt(w.dew_point, 1, " ℃");
  pushSpark("temp", w.temp); pushSpark("hum", w.humidity); pushSpark("wind", w.wind);
  drawSpark("spark-temp", sparkBuf.temp, "#38bdf8");
  drawSpark("spark-hum", sparkBuf.hum, "#34d399");
  drawSpark("spark-wind", sparkBuf.wind, "#fbbf24");

  // 하늘
  const sun = s.sun || {}, tw = s.twilight_sim || {};
  $("s-sunalt").textContent = fmt(sun.alt, 1, "°");
  $("s-phase").textContent = (sun.phase_label || "—") + (tw.enabled ? " · 황혼시뮬" : "");
  $("s-antisolar").textContent = fmt(sun.antisolar_az, 0, "°");
  $("btn-twilight").classList.toggle("active", !!tw.enabled);
  $("twilight-row").style.display = (s.mode === "sim" || tw.enabled) ? "" : "none";
  drawSky(s);

  // 오토플랫
  const af = s.autoflat || {};
  const [aduMin, aduMax] = aduRange();
  $("af-filter").textContent = af.filter || "—";
  $("af-frame").textContent = af.frame ? `${af.frame} / ${af.total}` : "—";
  $("af-exp").textContent = af.exposure ? `${Number(af.exposure).toFixed(2)}s` : "—";
  $("af-adu").textContent = af.last_adu ? Number(af.last_adu).toLocaleString() : "—";
  $("af-phase").textContent = af.running ? (af.phase || "...") : "대기 중 (idle)";
  $("btn-af-start").disabled = !!af.running;
  $("btn-af-stop").disabled = !af.running;
  drawGauge(af, aduMin, aduMax);
  // 진행률
  const filtCount = $("af-filters").value.split(",").map((x) => x.trim()).filter(Boolean).length || 1;
  const compF = Object.keys(af.results || {}).length;
  let pct = 0;
  if (af.running && af.total) pct = clamp((compF * af.total + (af.frame || 0)) / (filtCount * af.total) * 100, 0, 99);
  else if (!af.running && compF) pct = 100;
  $("af-bar").style.width = pct + "%";

  // 시스템 카드
  $("sys-safety-state").textContent = saf.state || "—";
  $("sys-safety-state").className = `safety-state s-${saf.state}`;
  $("sys-safety-reason").textContent = (saf.reasons || []).join(", ");
  setSysRow("wt", "run", saf.state || "감시 중");
  setSysRow("sf", af.running ? "run" : "idle", af.running ? (af.phase || "running") : "idle");

  // 개발자 패널 (열려 있을 때만 갱신)
  if ($("dev-drawer").classList.contains("open")) renderDev(s);
  updateModeSeg(s.mode);
}

function setSysRow(key, state, text) {
  const dot = $(`sys-${key}-dot`);
  if (dot) dot.className = "sys-dot" + (state === "idle" ? " idle" : state === "run" ? " run" : "");
  const st = $(`sys-${key}-state`);
  if (st) st.textContent = text;
}

// ---------- 개발자 드로어 ----------

function updateModeSeg(mode) {
  const active = (mode === "sim") ? "sim" : "real";
  document.querySelectorAll("#mode-seg .seg-btn").forEach((b) =>
    b.classList.toggle("active", b.dataset.mode === active));
}

function renderDev(s) {
  // 일반 설정(관측 정보)
  $("set-site").textContent = s.site || "—";
  $("set-mode").textContent = (s.mode || "—").toUpperCase();
  $("set-lst").textContent = s.time?.lst || "—";
  const sun = s.sun || {}, tw = s.twilight_sim || {};
  $("set-phase").textContent = (sun.phase_label || "—") + (tw.enabled ? " · 황혼시뮬" : "");
  // 개발자: 장비 연결 상태
  const rows = [
    ["마운트", s.mount?.connected, s.mount?.detail || ""],
    ["카메라", s.camera?.connected, s.camera?.detail || ""],
    ["필터휠", s.filter?.connected, s.filter?.name ? `pos ${s.filter.position} · ${s.filter.name}` : ""],
    ["기상", s.weather?.temp != null, "SIM"],
  ];
  $("dev-devices").innerHTML = rows.map(([nm, on, dl]) =>
    `<div class="dev-dev"><div class="nm"><span class="cd ${on ? "on" : "off"}"></span>${nm}</div><span class="dl">${escapeHtml(dl)}</span></div>`
  ).join("");
}

// 개발자 모드 (localStorage 저장; 고급 설정 노출 여부)
const DEVMODE_KEY = "earendel.devmode";
function applyDevMode(on) {
  $("dev-drawer").classList.toggle("devmode", on);
  $("devmode-toggle").checked = on;
  try { localStorage.setItem(DEVMODE_KEY, on ? "1" : "0"); } catch (e) { /* noop */ }
}

function openDrawer(open) {
  $("dev-drawer").classList.toggle("open", open);
  $("dev-overlay").classList.toggle("open", open);
  if (open && lastStatus) renderDev(lastStatus);
}

// ---------- 테이블 ----------

function frameRow(fr) {
  const cls = fr.flag === "ok" ? "ok" : "bad";
  return `<tr><td>${(fr.date_obs_utc || "").slice(11, 19)}</td><td>${fr.image_type}</td>` +
    `<td>${fr.filter_name}</td><td>${fmt(fr.exposure_s, 2)}s</td>` +
    `<td>${fr.median_adu ? Math.round(fr.median_adu).toLocaleString() : "—"}</td>` +
    `<td class="${cls}">${fr.flag}</td></tr>`;
}
function actionRow(a) {
  const cls = a.success ? "ok" : "fail";
  const msg = a.message === "ok" ? "" : (a.message || "");
  return `<tr><td>${(a.utc || "").slice(11, 19)}</td><td>${a.action_type}</td>` +
    `<td>${a.actor}</td><td class="${cls}">${a.success ? "OK" : "FAIL"}</td>` +
    `<td title="${escapeHtml(msg)}">${escapeHtml(msg.slice(0, 50))}</td></tr>`;
}
function prependRow(tableId, html, max = 40) {
  const tbody = document.querySelector(`#${tableId} tbody`);
  tbody.insertAdjacentHTML("afterbegin", html);
  while (tbody.childNodes.length > max) tbody.removeChild(tbody.lastChild);
}

// ---------- WebSocket ----------

function connectWS() {
  const ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onopen = () => $("ws-dot").classList.add("on");
  ws.onclose = () => { $("ws-dot").classList.remove("on"); setTimeout(connectWS, 2000); };
  ws.onmessage = (ev) => {
    const data = JSON.parse(ev.data);
    if (data.type === "status") applyStatus(data.status);
    else if (data.type === "log") logLine(data);
    else if (data.type === "frame") prependRow("tbl-frames", frameRow(data.frame));
    else if (data.type === "action") prependRow("tbl-actions", actionRow(data.action));
  };
}

// ---------- 초기 로드 ----------

async function init() {
  try {
    const [status, logs, frames, actions] = await Promise.all([
      fetch("/api/status").then((r) => r.json()),
      fetch("/api/logs").then((r) => r.json()),
      fetch("/api/frames").then((r) => r.json()),
      fetch("/api/actionlog").then((r) => r.json()),
    ]);
    const d = status.defaults?.autoflat;
    if (d) {
      if (d.filters) $("af-filters").value = d.filters.join(",");
      if (d.frames_per_filter) $("af-frames").value = d.frames_per_filter;
      if (d.adu_min) $("af-adumin").value = d.adu_min;
      if (d.adu_max) $("af-adumax").value = d.adu_max;
      if (d.dither_arcsec) $("af-dither").value = d.dither_arcsec;
      if (d.settle_seconds !== undefined) $("af-settle").value = d.settle_seconds;
      if (d.initial_exposure) $("af-initexp").value = d.initial_exposure;
      if (d.max_exposure) $("af-maxexp").value = d.max_exposure;
    }
    if (status.mode && status.mode !== "starting") applyStatus(status);
    logs.forEach(logLine);
    frames.reverse().forEach((fr) => prependRow("tbl-frames", frameRow(fr)));
    actions.reverse().forEach((a) => prependRow("tbl-actions", actionRow(a)));
  } catch (e) {
    logLine({ ts: nowts(), source: "ui", level: "error", msg: `초기 로드 실패: ${e}` });
  }
  connectWS();
}

// ---------- 버튼 핸들러 ----------

$("btn-goto").onclick = () => {
  const alt = Number($("in-alt").value), az = Number($("in-az").value);
  if (Number.isNaN(alt)) return;
  post("/api/actions/mount/goto", { alt, az });
};
$("btn-tracking").onclick = () => post("/api/actions/mount/tracking", { on: !(lastStatus?.mount?.tracking) });
$("btn-stop").onclick = () => post("/api/actions/mount/stop");
$("btn-filter").onclick = () => post("/api/actions/filter", { position: Number($("sel-filter").value) });
$("btn-cooler").onclick = () => post("/api/actions/camera/cooler", { on: !(lastStatus?.camera?.cooler_on) });

const toggleTwilight = () => post("/api/sim/twilight", { enabled: !(lastStatus?.twilight_sim?.enabled) });
$("btn-twilight").onclick = toggleTwilight;
$("btn-twilight2").onclick = toggleTwilight;

$("btn-af-start").onclick = () => post("/api/actions/autoflat/start", {
  filters: $("af-filters").value.split(",").map((x) => x.trim()).filter(Boolean),
  frames_per_filter: Number($("af-frames").value),
  adu_min: Number($("af-adumin").value),
  adu_max: Number($("af-adumax").value),
  dither_arcsec: Number($("af-dither").value),
  settle_seconds: Number($("af-settle").value),
  initial_exposure: Number($("af-initexp").value),
  max_exposure: Number($("af-maxexp").value),
});
$("btn-af-stop").onclick = () => post("/api/actions/autoflat/stop");

// 설정 드로어
$("dev-btn").onclick = () => openDrawer(true);
$("dev-close").onclick = () => openDrawer(false);
$("dev-overlay").onclick = () => openDrawer(false);
// 개발자 모드 토글 (기본 OFF — 일반 관측 설정만; 켜면 고급 설정 노출)
applyDevMode(localStorage.getItem(DEVMODE_KEY) === "1");
$("devmode-toggle").onchange = (e) => applyDevMode(e.target.checked);
document.querySelectorAll("#mode-seg .seg-btn").forEach((b) => {
  b.onclick = async () => {
    if (b.classList.contains("active")) return;
    try {
      const r = await post("/api/dev/mode", { mode: b.dataset.mode });
      logLine({ ts: nowts(), source: "dev", level: "info", msg: `드라이버 모드 → ${(r.mode || b.dataset.mode).toUpperCase()}` });
    } catch (e) { /* post() already logged */ }
  };
});

// 리사이즈 시 캔버스 다시 그리기
let resizeTimer = null;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => { if (lastStatus) applyStatus(lastStatus); }, 120);
});

init();
