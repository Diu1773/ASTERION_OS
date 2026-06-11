/* Watchtower 대시보드 — WebSocket 실시간 + REST 액션 */
"use strict";

const $ = (id) => document.getElementById(id);

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
    logLine({ ts: new Date().toTimeString().slice(0, 8), source: "ui", level: "error", msg: `요청 거부: ${detail}` });
    throw new Error(detail);
  }
  return res.json();
}

// ---------- 라이브 로그 ----------

const logEl = $("log");
function logLine(e) {
  const div = document.createElement("div");
  div.className = `l-${e.level || "info"}`;
  div.innerHTML = `<span class="l-ts">${e.ts}</span> [${e.source}] ${escapeHtml(e.msg)}`;
  logEl.appendChild(div);
  while (logEl.childNodes.length > 300) logEl.removeChild(logEl.firstChild);
  logEl.scrollTop = logEl.scrollHeight;
}
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// ---------- 스파크라인 ----------

const sparkBuf = { temp: [], hum: [], wind: [] };
function pushSpark(key, v) {
  if (v === null || v === undefined) return;
  const buf = sparkBuf[key];
  buf.push(v);
  if (buf.length > 600) buf.shift();
}
function drawSpark(canvas, buf, color) {
  const ctx = canvas.getContext("2d");
  const w = canvas.width, h = canvas.height;
  ctx.clearRect(0, 0, w, h);
  if (buf.length < 2) return;
  const min = Math.min(...buf), max = Math.max(...buf);
  const span = (max - min) || 1;
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.2;
  ctx.beginPath();
  buf.forEach((v, i) => {
    const x = (i / (buf.length - 1)) * (w - 4) + 2;
    const y = h - 3 - ((v - min) / span) * (h - 6);
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  });
  ctx.stroke();
}

// ---------- 상태 반영 ----------

let lastStatus = null;
let filterOptionsReady = false;

function applyStatus(s) {
  lastStatus = s;

  // 시계·배지
  $("t-utc").textContent = (s.time?.utc || "").slice(11) || "--:--:--";
  $("t-kst").textContent = s.time?.kst || "--:--:--";
  $("t-lst").textContent = s.time?.lst || "--:--:--";
  if (s.site) $("site-name").textContent = `${s.site} Observation OS v0.1`;

  const modeBadge = $("badge-mode");
  modeBadge.textContent = (s.mode || "--").toUpperCase();
  modeBadge.className = `badge ${s.mode}`;

  const saf = s.safety || {};
  const safBadge = $("badge-safety");
  safBadge.textContent = saf.state || "--";
  safBadge.className = `badge s-${saf.state}`;
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
  $("c-temp").textContent = fmt(c.ccd_temp, 1, " °C");
  $("c-cooler").textContent = c.cooler_on ? "ON" : "OFF";
  $("c-state").textContent = c.state || "—";
  const f = s.filter || {};
  $("c-filter").textContent = f.name || "—";
  if (!filterOptionsReady && Array.isArray(f.names) && f.names.length) {
    const sel = $("sel-filter");
    sel.innerHTML = f.names.map((n, i) => `<option value="${i}">${n}</option>`).join("");
    filterOptionsReady = true;
  }

  // 기상
  const w = s.weather || {};
  $("w-temp").textContent = fmt(w.temp, 1, " °C");
  $("w-hum").textContent = fmt(w.humidity, 0, " %");
  $("w-dew").textContent = fmt(w.dew_point, 1, " °C");
  $("w-wind").textContent = fmt(w.wind, 1, " m/s");
  pushSpark("temp", w.temp); pushSpark("hum", w.humidity); pushSpark("wind", w.wind);
  drawSpark($("spark-temp"), sparkBuf.temp, "#38bdf8");
  drawSpark($("spark-hum"), sparkBuf.hum, "#34d399");
  drawSpark($("spark-wind"), sparkBuf.wind, "#fbbf24");

  // 하늘
  const sun = s.sun || {};
  $("s-sunalt").textContent = fmt(sun.alt, 1, "°");
  const tw = s.twilight_sim || {};
  $("s-phase").textContent = (sun.phase_label || "—") + (tw.enabled ? " (황혼시뮬)" : "");
  $("s-antisolar").textContent = fmt(sun.antisolar_az, 0, "°");
  // 하늘 원 밝기: 황혼시뮬이면 factor, 아니면 태양고도 기반
  let bright = tw.enabled ? Math.min(1, tw.factor) : Math.max(0, Math.min(1, (sun.alt + 18) / 36));
  $("allsky").style.filter = `brightness(${0.25 + 0.75 * bright})`;
  $("btn-twilight").classList.toggle("active", !!tw.enabled);
  $("twilight-row").style.display = s.mode === "sim" || tw.enabled ? "" : "none";

  // 오토플랫
  const af = s.autoflat || {};
  $("af-phase").textContent = af.phase || "idle";
  $("af-filter").textContent = af.filter || "—";
  $("af-frame").textContent = af.frame ? `${af.frame} / ${af.total}` : "—";
  $("af-exp").textContent = af.exposure ? `${Number(af.exposure).toFixed(2)} s` : "—";
  const aduEl = $("af-adu");
  aduEl.textContent = af.last_adu ? Number(af.last_adu).toLocaleString() : "—";
  const aduMin = Number($("af-adumin").value), aduMax = Number($("af-adumax").value);
  aduEl.className = af.last_adu ? (af.last_adu >= aduMin && af.last_adu <= aduMax ? "ok" : "bad") : "";
  $("btn-af-start").disabled = !!af.running;
  $("btn-af-stop").disabled = !af.running;
  if (af.running && af.total) {
    const filters = $("af-filters").value.split(",").map((x) => x.trim()).filter(Boolean);
    const done = Object.values(af.results || {}).reduce((a, b) => a + b, 0);
    const totalFrames = filters.length * af.total || 1;
    $("af-bar").style.width = `${Math.min(100, (done + (af.frame || 0) / af.total) / totalFrames * 100 * af.total).toFixed(0)}%`;
  } else if (!af.running) {
    $("af-bar").style.width = af.phase === "idle" && af.results ? "100%" : "0%";
  }
}

// ---------- 테이블 ----------

function frameRow(fr) {
  const flagCls = fr.flag === "ok" ? "ok" : "bad";
  return `<tr><td>${(fr.date_obs_utc || "").slice(11, 19)}</td><td>${fr.image_type}</td>` +
    `<td>${fr.filter_name}</td><td>${fmt(fr.exposure_s, 2)}s</td>` +
    `<td>${fr.median_adu ? Math.round(fr.median_adu).toLocaleString() : "—"}</td>` +
    `<td class="${flagCls}">${fr.flag}</td></tr>`;
}
function actionRow(a) {
  const cls = a.success ? "ok" : "fail";
  const msg = a.message === "ok" ? "" : a.message;
  return `<tr><td>${(a.utc || "").slice(11, 19)}</td><td>${a.action_type}</td>` +
    `<td>${a.actor}</td><td class="${cls}">${a.success ? "OK" : "FAIL"}</td>` +
    `<td title="${escapeHtml(msg)}">${escapeHtml(msg.slice(0, 60))}</td></tr>`;
}
function prependRow(tableId, html, max = 30) {
  const tbody = document.querySelector(`#${tableId} tbody`);
  tbody.insertAdjacentHTML("afterbegin", html);
  while (tbody.childNodes.length > max) tbody.removeChild(tbody.lastChild);
}

// ---------- WebSocket ----------

function connectWS() {
  const ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onopen = () => $("ws-dot").classList.add("on");
  ws.onclose = () => {
    $("ws-dot").classList.remove("on");
    setTimeout(connectWS, 2000);
  };
  ws.onmessage = (ev) => {
    const data = JSON.parse(ev.data);
    if (data.type === "status") applyStatus(data.status);
    else if (data.type === "log") logLine(data);
    else if (data.type === "frame") prependRow("tbl-frames", frameRow(data.frame));
    else if (data.type === "action") prependRow("tbl-actions", actionRow(data.action), 50);
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
    if (status.mode !== "starting") applyStatus(status);
    logs.forEach(logLine);
    frames.reverse().forEach((fr) => prependRow("tbl-frames", frameRow(fr)));
    actions.reverse().forEach((a) => prependRow("tbl-actions", actionRow(a), 50));
    // 설정 기본값 주입
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
  } catch (e) {
    logLine({ ts: "--:--:--", source: "ui", level: "error", msg: `초기 로드 실패: ${e}` });
  }
  connectWS();
}

// ---------- 버튼 ----------

$("btn-goto").onclick = () => {
  const alt = Number($("in-alt").value), az = Number($("in-az").value);
  if (!alt && alt !== 0) return;
  post("/api/actions/mount/goto", { alt, az });
};
$("btn-tracking").onclick = () => {
  const on = !(lastStatus?.mount?.tracking);
  post("/api/actions/mount/tracking", { on });
};
$("btn-stop").onclick = () => post("/api/actions/mount/stop");
$("btn-filter").onclick = () => {
  post("/api/actions/filter", { position: Number($("sel-filter").value) });
};
$("btn-cooler").onclick = () => {
  const on = !(lastStatus?.camera?.cooler_on);
  post("/api/actions/camera/cooler", { on });
};
$("btn-twilight").onclick = () => {
  const enabled = !(lastStatus?.twilight_sim?.enabled);
  post("/api/sim/twilight", { enabled });
};
$("btn-af-start").onclick = () => {
  const body = {
    filters: $("af-filters").value.split(",").map((x) => x.trim()).filter(Boolean),
    frames_per_filter: Number($("af-frames").value),
    adu_min: Number($("af-adumin").value),
    adu_max: Number($("af-adumax").value),
    dither_arcsec: Number($("af-dither").value),
    settle_seconds: Number($("af-settle").value),
    initial_exposure: Number($("af-initexp").value),
    max_exposure: Number($("af-maxexp").value),
  };
  post("/api/actions/autoflat/start", body);
};
$("btn-af-stop").onclick = () => post("/api/actions/autoflat/stop");

init();
