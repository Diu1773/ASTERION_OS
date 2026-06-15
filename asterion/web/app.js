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
  const peek = document.getElementById("log-dock-peek");   // 접힘 상태 미리보기(최신 1줄)
  if (peek) peek.textContent = `${e.ts} [${e.source}] ${e.msg}`;
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

function drawSky(s) {
  const cv = $("sky-canvas"); if (!cv) return;
  const { ctx, w, h } = hidpi(cv);
  ctx.clearRect(0, 0, w, h);
  const cx = w / 2, cy = h / 2, R = Math.min(w, h) / 2 - 12;
  if (R <= 0) return;   // 숨겨진 탭이면 캔버스 크기 0 → 반지름 음수 (그리기 생략)
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

  const hasMount = m.alt != null && m.az != null &&
                   !Number.isNaN(m.alt) && !Number.isNaN(m.az);
  const md = hasMount ? { alt: m.alt, az: m.az } : null;  // 라이브 위치(실시간, 보간 없음)

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
  if (R <= 0) return;   // 숨겨진 탭이면 캔버스 크기 0 → 반지름 음수 (그리기 생략)
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
    if (grids.analysis) grids.analysis.refreshItems().layout(true);  // 높이 줄어듦 반영
  };
  if (persist) saveCharts();
  drawChart(chart);
  // 차트가 추가돼 플롯 패널이 커지면 즉시 다시 팩킹 (아래 패널과 겹침 방지)
  if (grids.analysis) grids.analysis.refreshItems().layout(true);
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

// ---------- 워크스페이스 탭 + Muuri 패널 매니저 (갭 없는 팩킹) ----------

const WIDTHS = ["w3", "w4", "w6", "w8", "w12"];
const TABS = ["control", "env", "plan", "analysis", "system"];
const ACTIVE_TAB_KEY = "asterion.activetab";
const grids = {};   // tab -> Muuri 인스턴스 (탭별 독립 그리드)

function currentTab() {
  const b = document.querySelector(".tab.active");
  return b ? b.dataset.tab : "control";
}
function layoutKey(tab) { return `asterion.layout.${tab}.v4`; }
function widthClass(item) {
  return WIDTHS.find((w) => item.classList.contains(w)) || "w4";
}
function setWidth(item, w) {
  WIDTHS.forEach((c) => item.classList.remove(c));
  item.classList.add(w);
  item.style.width = "";   // 인라인 % 폭(나눔선 조절) 해제 → 클래스 폭으로 복귀
}

function saveLayout(tab) {
  const grid = grids[tab];
  if (!grid) return;
  const els = grid.getItems().map((it) => it.getElement());
  const layout = {
    order: els.map((el) => el.dataset.panel),
    widths: Object.fromEntries(els.map((el) => [el.dataset.panel, widthClass(el)])),
    collapsed: Object.fromEntries(els.map((el) =>
      [el.dataset.panel, el.querySelector(".card").classList.contains("collapsed")])),
    pinned: Object.fromEntries(els.map((el) =>
      [el.dataset.panel, el.classList.contains("pinned")])),
  };
  try { localStorage.setItem(layoutKey(tab), JSON.stringify(layout)); }
  catch (e) { /* noop */ }
}

function applySavedLayout(tab, grid) {
  let layout = null;
  try { layout = JSON.parse(localStorage.getItem(layoutKey(tab)) || "null"); }
  catch (e) { /* noop */ }
  if (!layout) return;
  grid.getItems().forEach((it) => {
    const el = it.getElement(), pid = el.dataset.panel;
    if (layout.widths && layout.widths[pid]) setWidth(el, layout.widths[pid]);
    if (layout.collapsed && layout.collapsed[pid])
      el.querySelector(".card").classList.add("collapsed");
    if (layout.pinned && layout.pinned[pid]) el.classList.add("pinned");
  });
  if (layout.order) {
    const idx = {};
    layout.order.forEach((p, i) => { idx[p] = i; });
    grid.sort((a, b) => (idx[a.getElement().dataset.panel] ?? 99) -
                        (idx[b.getElement().dataset.panel] ?? 99));
  }
}

// 레이아웃이 바뀌면 캔버스 크기도 달라지므로 다시 그린다 (팩킹은 ensureGrid/RO가 담당)
function relayoutAfter(tab) {
  requestAnimationFrame(() => {
    if (lastStatus) applyStatus(lastStatus);
    drawTimeline(); drawAllCharts();
  });
}

// 카드 '크게 보기' — 그 카드만 큰 오버레이로 띄운다(격자는 안 건드림). 다시 누르거나
// 배경 클릭으로 닫힘. 캔버스(돔·게이지·차트)는 새 크기로 다시 그린다.
function toggleMaximize(item) {
  const on = item.classList.toggle("maximized");
  let bd = document.querySelector(".max-backdrop");
  if (on) {
    if (!bd) { bd = document.createElement("div"); bd.className = "max-backdrop"; document.body.appendChild(bd); }
    bd.onclick = () => toggleMaximize(item);
    bd.style.display = "block";
  } else if (bd) {
    bd.style.display = "none";
  }
  setTimeout(() => { if (lastStatus) applyStatus(lastStatus); drawTimeline(); drawAllCharts(); }, 30);
}

function addPanelTools(item, tab) {
  const card = item.querySelector(".card");
  const head = card.querySelector(".card-head");
  if (head.querySelector(".panel-tools")) return;
  // 좌상단 고정핀 — 켜면 드래그 잠금 (dragStartPredicate가 .pinned을 본다)
  const pin = document.createElement("button");
  pin.className = "pt-btn pt-pin" + (item.classList.contains("pinned") ? " on" : "");
  pin.title = "고정 (드래그 잠금)";
  pin.innerHTML = `<svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 17v5"/><path d="M9 10.8V4h6v6.8l2 3.2H7l2-3.2Z"/></svg>`;
  head.insertBefore(pin, head.firstChild);
  pin.onclick = (e) => {
    e.stopPropagation();
    pin.classList.toggle("on", item.classList.toggle("pinned"));
    saveLayout(tab);
  };
  const tools = document.createElement("div");
  tools.className = "panel-tools";
  tools.innerHTML =
    `<button class="pt-btn pt-max" title="크게 보기">⛶</button>` +
    `<button class="pt-btn pt-size" title="전체폭(모든 열) 토글">⤢</button>` +
    `<button class="pt-btn pt-collapse" title="접기/펼치기">▾</button>`;
  head.appendChild(tools);
  tools.querySelector(".pt-max").onclick = (e) => { e.stopPropagation(); toggleMaximize(item); };
  tools.querySelector(".pt-size").onclick = (e) => {
    e.stopPropagation();
    setWidth(item, item.classList.contains("w12") ? "w6" : "w12");   // 전체폭 토글
    grids[tab].refreshItems().layout();
    saveLayout(tab);
    positionDividers(tab);
    relayoutAfter(tab);
  };
  tools.querySelector(".pt-collapse").onclick = (e) => {
    e.stopPropagation();
    card.classList.toggle("collapsed");
    grids[tab].refreshItems([item]).layout(true);
    saveLayout(tab);
  };
}

// 행(row) 기준 격자 + 같은 행 높이 맞춤. Muuri 기본 masonry는 높이로 패킹해
// 같은 줄 카드 높이가 다르면 계단처럼 어긋나고, 행을 맞춰도 짧은 카드 밑에
// 빈공간이 남는다. 이 레이아웃은 (1) 강제높이를 풀어 자연 높이를 재고 폭(wN)으로
// 행을 나눈 뒤 (2) 각 행 카드를 그 행 최대 높이로 늘려(.muuri-item-content
// height:100%) 빈공간을 없앤다. 위치/높이만 다루므로 Muuri의 드래그·재배치
// 애니메이션은 그대로 유지된다.
// ===== 컬럼 masonry 레이아웃 =====
// 카드를 N개 열에 순서대로 분배. 각 카드는 제 높이 그대로라 빈칸이 없다.
// 전체폭(w12) 카드는 모든 열을 가로지른다. 열 폭은 colfr(비율)로 조절. 카드 높이는
// 항상 내용 높이(자동) — 빈칸/잘림 없음. 크게 보려면 그 카드 열을 넓히면 된다.
const colsKey  = (t) => `asterion.cols.${t}.v2`;
const colfrKey = (t) => `asterion.colfr.${t}.v2`;
const _rd = (k, d) => { try { const v = JSON.parse(localStorage.getItem(k)); return v == null ? d : v; } catch (e) { return d; } };
const _wr = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* noop */ } };
function colCount(tab) { return Math.max(1, Math.round(_rd(colsKey(tab), 2) || 2)); }
function saveCols(tab, n) { _wr(colsKey(tab), n); }
function normFr(arr, N) {
  if (!Array.isArray(arr) || arr.length !== N) return new Array(N).fill(1 / N);
  const s = arr.reduce((a, b) => a + (+b || 0), 0) || 1;
  return arr.map((x) => (+x || 0) / s);
}
function loadColFr(tab, N) { return normFr(_rd(colfrKey(tab), null), N); }
function saveColFr(tab, fr) { _wr(colfrKey(tab), fr); }

function colGridLayout(grid, layoutId, items, gridWidth, gridHeight, callback) {
  const n = items.length;
  const els = items.map((it) => it.getElement());
  if (gridWidth < 1) {                        // 숨겨진 탭 — 0px 찌부러짐 방지
    els.forEach((el) => { el.style.height = ""; });
    callback({ id: layoutId, items, slots: new Array(n * 2).fill(0), styles: {} });
    return;
  }
  const tab = grid._tab || "";
  const N = colCount(tab);
  const fr = grid._frLive || loadColFr(tab, N);
  const colX = [], colW = []; let ax = 0;
  for (let c = 0; c < N; c++) { colX[c] = ax; colW[c] = fr[c] * gridWidth; ax += colW[c]; }
  // 1) 행 배정(가로 정렬): 카드를 왼→오로 채우고 N개 차면 다음 행. 전체폭(w12)은 한 행 독차지.
  const rowOf = new Array(n), colOf = new Array(n), isFull = new Array(n);
  let row = 0, col = 0;
  for (let i = 0; i < n; i++) {
    const el = els[i], full = el.classList.contains("w12");
    if (full) {
      if (col > 0) { row++; col = 0; }       // 진행 중인 행 닫기
      isFull[i] = true; rowOf[i] = row; colOf[i] = 0;
      el.style.width = gridWidth + "px";
      row++; col = 0;                          // 전체폭 다음은 새 행
    } else {
      isFull[i] = false; rowOf[i] = row; colOf[i] = col;
      el.style.width = colW[col] + "px";
      col++; if (col >= N) { row++; col = 0; }
    }
    el.style.height = "";
  }
  // 2) 행별 최대 높이로 같은 행 카드를 정렬(대칭) → 가로줄이 맞는다
  const natH = els.map((el) => el.getBoundingClientRect().height);
  const rowMax = [];
  for (let i = 0; i < n; i++) rowMax[rowOf[i]] = Math.max(rowMax[rowOf[i]] || 0, natH[i]);
  const rowY = []; let acc = 0;
  for (let r = 0; r < rowMax.length; r++) { rowY[r] = acc; acc += (rowMax[r] || 0); }
  const slots = [];
  for (let i = 0; i < n; i++) {
    els[i].style.height = rowMax[rowOf[i]] + "px";   // 같은 행 = 같은 높이
    slots.push(isFull[i] ? 0 : colX[colOf[i]], rowY[rowOf[i]]);
  }
  grid._cols = N; grid._colX = colX; grid._colW = colW; grid._fr = fr;
  callback({ id: layoutId, items, slots, styles: { height: acc + "px" } });
}

// ---- 탭별 격자 열 수 (관제 2열 · 기상 3열 등) — 탭 상단 컨트롤 ----
function setCols(tab, n) {
  saveCols(tab, n);
  saveColFr(tab, new Array(n).fill(1 / n));   // 균등 폭으로 리셋
  const grid = grids[tab]; if (!grid) return;
  grid._frLive = null;
  grid.refreshItems().layout(true);
  syncColsToolbar(tab); positionDividers(tab); relayoutAfter(tab);
}
function syncColsToolbar(tab) {
  const pane = document.querySelector(`.tab-pane[data-pane="${tab}"]`);
  const bar = pane && pane.querySelector(".grid-toolbar");
  if (!bar) return;
  const N = colCount(tab);
  bar.querySelectorAll(".gt-btn").forEach((b) => b.classList.toggle("active", Number(b.dataset.cols) === N));
}
function injectGridToolbar(tab) {
  const pane = document.querySelector(`.tab-pane[data-pane="${tab}"]`);
  const gridEl = document.getElementById(`grid-${tab}`);
  if (!pane || !gridEl || pane.querySelector(".grid-toolbar")) return;
  const bar = document.createElement("div");
  bar.className = "grid-toolbar";
  bar.innerHTML = `<span class="gt-label">격자 열</span><div class="gt-cols">` +
    [1, 2, 3, 4].map((n) => `<button class="gt-btn" data-cols="${n}">${n}</button>`).join("") +
    `</div><button class="gt-tile" title="수동 폭·높이 리사이즈를 해제하고 깔끔히 정렬">⊞ 정렬</button>`;
  pane.insertBefore(bar, gridEl);
  bar.querySelectorAll(".gt-btn").forEach((b) => (b.onclick = () => setCols(tab, Number(b.dataset.cols))));
  bar.querySelector(".gt-tile").onclick = () => tileGrid(tab);
  syncColsToolbar(tab);
}
function tileGrid(tab) {                       // 열 폭을 균등으로 되돌려 깔끔히 정렬
  const grid = grids[tab]; if (!grid) return;
  const N = colCount(tab);
  saveColFr(tab, new Array(N).fill(1 / N));
  grid._frLive = null;
  grid.getItems().forEach((it) => { const el = it.getElement(); el.style.height = ""; });
  grid.refreshItems().layout(true);
  syncColsToolbar(tab); positionDividers(tab); relayoutAfter(tab);
}

const SNAP_PX = 7;                             // 스냅 거리
function showGuide(tab, vertical, pos, len, off) {   // PPT식 정렬 가이드선
  const cont = document.getElementById(`grid-${tab}`);
  let g = cont.querySelector(":scope > .snap-guide");
  if (!g) { g = document.createElement("div"); g.className = "snap-guide"; cont.appendChild(g); }
  g.classList.toggle("horizontal", !vertical);
  if (vertical) { g.style.left = pos + "px"; g.style.top = "0px"; g.style.height = len + "px"; g.style.width = ""; }
  else { g.style.top = pos + "px"; g.style.left = (off || 0) + "px"; g.style.width = len + "px"; g.style.height = ""; }
  g.style.display = "block";
}
function hideGuide(tab) {
  const cont = document.getElementById(`grid-${tab}`);
  const g = cont && cont.querySelector(":scope > .snap-guide");
  if (g) g.style.display = "none";
}

function positionDividers(tab) {               // 세로선(열 폭)만 — 높이 리사이즈는 없앰
  const grid = grids[tab]; if (!grid) return;
  const cont = document.getElementById(`grid-${tab}`);
  const cr = cont.getBoundingClientRect();
  if (cr.width === 0) return;                  // 숨겨진 탭
  let layer = cont.querySelector(":scope > .divider-layer");
  if (!layer) { layer = document.createElement("div"); layer.className = "divider-layer"; cont.appendChild(layer); }
  const N = grid._cols || 1;
  const totalH = parseFloat(cont.style.height) || cr.height;
  const colX = grid._colX || [], colW = grid._colW || [];
  const specs = [];
  for (let c = 0; c < N - 1; c++)              // 세로선: 열 사이 경계, 전체 높이
    specs.push({ left: colX[c] + colW[c], c });
  const pool = [...layer.querySelectorAll(".divider")];   // DOM 재사용 → 깜빡임 X
  while (pool.length < specs.length) { const d = document.createElement("div"); d.className = "divider"; layer.appendChild(d); pool.push(d); }
  while (pool.length > specs.length) { layer.removeChild(pool.pop()); }
  specs.forEach((s, i) => {
    const d = pool[i];
    d.className = "divider col-divider"; d.title = "드래그해 열 폭 조절";
    d.style.left = s.left + "px"; d.style.top = "0px"; d.style.height = totalH + "px"; d.style.width = "";
    d.onmousedown = (e) => startColDrag(tab, s.c, e);
  });
}

// 열 폭 리사이즈 — 인접 두 열 비율(fr) 재분배 (합 보존)
function startColDrag(tab, c, e) {
  e.preventDefault(); e.stopPropagation();
  const grid = grids[tab]; if (!grid) return;
  const cont = document.getElementById(`grid-${tab}`);
  const cw = cont.getBoundingClientRect().width;
  const fr = (grid._fr || []).slice();
  const lFr0 = fr[c], rFr0 = fr[c + 1], sum = lFr0 + rFr0;
  const pairLeftX = (grid._colX || [])[c] || 0, minFr = 0.12, startX = e.clientX;
  document.body.style.cursor = "col-resize";
  const apply = (ev) => {
    let lFr = Math.max(minFr, Math.min(sum - minFr, lFr0 + (ev.clientX - startX) / cw));
    let snap = null;
    for (const t of [lFr0, sum / 2]) if (Math.abs(lFr - t) <= SNAP_PX / cw) { lFr = t; snap = pairLeftX + t * cw; break; }
    fr[c] = lFr; fr[c + 1] = sum - lFr;
    grid._frLive = fr.slice();
    grid.layout(true);
    if (snap != null) showGuide(tab, true, Math.round(snap), cont.getBoundingClientRect().height); else hideGuide(tab);
  };
  const onMove = apply;
  const onUp = () => {
    document.removeEventListener("mousemove", onMove);
    document.removeEventListener("mouseup", onUp);
    document.body.style.cursor = ""; hideGuide(tab);
    grid._frLive = null; saveColFr(tab, fr);
    grid.refreshItems().layout(true); positionDividers(tab);
  };
  document.addEventListener("mousemove", onMove);
  document.addEventListener("mouseup", onUp);
}

function ensureGrid(tab) {
  if (grids[tab]) { grids[tab].refreshItems().layout(true); return grids[tab]; }
  const el = document.getElementById(`grid-${tab}`);
  if (!el || typeof Muuri === "undefined") return null;
  const grid = new Muuri(el, {
    items: ".muuri-item",
    dragEnabled: true,
    dragHandle: ".card-head",
    dragContainer: document.body,
    layout: colGridLayout,   // 컬럼 masonry (각 열 독립 높이, 빈칸 0)
    layoutDuration: 300,
    layoutEasing: "cubic-bezier(.2,.8,.2,1)",
    dragStartPredicate: (item, e) => {
      if (item.getElement().classList.contains("pinned")) return false;  // 고정핀
      return Muuri.ItemDrag.defaultStartPredicate(item, e, { distance: 6 });
    },
    dragSortPredicate: { threshold: 40, action: "move" },  // 큰 패널도 잘 재배치
    dragRelease: { duration: 300, easing: "cubic-bezier(.2,.8,.2,1)" },
  });
  grids[tab] = grid;
  grid._tab = tab;                              // colGridLayout이 탭별 열수·비율·높이를 읽음
  applySavedLayout(tab, grid);                  // 접힘·고정·순서·전체폭(w12)을 먼저 적용
  grid.getItems().forEach((it) => addPanelTools(it.getElement(), tab));
  injectGridToolbar(tab);                       // 탭 상단 격자 열수 컨트롤
  grid.on("layoutEnd", () => positionDividers(tab));   // 레이아웃 후 나눔선 재배치
  grid.on("dragInit", () => { grid._dragging = true; });
  grid.on("dragReleaseEnd", () => {
    grid._dragging = false; saveLayout(tab); relayoutAfter(tab);
  });
  // 콘텐츠 높이가 바뀌면(카메라 상태·차트 추가·접기) 자동으로 다시 팩킹 →
  // 칸 넘침·겹침 방지. 드래그 중에는 건드리지 않는다.
  let roTimer = 0;
  const ro = new ResizeObserver(() => {
    if (grid._dragging) return;
    clearTimeout(roTimer);
    roTimer = setTimeout(() => { if (!grid._dragging) grid.refreshItems().layout(true); }, 90);
  });
  grid.getItems().forEach((it) => ro.observe(it.getElement()));
  ro.observe(el);   // 컨테이너 폭 변화(탭 표시·뷰포트 늦게 잡힘)에도 자동 재팩킹
  grid.refreshItems().layout(true);
  return grid;
}

function showTab(tab) {
  if (!TABS.includes(tab)) tab = "control";
  document.querySelectorAll(".tab").forEach((b) =>
    b.classList.toggle("active", b.dataset.tab === tab));
  document.querySelectorAll(".tab-pane").forEach((p) =>
    p.classList.toggle("active", p.dataset.pane === tab));
  try { localStorage.setItem(ACTIVE_TAB_KEY, tab); } catch (e) { /* noop */ }
  ensureGrid(tab);                 // 표시된 뒤에야 폭을 측정할 수 있다
  if (tab === "system") refreshDevices();
  relayoutAfter(tab);
}

function initWorkspace() {
  document.querySelectorAll(".tab").forEach((b) => {
    b.onclick = () => showTab(b.dataset.tab);
  });
  let active = "control";
  try { active = localStorage.getItem(ACTIVE_TAB_KEY) || "control"; }
  catch (e) { /* noop */ }
  showTab(active);
}

// ---------- 시스템 탭: 장비 연결 (ASCOM / PWI4) ----------

let deviceConfig = null;     // /api/system/devices 결과
const ascomCache = {};       // device key -> [{progid, name}]

async function refreshDevices() {
  try {
    deviceConfig = await (await fetch("/api/system/devices")).json();
    renderConnList();
  } catch (e) { /* noop */ }
}

function snapKeyFor(key) { return key === "filterwheel" ? "filter" : key; }

function connDevHtml(dev) {
  // 백엔드별 설정 필드를 가산적으로 — 기상처럼 ASCOM+URL 둘 다인 장비도 지원
  let cfg = "";
  if (dev.has_progid) {
    cfg += `<div class="conn-dev-cfg"><span class="cfg-lbl">ASCOM ProgID</span>` +
      `<select data-cfg="progid" data-dev="${dev.key}"></select>` +
      `<button class="btn" data-act="save" data-dev="${dev.key}">저장</button>` +
      `<button class="btn" data-act="setup" data-dev="${dev.key}" title="드라이버 설정창 (COM 포트 등)">⚙</button></div>`;
  }
  if (dev.has_url) {
    cfg += `<div class="conn-dev-cfg"><span class="cfg-lbl">URL / IP</span>` +
      `<input data-cfg="url" data-dev="${dev.key}" value="${escapeHtml(dev.url || "")}" placeholder="http://…">` +
      `<button class="btn" data-act="save" data-dev="${dev.key}">저장</button></div>`;
  }
  if (!cfg) cfg = `<div class="conn-dev-cfg"><span class="cfg-lbl">시뮬레이터 전용 — 설정 없음</span></div>`;
  const backend = dev.real_kinds.length
    ? `<div class="conn-dev-cfg devmode-only"><span class="cfg-lbl">백엔드 (REAL 모드) · DEV</span>` +
      `<select data-cfg="backend" data-dev="${dev.key}">` +
      `<option value="sim">sim</option>` +
      dev.real_kinds.map((k) => `<option value="${k}">${k}</option>`).join("") +
      `</select></div>`
    : "";
  return `<div class="conn-dev" data-dev="${dev.key}">
    <div class="conn-dev-top">
      <span class="cd-dot" data-role="dot"></span>
      <span class="cd-label">${escapeHtml(dev.label)}</span>
      <span class="cd-backend" data-role="backend" title="프로토콜/어댑터">${dev.backend}</span>
      <span class="cd-name" data-role="name" title="장비명"></span>
      <span class="cd-state off" data-role="state">미연결</span>
    </div>
    ${backend}${cfg}
    <div class="conn-dev-actions">
      <button class="btn btn-go" data-act="connect" data-dev="${dev.key}">연결</button>
      <button class="btn" data-act="reconnect" data-dev="${dev.key}">재연결</button>
      <button class="btn btn-danger" data-act="disconnect" data-dev="${dev.key}">해제</button>
    </div>
    <div class="cd-detail" data-role="detail"></div>
  </div>`;
}

async function wireConnDev(dev) {
  const root = document.querySelector(`.conn-dev[data-dev="${dev.key}"]`);
  if (!root) return;
  const bsel = root.querySelector('[data-cfg="backend"]');
  if (bsel) {
    bsel.value = dev.backend === "sim" ? "sim" : dev.backend;
    bsel.onchange = () => saveDeviceCfg(dev.key, { backend: bsel.value });
  }
  const psel = root.querySelector('[data-cfg="progid"]');
  if (psel) {
    psel.innerHTML = `<option value="">— 선택 —</option>`;
    let list = ascomCache[dev.key];
    if (!list) {
      try {
        const r = await (await fetch(`/api/system/ascom?device=${dev.key}`)).json();
        list = r.drivers || [];
      } catch (e) { list = []; }
      ascomCache[dev.key] = list;
    }
    list.forEach((d) => {
      const o = document.createElement("option");
      o.value = d.progid; o.textContent = `${d.name} · ${d.progid}`;
      psel.appendChild(o);
    });
    if (dev.progid && !list.some((d) => d.progid === dev.progid)) {
      const o = document.createElement("option");
      o.value = dev.progid; o.textContent = `${dev.progid} (저장됨)`;
      psel.appendChild(o);
    }
    psel.value = dev.progid || "";
  }
  root.querySelectorAll("[data-act]").forEach((b) => {
    b.onclick = () => deviceAction(dev.key, b.dataset.act, root);
  });
}

async function saveDeviceCfg(key, body) {
  try {
    deviceConfig = await post("/api/system/configure", { device: key, ...body });
    renderConnList();
  } catch (e) { /* post()가 이미 로그 */ }
}

async function deviceAction(key, act, root) {
  if (act === "save") {
    const psel = root.querySelector('[data-cfg="progid"]');
    const uinp = root.querySelector('[data-cfg="url"]');
    const body = {};
    if (psel) body.progid = psel.value;
    if (uinp) body.url = uinp.value.trim();
    return saveDeviceCfg(key, body);
  }
  if (act === "setup") {   // 드라이버 설정창(모달) — 응답은 {ok}, 목록 갱신만
    try { await post("/api/system/setup", { device: key }); refreshDevices(); }
    catch (e) { /* post()가 이미 로그 */ }
    return;
  }
  try {   // connect / disconnect / reconnect → describe() 반환
    deviceConfig = await post(`/api/system/${act}`, { device: key });
    renderConnList();
  } catch (e) { /* post()가 이미 로그 */ }
}

function renderConnList() {
  const host = $("conn-list");
  if (!host || !deviceConfig) return;
  host.innerHTML = deviceConfig.devices.map(connDevHtml).join("");
  deviceConfig.devices.forEach(wireConnDev);
  renderConnLive();
  // 장비 카드가 채워지면 연결 패널 높이가 커지므로 시스템 그리드를 다시 팩킹
  if (grids.system) grids.system.refreshItems().layout(true);
}

// /api/status 스냅샷에서 실시간 연결상태·장비명·detail을 칩에 반영
function renderConnLive() {
  if (!deviceConfig || !lastStatus) return;
  deviceConfig.devices.forEach((dev) => {
    const root = document.querySelector(`.conn-dev[data-dev="${dev.key}"]`);
    if (!root) return;
    const d = lastStatus[snapKeyFor(dev.key)] || {};
    const on = !!d.connected;
    const dot = root.querySelector('[data-role="dot"]');
    dot.classList.toggle("on", on);
    dot.classList.toggle("off", !on);
    // 장비명 — 드라이버가 보고하는 이름 (연결 시에만, 예: Hubo-i)
    root.querySelector('[data-role="name"]').textContent =
      on ? (d.device_name || d.name || "") : "";
    // 명시적 상태 — 연결됨 / 미연결 / 연결됨·응답대기 (드라이버만 붙고 실제 데이터 없음)
    const st = root.querySelector('[data-role="state"]');
    if (!on) { st.textContent = "미연결"; st.className = "cd-state off"; }
    else if (deviceResponding(dev.key, d)) { st.textContent = "연결됨"; st.className = "cd-state on"; }
    else { st.textContent = "연결됨 · 응답대기"; st.className = "cd-state warn"; }
    root.querySelector('[data-role="detail"]').textContent = d.detail || "";
    // 버튼 활성화: 연결되면 [연결] 비활성, 끊겨 있으면 [해제] 비활성
    const cBtn = root.querySelector('[data-act="connect"]');
    const dBtn = root.querySelector('[data-act="disconnect"]');
    if (cBtn) cBtn.disabled = on;
    if (dBtn) dBtn.disabled = !on;
  });
}

// 드라이버는 connected라는데 실제 데이터가 오는지 — 가대 물리연결 안 됐는데
// 드라이버만 붙어 connected로 뜨는 경우를 노란 '응답대기'로 구분.
function deviceResponding(key, d) {
  if (key === "mount") return d.alt != null || d.az != null || d.ra_hours != null;
  if (key === "weather") return d.temp != null;
  if (key === "focuser") return d.position != null;
  return true;  // 카메라/필터휠 등은 connected로 충분
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

  // 장비 연결 칩 (자동 이름 + 초록/빨강)
  setConn("conn-mount", s.mount, "MOUNT");
  setConn("conn-camera", s.camera, "CAMERA");
  setConn("conn-focuser", s.focuser, "FOCUSER");
  setConn("conn-weather", s.weather, "WEATHER");

  // 망원경
  const m = s.mount || {};
  $("m-alt").textContent = fmt(m.alt, 2, "°");
  $("m-az").textContent = fmt(m.az, 2, "°");
  $("m-ra").textContent = m.ra_str || "—";
  $("m-dec").textContent = m.dec_str || "—";
  $("m-track").classList.toggle("on", !!m.tracking);
  $("m-slew").classList.toggle("on", !!m.slewing);
  $("m-park").classList.toggle("on", !!m.at_park);
  // 파킹/홈 — 드라이버가 지원할 때만 노출, 상태에 맞게 버튼 활성화
  const parkRow = $("park-row");
  if (parkRow) {
    parkRow.style.display = (m.can_park || m.can_home) ? "" : "none";
    $("btn-park").disabled = !m.can_park || !!m.at_park;
    $("btn-unpark").disabled = !m.can_park || !m.at_park;
    $("btn-home").disabled = !m.can_home || !!m.at_park;
    $("btn-setpark").disabled = !m.can_park;
  }

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
  $("w-winddir").textContent = fmt(w.wind_dir, 0, "°");
  $("w-cloud").textContent = fmt(w.cloud, 2);
  $("w-rain").textContent = w.rain ? "감지" : "없음";
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
  drawSky(s);   // 1Hz 라이브 위치를 즉시 렌더 (보간 없음 = 실시간)
  kickSky();    // 목표 마커 펄스 애니메이션만 (필요할 때) 재개

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

  renderSystemInfo(s);
  renderConnLive();
  updateModeSeg(s.mode);
}

function setSysRow(key, state, text) {
  const dot = $(`sys-${key}-dot`);
  if (dot) dot.className = "sys-dot" +
    (state === "idle" ? " idle" : state === "run" ? " run" : "");
  const st = $(`sys-${key}-state`);
  if (st) st.textContent = text;
}

// 장비 연결 칩: 연결되면 초록+장비명, 안 되면 빨강+타입라벨
function setConn(id, dev, fallback) {
  const el = $(id);
  if (!el) return;
  const on = !!(dev && dev.connected);
  el.classList.toggle("on", on);
  el.classList.toggle("off", !on);
  el.textContent = (on && dev.name) ? dev.name : fallback;
  el.title = on ? (dev.name || fallback) : `${fallback} 미연결`;
}

// ---------- 설정 드로어 ----------

function updateModeSeg(mode) {
  const active = (mode === "sim") ? "sim" : "real";
  document.querySelectorAll("#mode-seg .seg-btn").forEach((b) =>
    b.classList.toggle("active", b.dataset.mode === active));
}

// 시스템 탭 사이트/모드 정보 (항상 DOM에 있으므로 매 스냅샷 갱신)
function renderSystemInfo(s) {
  const set = (id, v) => { const el = $(id); if (el) el.textContent = v; };
  set("set-site", s.site || "—");
  set("set-mode", (s.mode || "—").toUpperCase());
  set("set-lst", s.time?.lst || "—");
  const sun = s.sun || {}, tw = s.twilight_sim || {};
  set("set-phase", (sun.phase_label || "—") + (tw.enabled ? " · 황혼시뮬" : ""));
}

const DEVMODE_KEY = "asterion.devmode";
function applyDevMode(on) {
  document.body.classList.toggle("devmode", on);  // 시스템 탭 고급 컨트롤 게이팅
  $("devmode-toggle").checked = on;
  try { localStorage.setItem(DEVMODE_KEY, on ? "1" : "0"); } catch (e) { /* noop */ }
}

function openDrawer(open) {
  $("dev-drawer").classList.toggle("open", open);
  $("dev-overlay").classList.toggle("open", open);
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
  // 목표 마커 펄스만 rAF로 애니메이션. 마운트는 1Hz 라이브 위치라 보간 불필요
  // (실물 마운트의 실제 위치를 그대로 표시 = 실시간).
  return !!skyTarget;
}
function skyLoop() {
  if (lastStatus) drawSky(lastStatus);
  skyRaf = skyNeedsAnim() ? requestAnimationFrame(skyLoop) : 0;
}
function kickSky() { if (!skyRaf) skyRaf = requestAnimationFrame(skyLoop); }

// ---------- 초기 로드 ----------

async function init() {
  initWorkspace();
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
$("btn-park").onclick = () => { skyTarget = null; post("/api/actions/mount/park"); };
$("btn-unpark").onclick = () => post("/api/actions/mount/unpark");
$("btn-home").onclick = () => { skyTarget = null; post("/api/actions/mount/home"); };
$("btn-setpark").onclick = () => post("/api/actions/mount/setpark");

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
  try { localStorage.removeItem(layoutKey(currentTab())); } catch (e) { /* noop */ }
  location.reload();
};

// 전역 로그 독 — 헤더 클릭으로 펼치기/접기, 상태를 localStorage에 저장
const LOGDOCK_KEY = "asterion.logdock";
function setLogDock(open) {
  const dock = $("log-dock"); if (!dock) return;
  dock.classList.toggle("collapsed", !open);
  try { localStorage.setItem(LOGDOCK_KEY, open ? "1" : "0"); } catch (e) { /* noop */ }
  if (open) logEl.scrollTop = logEl.scrollHeight;
}
$("log-dock-head").onclick = () =>
  setLogDock($("log-dock").classList.contains("collapsed"));
setLogDock((() => {
  try { return localStorage.getItem(LOGDOCK_KEY) === "1"; } catch (e) { return false; }
})());
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
    const g = grids[currentTab()];
    if (g) g.refreshItems().layout(true);
    if (lastStatus) applyStatus(lastStatus);
    drawTimeline();
    drawAllCharts();
  }, 150);
});

// ---------------- 임베드 패널 (위성영상·CCTV 등 외부 소스 붙여넣기) ----------------
// Grafana 패널처럼 URL만 붙여넣으면 이미지/MJPEG/페이지를 카드 안에 띄운다.
// 설정은 패널별로 localStorage(asterion.embed.<id>)에 저장된다. (ENV 탭)
(function initEmbeds() {
  const EKEY = (id) => `asterion.embed.${id}`;
  const timers = {};
  const hostOf = (u) => { try { return new URL(u).host; } catch (e) { return u.slice(0, 36); } };
  const q = (sel) => document.querySelector(sel);
  const loadCfg = (id) => {
    try { return JSON.parse(localStorage.getItem(EKEY(id)) || "null") || {}; }
    catch (e) { return {}; }
  };
  const storeCfg = (id, cfg) => {
    try { localStorage.setItem(EKEY(id), JSON.stringify(cfg)); } catch (e) { /* noop */ }
  };

  function renderEmbed(id) {
    const view = q(`.embed-view[data-embed="${id}"]`);
    if (!view) return;
    const img = view.querySelector(".embed-img");
    const frame = view.querySelector(".embed-frame");
    const empty = view.querySelector(".embed-empty");
    const meta = q(`.embed-meta[data-embed-meta="${id}"]`);
    const cfg = loadCfg(id);

    clearInterval(timers[id]);
    img.classList.remove("on"); frame.classList.remove("on");
    img.removeAttribute("src"); frame.removeAttribute("src");
    img.onerror = null;

    if (!cfg.url) {
      empty.style.display = "";
      if (meta) meta.textContent = "—";
      return;
    }
    empty.style.display = "none";
    const tag = hostOf(cfg.url);

    if (cfg.type === "iframe") {
      frame.src = cfg.url;
      frame.classList.add("on");
      if (meta) meta.textContent = "페이지 · " + tag;
      return;
    }
    // image·stream 둘 다 <img>로 표시 (MJPEG는 브라우저가 스트림을 유지)
    img.onerror = () => { if (meta) meta.textContent = "로드 실패 · " + tag; };
    img.classList.add("on");
    if (cfg.type === "stream") {
      img.src = cfg.url;
      if (meta) meta.textContent = "스트림 · " + tag;
    } else {
      const bust = () => {
        const sep = cfg.url.includes("?") ? "&" : "?";
        img.src = cfg.url + sep + "_t=" + Date.now();   // 캐시버스트로 새 프레임
      };
      bust();
      const sec = Math.max(0, Number(cfg.interval) || 0);
      if (sec > 0) timers[id] = setInterval(bust, sec * 1000);
      if (meta) meta.textContent = (sec > 0 ? `이미지 · ${sec}s 갱신` : "이미지") + " · " + tag;
    }
  }

  function showEditor(id, open) {
    const editor = q(`.embed-editor[data-embed-editor="${id}"]`);
    if (!editor) return;
    if (open) {
      const cfg = loadCfg(id);
      editor.querySelector("[data-embed-url]").value = cfg.url || "";
      if (cfg.type) editor.querySelector("[data-embed-type]").value = cfg.type;
      if (cfg.interval != null) editor.querySelector("[data-embed-interval]").value = cfg.interval;
    }
    editor.hidden = !open;
    const g = grids[currentTab()]; if (g) g.refreshItems().layout(true);   // 높이 변화 → 재팩킹
  }

  document.querySelectorAll("[data-embed-gear]").forEach((btn) => {
    btn.onclick = (e) => {
      e.stopPropagation();   // 카드헤드 드래그/접기와 충돌 방지
      const id = btn.dataset.embedGear;
      const editor = q(`.embed-editor[data-embed-editor="${id}"]`);
      showEditor(id, editor.hidden);
    };
  });

  document.querySelectorAll(".embed-editor").forEach((editor) => {
    const id = editor.dataset.embedEditor;
    editor.querySelector("[data-embed-save]").onclick = () => {
      storeCfg(id, {
        url: editor.querySelector("[data-embed-url]").value.trim(),
        type: editor.querySelector("[data-embed-type]").value,
        interval: Number(editor.querySelector("[data-embed-interval]").value) || 0,
      });
      renderEmbed(id);
      showEditor(id, false);
    };
    editor.querySelector("[data-embed-clear]").onclick = () => {
      try { localStorage.removeItem(EKEY(id)); } catch (e) { /* noop */ }
      editor.querySelector("[data-embed-url]").value = "";
      renderEmbed(id);
      showEditor(id, false);
    };
    editor.querySelector("[data-embed-cancel]").onclick = () => showEditor(id, false);
  });

  document.querySelectorAll(".embed-view[data-embed]").forEach((v) => renderEmbed(v.dataset.embed));
})();

init();
