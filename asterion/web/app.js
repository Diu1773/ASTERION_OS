/* Asterion 통합 관측 플랫폼 대시보드
   — WebSocket 실시간, 클릭-GoTo 전천 돔, 캡처/포커서 콘솔,
     야간 타임라인, 자율형 시계열 플롯 빌더, 패널 매니저 */
"use strict";

const $ = (id) => document.getElementById(id);
const TAU = Math.PI * 2;
const D2R = Math.PI / 180;

// ---------- 유틸 ----------

function fmt(v, d = 1, suffix = "") {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return Number(v).toFixed(d) + suffix;
}
function nowts() { return new Date().toTimeString().slice(0, 8); }
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }
function lerp(a, b, t) { return a + (b - a) * t; }
function mix(c1, c2, t) {
  return `rgb(${Math.round(lerp(c1[0], c2[0], t))},${Math.round(lerp(c1[1], c2[1], t))},${Math.round(lerp(c1[2], c2[2], t))})`;
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

// HiDPI 캔버스
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

// alt/az ↔ RA/Dec (서버 ephemeris 미러)
function altazToRadec(alt, az, lat, lstH) {
  const a = alt * D2R, A = az * D2R, p = lat * D2R;
  const sd = Math.sin(a) * Math.sin(p) + Math.cos(a) * Math.cos(p) * Math.cos(A);
  const dec = Math.asin(clamp(sd, -1, 1));
  const cH = (Math.sin(a) - Math.sin(p) * sd) /
             Math.max(1e-9, Math.cos(p) * Math.cos(dec));
  let H = Math.acos(clamp(cH, -1, 1)) / D2R;
  if (Math.sin(A) > 0) H = -H;
  const ra = (((lstH - H / 15) % 24) + 24) % 24;
  return [ra, dec / D2R];
}
function fmtRa(h) {
  if (h === null || h === undefined) return "—";
  const t = ((h % 24) + 24) % 24;
  const hh = Math.floor(t), mm = Math.floor((t - hh) * 60);
  const ss = ((t - hh) * 60 - mm) * 60;
  return `${String(hh).padStart(2, "0")}:${String(mm).padStart(2, "0")}:${ss.toFixed(0).padStart(2, "0")}`;
}
function fmtDec(d) {
  if (d === null || d === undefined) return "—";
  const sg = d >= 0 ? "+" : "-", a = Math.abs(d);
  const dd = Math.floor(a), mm = Math.floor((a - dd) * 60);
  const ss = ((a - dd) * 60 - mm) * 60;
  return `${sg}${String(dd).padStart(2, "0")}:${String(mm).padStart(2, "0")}:${ss.toFixed(0).padStart(2, "0")}`;
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
  ctx.beginPath(); ctx.moveTo(x(0), h);
  buf.forEach((v, i) => ctx.lineTo(x(i), y(v)));
  ctx.lineTo(x(buf.length - 1), h); ctx.closePath();
  ctx.fillStyle = color + "22"; ctx.fill();
  ctx.beginPath();
  buf.forEach((v, i) => i ? ctx.lineTo(x(i), y(v)) : ctx.moveTo(x(i), y(v)));
  ctx.strokeStyle = color; ctx.lineWidth = 1.3; ctx.stroke();
}

// ---------- 전천 돔 (클릭-GoTo) ----------

let skyTarget = null;   // {alt, az, ts} — 클릭으로 지정한 목표
let skyGeom = null;     // {cx, cy, R} — 마지막 그리기 기하 (클릭 역변환용)
let mountDraw = null;   // 화면에 그리는 망원경 위치 (서버 1Hz 값으로 이징)

// 서버 위치(1Hz, 슬루 보간됨)를 향해 매 프레임 부드럽게 따라감
function easeMount(m) {
  if (m.alt === null || m.az === null) return mountDraw;
  if (!mountDraw) { mountDraw = { alt: m.alt, az: m.az }; return mountDraw; }
  const k = 0.2;
  mountDraw.alt += (m.alt - mountDraw.alt) * k;
  const daz = ((m.az - mountDraw.az + 540) % 360) - 180;
  mountDraw.az = (mountDraw.az + daz * k + 360) % 360;
  return mountDraw;
}

function drawSky(s) {
  const cv = $("sky-canvas"); if (!cv) return;
  const { ctx, w, h } = hidpi(cv);
  ctx.clearRect(0, 0, w, h);
  const cx = w / 2, cy = h / 2, R = Math.min(w, h) / 2 - 12;
  skyGeom = { cx, cy, R };
  const sun = s.sun || {}, tw = s.twilight_sim || {}, m = s.mount || {};

  let b = tw.enabled ? clamp(tw.factor || 0, 0, 1)
                     : clamp(((sun.alt ?? -18) + 18) / 36, 0, 1);
  const g = ctx.createRadialGradient(cx, cy - R * 0.15, R * 0.1, cx, cy, R);
  g.addColorStop(0, mix([12, 18, 33], [96, 150, 214], b));
  g.addColorStop(0.7, mix([9, 13, 26], [60, 100, 165], b));
  g.addColorStop(1, mix([5, 8, 16], [150, 110, 70], b * 0.7));
  ctx.beginPath(); ctx.arc(cx, cy, R, 0, TAU); ctx.fillStyle = g; ctx.fill();

  ctx.lineWidth = 1;
  [30, 60].forEach((alt) => {
    const r = (90 - alt) / 90 * R;
    ctx.beginPath(); ctx.arc(cx, cy, r, 0, TAU);
    ctx.strokeStyle = "rgba(150,180,225,.14)"; ctx.stroke();
  });
  ctx.strokeStyle = "rgba(150,180,225,.10)";
  ctx.beginPath(); ctx.moveTo(cx - R, cy); ctx.lineTo(cx + R, cy);
  ctx.moveTo(cx, cy - R); ctx.lineTo(cx, cy + R); ctx.stroke();
  ctx.beginPath(); ctx.arc(cx, cy, R, 0, TAU);
  ctx.strokeStyle = "rgba(150,180,225,.4)"; ctx.lineWidth = 1.5; ctx.stroke();
  ctx.fillStyle = "rgba(190,210,240,.65)"; ctx.font = "11px monospace";
  ctx.textAlign = "center"; ctx.textBaseline = "middle";
  ctx.fillText("N", cx, cy - R + 11); ctx.fillText("S", cx, cy + R - 11);
  ctx.fillText("E", cx + R - 11, cy); ctx.fillText("W", cx - R + 11, cy);

  const proj = (alt, az) => {
    const r = (90 - clamp(alt, 0, 90)) / 90 * R, a = az * D2R;
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

  const hasMount = m.alt !== null && m.alt !== undefined &&
                   m.az !== null && m.az !== undefined;
  const md = hasMount ? easeMount(m) : null;  // 부드러운 보간 위치

  // 클릭 목표 마커 (주황 다이아) + 망원경→목표 점선
  if (skyTarget) {
    const [gx, gy] = proj(skyTarget.alt, skyTarget.az);
    if (md) {
      const [tx, ty] = proj(md.alt, md.az);
      ctx.setLineDash([4, 4]);
      ctx.beginPath(); ctx.moveTo(tx, ty); ctx.lineTo(gx, gy);
      ctx.strokeStyle = "rgba(251,146,60,.6)"; ctx.lineWidth = 1.2; ctx.stroke();
      ctx.setLineDash([]);
    }
    ctx.save();
    ctx.translate(gx, gy); ctx.rotate(Math.PI / 4);
    ctx.strokeStyle = "#fb923c"; ctx.lineWidth = 1.6;
    ctx.strokeRect(-5, -5, 10, 10);
    ctx.restore();
    const pulse = (Date.now() % 1600) / 1600;
    ctx.beginPath(); ctx.arc(gx, gy, 7 + pulse * 9, 0, TAU);
    ctx.strokeStyle = `rgba(251,146,60,${0.55 * (1 - pulse)})`;
    ctx.lineWidth = 1.2; ctx.stroke();
  }

  // 망원경 포인팅 (청록 십자 / 슬루 중 호박색)
  if (md) {
    const [tx, ty] = proj(md.alt, md.az);
    const col = m.slewing ? "#fbbf24" : "#4cc9f0";
    ctx.strokeStyle = col; ctx.lineWidth = 1.5;
    ctx.beginPath(); ctx.arc(tx, ty, 7, 0, TAU); ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(tx - 12, ty); ctx.lineTo(tx - 3, ty);
    ctx.moveTo(tx + 3, ty); ctx.lineTo(tx + 12, ty);
    ctx.moveTo(tx, ty - 12); ctx.lineTo(tx, ty - 3);
    ctx.moveTo(tx, ty + 3); ctx.lineTo(tx, ty + 12);
    ctx.stroke();
    ctx.beginPath(); ctx.arc(tx, ty, 2, 0, TAU); ctx.fillStyle = col; ctx.fill();

    // 목표 도달 → 마커 자동 해제
    if (skyTarget && !m.slewing) {
      const dAlt = Math.abs(m.alt - skyTarget.alt);
      const dAz = Math.abs(((m.az - skyTarget.az + 540) % 360) - 180) *
                  Math.cos(m.alt * D2R);
      if (Math.hypot(dAlt, dAz) < 0.7) skyTarget = null;
    }
  }
}

// 클릭 → alt/az 역변환 → GoTo 메뉴
function skyClickHandler(ev) {
  if (!skyGeom || !lastStatus) return;
  const cv = $("sky-canvas");
  const rect = cv.getBoundingClientRect();
  const px = ev.clientX - rect.left, py = ev.clientY - rect.top;
  const dx = px - skyGeom.cx, dy = py - skyGeom.cy;
  const r = Math.hypot(dx, dy);
  if (r > skyGeom.R) { hideSkyMenu(); return; }
  const alt = 90 - (r / skyGeom.R) * 90;
  const az = ((Math.atan2(dx, -dy) / D2R) + 360) % 360;
  const lat = lastStatus.geo?.lat ?? 36.6;
  const lstH = lastStatus.time?.lst_hours ?? 0;
  const [ra, dec] = altazToRadec(alt, az, lat, lstH);

  const menu = $("sky-menu");
  menu.innerHTML =
    `<div class="sm-line">ALT ${alt.toFixed(1)}° · AZ ${az.toFixed(1)}°</div>` +
    `<div class="sm-line sm-sub">RA ${fmtRa(ra)} · DEC ${fmtDec(dec)}</div>` +
    `<div class="ctrl-row">` +
    `<button class="btn btn-go" id="sm-goto">GoTo</button>` +
    `<button class="btn" id="sm-close">닫기</button></div>`;
  const wrap = $("sky-wrap").getBoundingClientRect();
  menu.style.left = clamp(ev.clientX - wrap.left + 8, 0, wrap.width - 180) + "px";
  menu.style.top = clamp(ev.clientY - wrap.top + 8, 0, wrap.height - 90) + "px";
  menu.style.display = "block";
  $("sm-goto").onclick = async () => {
    hideSkyMenu();
    skyTarget = { alt, az, ts: Date.now() };
    kickSky();
    try { await post("/api/actions/mount/goto", { alt, az }); }
    catch (e) { skyTarget = null; }
  };
  $("sm-close").onclick = hideSkyMenu;
}
function hideSkyMenu() { $("sky-menu").style.display = "none"; }

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
  ctx.beginPath(); ctx.arc(cx, cy, R, A0, A1);
  ctx.strokeStyle = "rgba(130,170,220,.13)"; ctx.stroke();
  ctx.beginPath(); ctx.arc(cx, cy, R, ang(aduMin), ang(aduMax));
  ctx.strokeStyle = "rgba(52,211,153,.75)"; ctx.stroke();
  const inBand = val >= aduMin && val <= aduMax;
  if (val > 0) {
    ctx.beginPath(); ctx.arc(cx, cy, R, A0, ang(val));
    ctx.strokeStyle = inBand ? "#34d399" : "#fbbf24"; ctx.stroke();
    const a = ang(val);
    ctx.beginPath(); ctx.moveTo(cx, cy);
    ctx.lineTo(cx + (R - 3) * Math.cos(a), cy + (R - 3) * Math.sin(a));
    ctx.strokeStyle = "#e6eefb"; ctx.lineWidth = 2; ctx.stroke();
  }
  ctx.beginPath(); ctx.arc(cx, cy, 4, 0, TAU); ctx.fillStyle = "#e6eefb"; ctx.fill();
  ctx.textAlign = "center"; ctx.textBaseline = "alphabetic";
  ctx.fillStyle = val ? (inBand ? "#34d399" : "#fbbf24") : "#8194b4";
  ctx.font = "bold 23px monospace";
  ctx.fillText(val ? Math.round(val).toLocaleString() : "—", cx, cy - R * 0.30);
  ctx.fillStyle = "#4b5d7c"; ctx.font = "10px monospace";
  ctx.fillText(`ADU · 목표 ${Math.round(aduMin / 1000)}–${Math.round(aduMax / 1000)}k`,
               cx, cy - R * 0.30 + 17);
}
function aduRange() {
  return [Number($("af-adumin").value) || 20000,
          Number($("af-adumax").value) || 25000];
}

// ---------- 야간 타임라인 ----------

let timelineData = null;
let trackData = null;
let lastTimelineDraw = 0;

async function fetchTimeline() {
  try {
    timelineData = await (await fetch("/api/night/timeline")).json();
    drawTimeline();
  } catch (e) { /* 다음 주기에 재시도 */ }
}

async function fetchTrack(ra, dec, name) {
  try {
    trackData = await (await fetch(
      `/api/night/track?ra=${encodeURIComponent(ra)}&dec=${encodeURIComponent(dec)}`)).json();
    $("tl-target").textContent = name || "—";
    drawTimeline();
  } catch (e) { /* noop */ }
}

function drawTimeline() {
  const cv = $("timeline-canvas"); if (!cv || !timelineData) return;
  lastTimelineDraw = Date.now();
  const { ctx, w, h } = hidpi(cv);
  ctx.clearRect(0, 0, w, h);
  const td = timelineData;
  const t0 = td.start, t1 = td.end;
  const X = (t) => 40 + (t - t0) / (t1 - t0) * (w - 50);
  const Y = (a) => (h - 18) - ((a - (-30)) / 120) * (h - 30);

  // 하늘 밝기 밴드
  for (let i = 0; i < td.t.length - 1; i++) {
    const a = td.sun_alt[i];
    let col;
    if (a > 0) col = "#27415e";
    else if (a > -6) col = "#1d3148";
    else if (a > -12) col = "#152438";
    else if (a > -18) col = "#0e1a2c";
    else col = "#070e1b";
    ctx.fillStyle = col;
    ctx.fillRect(X(td.t[i]), 12, X(td.t[i + 1]) - X(td.t[i]) + 1, h - 30);
  }
  // 플랫 창
  (td.flat_windows || []).forEach((wd) => {
    ctx.fillStyle = "rgba(52,211,153,.20)";
    ctx.fillRect(X(wd.start), 12, X(wd.end) - X(wd.start), h - 30);
  });
  // 지평선 (alt 0)
  ctx.strokeStyle = "rgba(150,180,225,.25)"; ctx.setLineDash([3, 3]);
  ctx.beginPath(); ctx.moveTo(40, Y(0)); ctx.lineTo(w - 10, Y(0)); ctx.stroke();
  ctx.setLineDash([]);
  // 태양 곡선
  ctx.beginPath();
  td.t.forEach((t, i) => {
    const x = X(t), y = Y(td.sun_alt[i]);
    i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
  });
  ctx.strokeStyle = "#ffd884"; ctx.lineWidth = 1.4; ctx.stroke();
  // 대상 곡선
  if (trackData && trackData.t) {
    ctx.beginPath();
    trackData.t.forEach((t, i) => {
      const x = X(t), y = Y(trackData.alt[i]);
      i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
    });
    ctx.strokeStyle = "#4cc9f0"; ctx.lineWidth = 1.6; ctx.stroke();
  }
  // 현재 시각
  const nowT = Date.now() / 1000;
  if (nowT > t0 && nowT < t1) {
    ctx.strokeStyle = "#fb7185"; ctx.lineWidth = 1.2;
    ctx.beginPath(); ctx.moveTo(X(nowT), 10); ctx.lineTo(X(nowT), h - 16);
    ctx.stroke();
  }
  // 축 라벨
  ctx.fillStyle = "#4b5d7c"; ctx.font = "9px monospace";
  ctx.textAlign = "right";
  [-18, 0, 30, 60, 90].forEach((a) => ctx.fillText(a + "°", 36, Y(a) + 3));
  ctx.textAlign = "center";
  for (let t = Math.ceil(t0 / 10800) * 10800; t < t1; t += 10800) {
    const d = new Date(t * 1000);
    ctx.fillText(String(d.getHours()).padStart(2, "0") + "h", X(t), h - 4);
  }
}

// ---------- 시계열 플롯 빌더 ----------

const PALETTE = ["#4cc9f0", "#34d399", "#fbbf24", "#fb7185",
                 "#c084fc", "#f97316", "#a3e635", "#38bdf8"];
const CHARTS_KEY = "asterion.charts.v1";
const tele = { t: [], series: {} };  // 클라이언트 텔레메트리 스토어 (최근 1h)
let teleKeys = [];
let charts = [];                      // {id, keys, window}
let chartSeq = 0;

function teleAppend(ts, flat) {
  tele.t.push(ts);
  const known = new Set(Object.keys(tele.series));
  Object.keys(flat).forEach((k) => {
    if (!known.has(k)) tele.series[k] = new Array(tele.t.length - 1).fill(null);
  });
  Object.keys(tele.series).forEach((k) => {
    tele.series[k].push(flat[k] ?? null);
  });
  if (tele.t.length > 3700) {
    const cut = tele.t.length - 3700;
    tele.t.splice(0, cut);
    Object.values(tele.series).forEach((arr) => arr.splice(0, cut));
  }
}

async function initTelemetry() {
  try {
    teleKeys = await (await fetch("/api/telemetry/keys")).json();
    if (!teleKeys.length) { setTimeout(initTelemetry, 2500); return; }
    const hist = await (await fetch(
      `/api/telemetry/history?keys=${teleKeys.join(",")}&seconds=3600`)).json();
    tele.t = hist.t || [];
    tele.series = {};
    teleKeys.forEach((k) => { tele.series[k] = hist.series[k] || []; });
    renderPlotKeys();
    loadCharts();
  } catch (e) { setTimeout(initTelemetry, 4000); }
}

function renderPlotKeys() {
  $("plot-keys").innerHTML = teleKeys.map((k) =>
    `<span class="plot-key" data-key="${k}">${k}</span>`).join("");
  document.querySelectorAll(".plot-key").forEach((el) => {
    el.onclick = () => el.classList.toggle("sel");
  });
}

function selectedKeys() {
  return [...document.querySelectorAll(".plot-key.sel")].map((e) => e.dataset.key);
}

function saveCharts() {
  try {
    localStorage.setItem(CHARTS_KEY, JSON.stringify(
      charts.map(({ keys, window: win }) => ({ keys, window: win }))));
  } catch (e) { /* noop */ }
}
function loadCharts() {
  let saved = [];
  try { saved = JSON.parse(localStorage.getItem(CHARTS_KEY) || "[]"); }
  catch (e) { /* noop */ }
  saved.forEach((c) => addChart(c.keys, c.window, false));
}

function addChart(keys, windowS, persist = true) {
  if (!keys || !keys.length) return;
  const id = `chart-${++chartSeq}`;
  const tile = document.createElement("div");
  tile.className = "chart-tile";
  tile.id = id;
  tile.innerHTML =
    `<div class="ct-head"><span class="ct-title">${keys.join(" · ")}</span>` +
    `<button class="ct-x" title="삭제">✕</button></div>` +
    `<canvas></canvas><div class="ct-legend"></div>`;
  $("charts").appendChild(tile);
  const chart = { id, keys, window: windowS || 900, el: tile };
  charts.push(chart);
  tile.querySelector(".ct-x").onclick = () => {
    charts = charts.filter((c) => c.id !== id);
    tile.remove();
    saveCharts();
  };
  if (persist) saveCharts();
  drawChart(chart);
}

function drawChart(chart) {
  const cv = chart.el.querySelector("canvas");
  const { ctx, w, h } = hidpi(cv);
  ctx.clearRect(0, 0, w, h);
  const cutoff = Date.now() / 1000 - chart.window;
  let i0 = tele.t.findIndex((t) => t >= cutoff);
  if (i0 < 0) i0 = Math.max(0, tele.t.length - 2);
  const ts = tele.t.slice(i0);
  if (ts.length < 2) return;
  const t0 = ts[0], t1 = ts[ts.length - 1];
  const X = (t) => 4 + (t - t0) / Math.max(1, t1 - t0) * (w - 8);
  const legend = [];
  chart.keys.forEach((key, ki) => {
    const arr = (tele.series[key] || []).slice(i0);
    const vals = arr.filter((v) => v !== null && v !== undefined);
    if (!vals.length) { legend.push([key, "—", ki]); return; }
    const min = Math.min(...vals), max = Math.max(...vals);
    const span = (max - min) || 1;
    const Y = (v) => h - 4 - ((v - min) / span) * (h - 10);
    ctx.beginPath();
    let started = false;
    arr.forEach((v, i) => {
      if (v === null || v === undefined) { started = false; return; }
      const x = X(ts[i]), y = Y(v);
      started ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
      started = true;
    });
    ctx.strokeStyle = PALETTE[ki % PALETTE.length];
    ctx.lineWidth = 1.3;
    ctx.stroke();
    const last = vals[vals.length - 1];
    legend.push([key, `${Number(last).toLocaleString()} (${min.toFixed(1)}~${max.toFixed(1)})`, ki]);
  });
  chart.el.querySelector(".ct-legend").innerHTML = legend.map(([k, v, ki]) =>
    `<span><i style="background:${PALETTE[ki % PALETTE.length]}"></i>${k}: ${v}</span>`).join("");
}
function drawAllCharts() { charts.forEach(drawChart); }

// ---------- 패널 매니저 (크기/접기/드래그/타일) ----------

const LAYOUT_KEY = "asterion.layout.v1";
const SPANS = [3, 4, 6, 8, 12];

function panelId(card) { return card.dataset.panel; }
function getSpan(card) {
  const m = [...card.classList].find((c) => /^span\d+$/.test(c));
  return m ? Number(m.slice(4)) : 4;
}
function setSpan(card, n) {
  [...card.classList].filter((c) => /^span\d+$/.test(c))
    .forEach((c) => card.classList.remove(c));
  card.classList.add(`span${n}`);
}

function saveLayout() {
  const cards = [...document.querySelectorAll("#grid > section.card")];
  const layout = {
    order: cards.map(panelId),
    spans: Object.fromEntries(cards.map((c) => [panelId(c), getSpan(c)])),
    collapsed: Object.fromEntries(
      cards.map((c) => [panelId(c), c.classList.contains("collapsed")])),
  };
  try { localStorage.setItem(LAYOUT_KEY, JSON.stringify(layout)); }
  catch (e) { /* noop */ }
}

function applySavedLayout() {
  let layout = null;
  try { layout = JSON.parse(localStorage.getItem(LAYOUT_KEY) || "null"); }
  catch (e) { /* noop */ }
  if (!layout) return;
  const grid = $("grid");
  const byId = {};
  document.querySelectorAll("#grid > section.card").forEach((c) => {
    byId[panelId(c)] = c;
  });
  (layout.order || []).forEach((pid) => {
    if (byId[pid]) grid.appendChild(byId[pid]);
  });
  Object.entries(layout.spans || {}).forEach(([pid, n]) => {
    if (byId[pid] && SPANS.includes(n)) setSpan(byId[pid], n);
  });
  Object.entries(layout.collapsed || {}).forEach(([pid, col]) => {
    if (byId[pid]) byId[pid].classList.toggle("collapsed", !!col);
  });
}

let draggingCard = null;

function initPanelManager() {
  document.querySelectorAll("#grid > section.card").forEach((card) => {
    const head = card.querySelector(".card-head");
    const tools = document.createElement("div");
    tools.className = "panel-tools";
    tools.innerHTML =
      `<button class="pt-btn pt-size" title="크기 변경">⤢</button>` +
      `<button class="pt-btn pt-collapse" title="접기/펼치기">▾</button>`;
    head.appendChild(tools);
    tools.querySelector(".pt-size").onclick = (e) => {
      e.stopPropagation();
      const cur = getSpan(card);
      const next = SPANS[(SPANS.indexOf(cur) + 1) % SPANS.length];
      setSpan(card, next);
      saveLayout();
      if (lastStatus) applyStatus(lastStatus);
      drawTimeline(); drawAllCharts();
    };
    tools.querySelector(".pt-collapse").onclick = (e) => {
      e.stopPropagation();
      card.classList.toggle("collapsed");
      saveLayout();
    };
    head.setAttribute("draggable", "true");
    head.addEventListener("dragstart", () => {
      draggingCard = card;
      card.classList.add("dragging");
    });
    head.addEventListener("dragend", () => {
      card.classList.remove("dragging");
      draggingCard = null;
      saveLayout();
      if (lastStatus) applyStatus(lastStatus);
      drawTimeline(); drawAllCharts();
    });
    card.addEventListener("dragover", (e) => {
      if (!draggingCard || draggingCard === card) return;
      e.preventDefault();
      const rect = card.getBoundingClientRect();
      const before = (e.clientY - rect.top) < rect.height / 2;
      card.parentNode.insertBefore(
        draggingCard, before ? card : card.nextSibling);
    });
  });
  applySavedLayout();
}

// ---------- 상태 반영 ----------

let lastStatus = null;
let filterOptionsReady = false;

function applyStatus(s) {
  lastStatus = s;

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

  // 카메라 + 캡처
  const c = s.camera || {};
  $("c-temp").textContent = fmt(c.ccd_temp, 1, " ℃") +
    (c.cooler_on ? " ❄" : "");
  $("c-state").textContent = c.state || "—";
  const f = s.filter || {};
  $("c-filter").textContent = f.name || "—";
  if (!filterOptionsReady && Array.isArray(f.names) && f.names.length) {
    $("sel-filter").innerHTML = f.names.map((n, i) =>
      `<option value="${i}">${n}</option>`).join("");
    filterOptionsReady = true;
  }
  const cap = s.capture || {};
  const capCount = cap.count ? `/${cap.count}` : "";
  $("cap-state").textContent = cap.active
    ? `${cap.state} (#${cap.seq}${capCount})` : "idle";
  $("cap-median").textContent = cap.last_median
    ? Number(cap.last_median).toLocaleString() : "—";
  $("cap-file").textContent = cap.last_file || "—";
  $("btn-cap-once").disabled = !!cap.active;
  $("btn-cap-loop").disabled = !!cap.active;
  $("btn-cap-stop").disabled = !cap.active;
  $("btn-autosave").classList.toggle("active", !!cap.autosave);

  // 포커서
  const fo = s.focuser || {};
  $("f-pos").textContent = fo.position !== null && fo.position !== undefined
    ? Number(fo.position).toLocaleString() : "—";
  $("f-moving").textContent = fo.moving ? "YES" : "no";
  $("f-temp").textContent = fmt(fo.temperature, 1, " ℃");
  if (fo.position !== null && fo.position !== undefined && fo.max_position) {
    $("f-bar").style.width =
      clamp(fo.position / fo.max_position * 100, 0, 100) + "%";
  }

  // 기상 + 시스템
  const w = s.weather || {};
  $("w-temp").textContent = fmt(w.temp, 1, "℃");
  $("w-hum").textContent = fmt(w.humidity, 0, "%");
  $("w-wind").textContent = fmt(w.wind, 1, " m/s");
  $("w-dew").textContent = fmt(w.dew_point, 1, " ℃");
  pushSpark("temp", w.temp); pushSpark("hum", w.humidity);
  pushSpark("wind", w.wind);
  drawSpark("spark-temp", sparkBuf.temp, "#38bdf8");
  drawSpark("spark-hum", sparkBuf.hum, "#34d399");
  drawSpark("spark-wind", sparkBuf.wind, "#fbbf24");

  $("sys-safety-state").textContent = saf.state || "—";
  $("sys-safety-state").className = `safety-state s-${saf.state}`;
  $("sys-safety-reason").textContent = (saf.reasons || []).join(", ");
  const af = s.autoflat || {};
  setSysRow("wt", "run", saf.state || "감시 중");
  setSysRow("sf", af.running ? "run" : "idle",
            af.running ? (af.phase || "running") : "idle");
  setSysRow("cp", cap.active ? "run" : "idle",
            cap.active ? (cap.state || "running") : "idle");

  // 하늘
  const sun = s.sun || {}, tw = s.twilight_sim || {};
  $("s-sunalt").textContent = fmt(sun.alt, 1, "°");
  $("s-phase").textContent = (sun.phase_label || "—") +
    (tw.enabled ? " · 황혼시뮬" : "");
  $("s-antisolar").textContent = fmt(sun.antisolar_az, 0, "°");
  $("btn-twilight").classList.toggle("active", !!tw.enabled);
  $("twilight-row").style.display =
    (s.mode === "sim" || tw.enabled) ? "" : "none";
  drawSky(s);
  kickSky();  // 위치가 바뀌었으면 마커가 부드럽게 따라가도록 애니메이션 재개

  // 오토플랫
  const [aduMin, aduMax] = aduRange();
  $("af-filter").textContent = af.filter || "—";
  $("af-frame").textContent = af.frame ? `${af.frame} / ${af.total}` : "—";
  $("af-exp").textContent = af.exposure
    ? `${Number(af.exposure).toFixed(2)}s` : "—";
  $("af-adu").textContent = af.last_adu
    ? Number(af.last_adu).toLocaleString() : "—";
  $("af-phase").textContent = af.running ? (af.phase || "...") : "대기 중 (idle)";
  $("btn-af-start").disabled = !!af.running;
  $("btn-af-stop").disabled = !af.running;
  drawGauge(af, aduMin, aduMax);
  const filtCount = $("af-filters").value.split(",")
    .map((x) => x.trim()).filter(Boolean).length || 1;
  const compF = Object.keys(af.results || {}).length;
  let pct = 0;
  if (af.running && af.total) {
    pct = clamp((compF * af.total + (af.frame || 0)) /
                (filtCount * af.total) * 100, 0, 99);
  } else if (!af.running && compF) pct = 100;
  $("af-bar").style.width = pct + "%";

  // 텔레메트리 append + 차트
  if (s.telemetry_last) {
    teleAppend(Date.now() / 1000, s.telemetry_last);
    drawAllCharts();
  }
  // 타임라인 now선 갱신 (30초 간격)
  if (Date.now() - lastTimelineDraw > 30000) drawTimeline();

  if ($("dev-drawer").classList.contains("open")) renderDev(s);
  updateModeSeg(s.mode);
}

function setSysRow(key, state, text) {
  const dot = $(`sys-${key}-dot`);
  if (dot) dot.className = "sys-dot" +
    (state === "idle" ? " idle" : state === "run" ? " run" : "");
  const st = $(`sys-${key}-state`);
  if (st) st.textContent = text;
}

// ---------- 설정 드로어 ----------

function updateModeSeg(mode) {
  const active = (mode === "sim") ? "sim" : "real";
  document.querySelectorAll("#mode-seg .seg-btn").forEach((b) =>
    b.classList.toggle("active", b.dataset.mode === active));
}

function renderDev(s) {
  $("set-site").textContent = s.site || "—";
  $("set-mode").textContent = (s.mode || "—").toUpperCase();
  $("set-lst").textContent = s.time?.lst || "—";
  const sun = s.sun || {}, tw = s.twilight_sim || {};
  $("set-phase").textContent = (sun.phase_label || "—") +
    (tw.enabled ? " · 황혼시뮬" : "");
  const rows = [
    ["마운트", s.mount?.connected, s.mount?.detail || ""],
    ["카메라", s.camera?.connected, s.camera?.detail || ""],
    ["필터휠", s.filter?.connected,
     s.filter?.name ? `pos ${s.filter.position} · ${s.filter.name}` : ""],
    ["포커서", s.focuser?.connected, s.focuser?.detail || ""],
    ["기상", s.weather?.temp != null, "SIM"],
  ];
  $("dev-devices").innerHTML = rows.map(([nm, on, dl]) =>
    `<div class="dev-dev"><div class="nm"><span class="cd ${on ? "on" : "off"}"></span>${nm}</div>` +
    `<span class="dl">${escapeHtml(dl)}</span></div>`).join("");
}

const DEVMODE_KEY = "asterion.devmode";
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
  ws.onclose = () => {
    $("ws-dot").classList.remove("on");
    setTimeout(connectWS, 2000);
  };
  ws.onmessage = (ev) => {
    const data = JSON.parse(ev.data);
    if (data.type === "status") applyStatus(data.status);
    else if (data.type === "log") logLine(data);
    else if (data.type === "frame") prependRow("tbl-frames", frameRow(data.frame));
    else if (data.type === "action") prependRow("tbl-actions", actionRow(data.action));
    else if (data.type === "preview") updatePreview(data.token, data.meta);
  };
}

// ---------- 프레임 미리보기 ----------

function updatePreview(token, meta) {
  const img = $("frame-img");
  img.onload = () => { img.classList.add("on"); $("frame-empty").style.display = "none"; };
  img.src = `/api/preview.png?t=${token}`;
  const m = meta || {};
  const med = (m.median ?? null) !== null ? Number(m.median).toLocaleString() : "?";
  $("frame-meta").textContent =
    `${m.type || ""} ${m.filter || ""} · ${m.exposure_s ?? "?"}s · median ${med}` +
    (m.file ? ` · ${m.file}` : "");
}

async function initPreview() {
  try {
    const d = await (await fetch("/api/preview/meta")).json();
    if (d.token > 0) updatePreview(d.token, d.meta);
  } catch (e) { /* 아직 프레임 없음 */ }
}

// ---------- 부드러운 돔 애니메이션 (움직일 때만 rAF, 정지하면 idle) ----------

let skyRaf = 0;
function skyNeedsAnim() {
  if (!lastStatus) return false;
  const m = lastStatus.mount || {};
  if (m.slewing || skyTarget) return true;
  if (mountDraw && m.alt != null && m.az != null) {
    const daz = Math.abs(((m.az - mountDraw.az + 540) % 360) - 180);
    if (Math.abs(m.alt - mountDraw.alt) > 0.05 || daz > 0.05) return true;
  }
  return false;
}
function skyLoop() {
  if (lastStatus) drawSky(lastStatus);
  skyRaf = skyNeedsAnim() ? requestAnimationFrame(skyLoop) : 0;
}
function kickSky() { if (!skyRaf) skyRaf = requestAnimationFrame(skyLoop); }

// ---------- 초기 로드 ----------

async function init() {
  initPanelManager();
  try {
    const [status, logs, frames, actions] = await Promise.all([
      fetch("/api/status").then((r) => r.json()),
      fetch("/api/logs").then((r) => r.json()),
      fetch("/api/frames").then((r) => r.json()),
      fetch("/api/actionlog").then((r) => r.json()),
    ]);
    const dAf = status.defaults?.autoflat;
    if (dAf) {
      if (dAf.filters) $("af-filters").value = dAf.filters.join(",");
      if (dAf.frames_per_filter) $("af-frames").value = dAf.frames_per_filter;
      if (dAf.adu_min) $("af-adumin").value = dAf.adu_min;
      if (dAf.adu_max) $("af-adumax").value = dAf.adu_max;
      if (dAf.dither_arcsec) $("af-dither").value = dAf.dither_arcsec;
      if (dAf.settle_seconds !== undefined) $("af-settle").value = dAf.settle_seconds;
      if (dAf.initial_exposure) $("af-initexp").value = dAf.initial_exposure;
      if (dAf.max_exposure) $("af-maxexp").value = dAf.max_exposure;
    }
    const dCap = status.defaults?.capture;
    if (dCap) {
      if (dCap.default_exposure) $("cap-exp").value = dCap.default_exposure;
      if (dCap.default_interval) $("cap-interval").value = dCap.default_interval;
    }
    if (status.mode && status.mode !== "starting") applyStatus(status);
    logs.forEach(logLine);
    frames.reverse().forEach((fr) => prependRow("tbl-frames", frameRow(fr)));
    actions.reverse().forEach((a) => prependRow("tbl-actions", actionRow(a)));
  } catch (e) {
    logLine({ ts: nowts(), source: "ui", level: "error",
              msg: `초기 로드 실패: ${e}` });
  }
  connectWS();
  fetchTimeline();
  setInterval(fetchTimeline, 600000);  // 10분마다 갱신
  initTelemetry();
  initPreview();
  kickSky();                            // 돔 애니메이션 (필요할 때만)
}

// ---------- 버튼 핸들러 ----------

// 망원경
$("btn-goto").onclick = () => {
  const alt = Number($("in-alt").value), az = Number($("in-az").value);
  if (Number.isNaN(alt) || $("in-alt").value === "") return;
  skyTarget = { alt, az: az % 360, ts: Date.now() };
  kickSky();
  post("/api/actions/mount/goto", { alt, az }).catch(() => { skyTarget = null; });
};
$("btn-goto-radec").onclick = () => {
  const ra = $("in-ra").value.trim(), dec = $("in-dec").value.trim();
  if (!ra || !dec) return;
  post("/api/actions/mount/goto_radec", { ra, dec });
};
$("btn-resolve").onclick = async () => {
  const name = $("in-target").value.trim();
  if (!name) return;
  const line = $("resolve-line");
  line.style.color = "";
  line.textContent = "검색 중…";
  try {
    const r = await fetch(`/api/resolve?name=${encodeURIComponent(name)}`);
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
    const d = await r.json();
    $("in-ra").value = d.ra_str;
    $("in-dec").value = d.dec_str;
    line.textContent = `${name} → ${d.ra_str} ${d.dec_str}`;
    fetchTrack(d.ra_str, d.dec_str, name);  // 타임라인에 고도 곡선
  } catch (e) {
    line.style.color = "var(--err)";
    line.textContent = `해석 실패: ${e.message}`;
  }
};
$("in-target").addEventListener("keydown", (e) => {
  if (e.key === "Enter") $("btn-resolve").click();
});
document.querySelectorAll(".jog-pad [data-jog]").forEach((b) => {
  b.onclick = () => post("/api/actions/mount/jog", {
    direction: b.dataset.jog,
    arcsec: Number($("jog-step").value),
  });
});
$("btn-tracking").onclick = () =>
  post("/api/actions/mount/tracking", { on: !(lastStatus?.mount?.tracking) });
$("btn-stop").onclick = () => { skyTarget = null; post("/api/actions/mount/stop"); };

// 하늘 돔 클릭
$("sky-canvas").addEventListener("click", skyClickHandler);

// 카메라 / 캡처
$("btn-filter").onclick = () =>
  post("/api/actions/filter", { position: Number($("sel-filter").value) });
$("btn-cooler").onclick = () => {
  const sp = $("c-setpoint").value;
  post("/api/actions/camera/cooler", {
    on: !(lastStatus?.camera?.cooler_on),
    setpoint: sp === "" ? null : Number(sp),
  });
};
function captureBody(count) {
  return {
    exposure_s: Number($("cap-exp").value),
    frame_type: $("cap-type").value,
    count,
    interval_s: Number($("cap-interval").value),
  };
}
$("btn-cap-once").onclick = () =>
  post("/api/actions/camera/capture", captureBody(1));
$("btn-cap-loop").onclick = () =>
  post("/api/actions/camera/capture", captureBody(Number($("cap-count").value)));
$("btn-cap-stop").onclick = () => post("/api/actions/camera/capture/stop");
$("btn-autosave").onclick = () =>
  post("/api/actions/camera/autosave", { on: !(lastStatus?.capture?.autosave) });

// 포커서
$("btn-f-go").onclick = () => {
  const p = Number($("f-target").value);
  if (Number.isNaN(p) || $("f-target").value === "") return;
  post("/api/actions/focuser/move", { position: p });
};
document.querySelectorAll(".f-nudge [data-fn]").forEach((b) => {
  b.onclick = () => post("/api/actions/focuser/nudge",
                         { delta: Number(b.dataset.fn) });
});

// 오토플랫
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

// 황혼 시뮬
const toggleTwilight = () =>
  post("/api/sim/twilight", { enabled: !(lastStatus?.twilight_sim?.enabled) });
$("btn-twilight").onclick = toggleTwilight;
$("btn-twilight2").onclick = toggleTwilight;

// 플롯 빌더
$("btn-plot-add").onclick = () => {
  const keys = selectedKeys();
  if (!keys.length) {
    logLine({ ts: nowts(), source: "ui", level: "warn",
              msg: "시리즈를 먼저 선택하세요 (키 클릭)" });
    return;
  }
  addChart(keys, Number($("plot-window").value));
  document.querySelectorAll(".plot-key.sel")
    .forEach((el) => el.classList.remove("sel"));
};

// 설정 드로어
$("dev-btn").onclick = () => openDrawer(true);
$("dev-close").onclick = () => openDrawer(false);
$("dev-overlay").onclick = () => openDrawer(false);
applyDevMode(localStorage.getItem(DEVMODE_KEY) === "1");
$("devmode-toggle").onchange = (e) => applyDevMode(e.target.checked);
$("btn-layout-reset").onclick = () => {
  try { localStorage.removeItem(LAYOUT_KEY); } catch (e) { /* noop */ }
  location.reload();
};
document.querySelectorAll("#mode-seg .seg-btn").forEach((b) => {
  b.onclick = async () => {
    if (b.classList.contains("active")) return;
    try {
      const r = await post("/api/dev/mode", { mode: b.dataset.mode });
      logLine({ ts: nowts(), source: "dev", level: "info",
                msg: `드라이버 모드 → ${(r.mode || b.dataset.mode).toUpperCase()}` });
    } catch (e) { /* post()가 이미 로그 */ }
  };
});

// 리사이즈 → 캔버스 재그리기
let resizeTimer = null;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    if (lastStatus) applyStatus(lastStatus);
    drawTimeline();
    drawAllCharts();
  }, 150);
});

init();
