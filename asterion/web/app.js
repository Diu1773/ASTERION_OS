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

function logLine(e) {
  // 모든 .log 컨테이너에 추가 — 전역 독(#log)과 시스템 탭 패널(#log-sys) 동기화
  const html = `<span class="l-ts">${e.ts}</span> <span class="l-src">[${e.source}]</span> ${escapeHtml(e.msg)}`;
  document.querySelectorAll(".log").forEach((logEl) => {
    const div = document.createElement("div");
    div.className = `l-${e.level || "info"}`;
    div.innerHTML = html;
    logEl.appendChild(div);
    while (logEl.childNodes.length > 300) logEl.removeChild(logEl.firstChild);
    logEl.scrollTop = logEl.scrollHeight;
  });
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
function drawSpark(cls, buf, color) {   // 모든 인스턴스(.js-c-sp-*)에 그린다
  document.querySelectorAll("." + cls).forEach((cv) => drawSparkOn(cv, buf, color));
}
function drawSparkOn(cv, buf, color) {
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
// 돔 방위 반전 (SkyX/PWI처럼 E↔W, N↔S 뒤집기). proj·역투영·방위라벨에 반영.
let skyFlip = (() => { try { return JSON.parse(localStorage.getItem("asterion.skyflip") || "{}") || {}; } catch (e) { return {}; } })();
function saveSkyFlip() { try { localStorage.setItem("asterion.skyflip", JSON.stringify(skyFlip)); } catch (e) { /* noop */ } }

function drawSky(s) {   // 모든 인스턴스(.js-c-sky: 원본 + 관제 탭 복제)에 그린다
  document.querySelectorAll(".js-c-sky").forEach((cv) => drawSkyOn(cv, s));
}
function drawSkyOn(cv, s) {
  const { ctx, w, h } = hidpi(cv);
  ctx.clearRect(0, 0, w, h);
  const cx = w / 2, cy = h / 2, R = Math.min(w, h) / 2 - 12;
  if (R <= 0) return;   // 숨겨진 탭이면 캔버스 크기 0 → 반지름 음수 (그리기 생략)
  if (cv.id === "sky-canvas") skyGeom = { cx, cy, R };   // 클릭 기하는 원본만 저장
  const sun = s.sun || {}, tw = s.twilight_sim || {}, m = s.mount || {};

  let b = tw.enabled ? clamp(tw.factor || 0, 0, 1)
                     : clamp(((sun.alt ?? -18) + 18) / 36, 0, 1);
  const g = ctx.createRadialGradient(cx, cy - R * 0.15, R * 0.1, cx, cy, R);
  g.addColorStop(0, mix([12, 18, 33], [96, 150, 214], b));
  g.addColorStop(0.7, mix([9, 13, 26], [60, 100, 165], b));
  g.addColorStop(1, mix([5, 8, 16], [150, 110, 70], b * 0.7));
  ctx.beginPath(); ctx.arc(cx, cy, R, 0, TAU); ctx.fillStyle = g; ctx.fill();

  const sx = skyFlip.ew ? -1 : 1, sy = skyFlip.ns ? -1 : 1;   // 방위 반전
  ctx.lineWidth = 1;
  [30, 60].forEach((alt) => {
    const r = (90 - alt) / 90 * R;
    ctx.beginPath(); ctx.arc(cx, cy, r, 0, TAU);
    ctx.strokeStyle = "rgba(150,180,225,.14)"; ctx.stroke();
    ctx.fillStyle = "rgba(150,180,225,.5)"; ctx.font = "9px monospace";
    ctx.textAlign = "left"; ctx.textBaseline = "middle";
    ctx.fillText(`${alt}°`, cx + 4, cy - r);                  // 고도선 숫자
  });
  ctx.strokeStyle = "rgba(150,180,225,.10)";
  ctx.beginPath(); ctx.moveTo(cx - R, cy); ctx.lineTo(cx + R, cy);
  ctx.moveTo(cx, cy - R); ctx.lineTo(cx, cy + R); ctx.stroke();
  ctx.beginPath(); ctx.arc(cx, cy, R, 0, TAU);
  ctx.strokeStyle = "rgba(150,180,225,.4)"; ctx.lineWidth = 1.5; ctx.stroke();
  ctx.fillStyle = "rgba(190,210,240,.65)"; ctx.font = "11px monospace";
  ctx.textAlign = "center"; ctx.textBaseline = "middle";
  ctx.fillText(sy < 0 ? "S" : "N", cx, cy - R + 11);   // 반전 반영
  ctx.fillText(sy < 0 ? "N" : "S", cx, cy + R - 11);
  ctx.fillText(sx < 0 ? "W" : "E", cx + R - 11, cy);
  ctx.fillText(sx < 0 ? "E" : "W", cx - R + 11, cy);

  const proj = (alt, az) => {
    const r = (90 - clamp(alt, 0, 90)) / 90 * R, a = az * D2R;
    return [cx + sx * r * Math.sin(a), cy - sy * r * Math.cos(a)];
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

  const hasMount = !m.stale && m.alt != null && m.az != null &&
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
  const sx = skyFlip.ew ? -1 : 1, sy = skyFlip.ns ? -1 : 1;   // 반전 역적용
  const dx = px - skyGeom.cx, dy = py - skyGeom.cy;
  const r = Math.hypot(dx, dy);
  if (r > skyGeom.R) { hideSkyMenu(); return; }
  const alt = 90 - (r / skyGeom.R) * 90;
  const az = ((Math.atan2(sx * dx, -sy * dy) / D2R) + 360) % 360;
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
    // RA/DEC로 슬루 (SlewToCoordinatesAsync) — AltAz 슬루 미지원 적도의도 동작.
    // 엔드포인트(GotoRaDecReq)는 ra/dec를 문자열로 받는다 → 문자열로 보냄.
    try { await post("/api/actions/mount/goto_radec", { ra: ra.toFixed(5), dec: dec.toFixed(4) }); }
    catch (e) { skyTarget = null; }
  };
  $("sm-close").onclick = hideSkyMenu;
}
function hideSkyMenu() { $("sky-menu").style.display = "none"; }

// ---------- ADU 게이지 ----------

function drawGauge(af, aduMin, aduMax) {
  document.querySelectorAll(".js-c-gauge").forEach((cv) => drawGaugeOn(cv, af, aduMin, aduMax));
}
function drawGaugeOn(cv, af, aduMin, aduMax) {
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
  if (!timelineData) return;
  lastTimelineDraw = Date.now();
  document.querySelectorAll(".js-c-tl").forEach((cv) => drawTimelineOn(cv));
}
function drawTimelineOn(cv) {
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
const TABS = ["control", "devices", "env", "plan", "analysis", "system"];
const ACTIVE_TAB_KEY = "asterion.activetab";
const grids = {};   // tab -> Muuri 인스턴스 (탭별 독립 그리드)

// ── Gridstack 프로토타입 (?proto=<tab> | ?proto=1 → devices) ──────────────
// 기존 Muuri 경로는 그대로 두고, 지정 탭만 자유배치·리사이즈 그리드로 띄운다.
// Gridstack 인스턴스는 grids[]가 아니라 gstacks[]에 둔다 — 리사이즈 핸들러 등
// 기존 코드가 grids[tab]에 Muuri 메서드를 호출하므로 섞이면 안 된다.
const gstacks = {};
// 기본 레이아웃 = Gridstack 자유배치 MCT (관제 탭은 ＋패널 구성 기능 위해 Muuri 유지).
//   기본(파라미터 없음) → 관제 제외 전 탭 MCT
//   ?classic=1 또는 ?proto=off → 전 탭 옛 Muuri
//   ?proto=all|1 → 관제 포함 전 탭 MCT
//   ?proto=devices,env → 지정 탭만 MCT
const PROTO_TABS = (() => {
  const params = new URLSearchParams(location.search);
  if (params.has("classic")) return new Set();
  const p = params.get("proto");
  if (p === "0" || p === "off") return new Set();
  if (p === "1" || p === "all") return new Set(TABS);
  if (p) return new Set(p.split(",").map((s) => s.trim()).filter((t) => TABS.includes(t)));
  return new Set(TABS);   // 기본: 관제 포함 전 탭 MCT (관제는 미러 조합을 Gridstack에서)
})();
// 패널별 초기 높이(격자 칸 수). 사용자가 드래그/리사이즈하면 그 값이 저장된다.
const PROTO_GS_H = {
  sky: 9, skyflat: 9, mount: 6, camera: 6, focuser: 5, image: 7,
  safety: 5, weather: 7, "embed-sat": 8, "embed-cctv": 8,
  timeline: 6, plots: 9, frames: 7, actions: 7,
  connections: 7, "log-sys": 7, sysinfo: 5,
};
// devices 탭 기본 배치 — 비대칭 미션컨트롤: 큰 Sky 모니터(좌측 세로) + 우측 계기
// 클러스터(오토플랫·마운트·카메라) + 하단 와이드(포커서·프레임 뷰어). 12열 무빈칸 타일.
const PROTO_GS_LAYOUT = {
  // 장비(devices) — 컴팩트. 상단 [Sky | 오토플랫] (큰 viz), 중단 [마운트|카메라|포커서]
  // 좁은 3열(readout이 space-between으로 폭 채워 narrow가 딱 맞음), 하단 Imaging 풀폭.
  sky:     { x: 0, y: 0,  w: 6, h: 11 },
  skyflat: { x: 6, y: 0,  w: 6, h: 11 },
  mount:   { x: 0, y: 11, w: 4, h: 12 },
  camera:  { x: 4, y: 11, w: 4, h: 12 },
  focuser: { x: 8, y: 11, w: 4, h: 8  },
  image:   { x: 8, y: 17, w: 4, h: 8  },
  // 기상(env) — 좌측 안전·기상(바닥 y18), 우측 위성·CCTV 와이드(바닥 y18)
  safety:       { x: 0, y: 0, w: 4, h: 8  },
  weather:      { x: 0, y: 8, w: 4, h: 10 },
  "embed-sat":  { x: 4, y: 0, w: 8, h: 9  },
  "embed-cctv": { x: 4, y: 9, w: 8, h: 9  },
  // 계획(plan)
  timeline: { x: 0, y: 0, w: 12, h: 9 },
  // 분석(analysis) — 차트 풀폭 상단, 프레임·액션 하단 2열(바닥 맞춤)
  plots:   { x: 0, y: 0,  w: 12, h: 10 },
  frames:  { x: 0, y: 10, w: 6,  h: 8  },
  actions: { x: 6, y: 10, w: 6,  h: 8  },
  // 시스템(system) — 연결 + 로그 상단(바닥 맞춤), 시스템정보 풀폭 하단
  connections: { x: 0, y: 0,  w: 8,  h: 10 },
  "log-sys":   { x: 8, y: 0,  w: 4,  h: 10 },
  sysinfo:     { x: 0, y: 10, w: 12, h: 6  },
};

// 패널별 sizing 정의 — 비율잠금은 viz만(폼은 내용이 안 늘어나 무의미), control은 min/max로
// '내용 이상 못 늘어나게' 막아 여백 제거. ar=[w,h]는 viz의 박스 목표비(잠금/스냅 대상).
// fills: true=viz채움, 'gauge'/'scroll'=부분채움, false=폼. (defH는 라이브 측정 반영)
const PANEL_DEF = {
  sky:          { klass: "viz",     fills: true,     ar: [6, 7],  minW: 4, minH: 9,  defW: 6,  defH: 12, maxW: 9 },
  skyflat:      { klass: "mixed",   fills: "gauge",  ar: null,    minW: 6, minH: 9,  defW: 6,  defH: 10, maxW: 8 },
  mount:        { klass: "control", fills: false,    ar: null,    minW: 4, minH: 9,  defW: 6,  defH: 12, maxW: 6, maxH: 14 },
  camera:       { klass: "control", fills: false,    ar: null,    minW: 4, minH: 10, defW: 6,  defH: 12, maxW: 6, maxH: 14 },
  focuser:      { klass: "control", fills: false,    ar: null,    minW: 4, minH: 5,  defW: 6,  defH: 8,  maxW: 8, maxH: 9 },
  image:        { klass: "viz",     fills: true,     ar: [3, 2],  minW: 4, minH: 6,  defW: 6,  defH: 9,  maxW: 12 },
  safety:       { klass: "control", fills: false,    ar: null,    minW: 4, minH: 6,  defW: 5,  defH: 8,  maxW: 6, maxH: 8 },
  weather:      { klass: "mixed",   fills: false,    ar: null,    minW: 4, minH: 9,  defW: 5,  defH: 11, maxW: 7 },
  "embed-sat":  { klass: "viz",     fills: true,     ar: [16, 9], minW: 5, minH: 6,  defW: 7,  defH: 8,  maxW: 12 },
  "embed-cctv": { klass: "viz",     fills: true,     ar: [16, 9], minW: 5, minH: 6,  defW: 7,  defH: 8,  maxW: 12 },
  timeline:     { klass: "viz",     fills: true,     ar: [12, 3], minW: 8, minH: 5,  defW: 12, defH: 6,  maxW: 12 },
  plots:        { klass: "viz",     fills: true,     ar: [12, 5], minW: 7, minH: 6,  defW: 12, defH: 9,  maxW: 12 },
  frames:       { klass: "control", fills: false,    ar: null,    minW: 4, minH: 6,  defW: 6,  defH: 9,  maxW: 12 },
  actions:      { klass: "control", fills: false,    ar: null,    minW: 4, minH: 6,  defW: 6,  defH: 9,  maxW: 12 },
  connections:  { klass: "control", fills: false,    ar: null,    minW: 5, minH: 8,  defW: 8,  defH: 10, maxW: 12 },
  "log-sys":    { klass: "control", fills: "scroll", ar: null,    minW: 3, minH: 8,  defW: 4,  defH: 10, maxW: 12 },
  sysinfo:      { klass: "control", fills: false,    ar: null,    minW: 6, minH: 4,  defW: 12, defH: 6,  maxW: 12, maxH: 6 },
};

// 리사이즈 시 viz 패널을 정의된 비율(ar)로 스냅 (높이를 폭에 맞춤). control/mixed는 제외.
// min/max가 비율보다 우선. _arLock: grid.update→change→resize 재귀 루프 방지.
let _arLock = false;
function aspectSnap(grid, el) {
  if (_arLock || !el) return;
  const id = el.getAttribute("gs-id") || el.dataset.panel;
  const sp = PANEL_DEF[id];
  if (!sp || sp.klass !== "viz" || !sp.ar) return;
  const n = el.gridstackNode; if (!n) return;
  const colPx = grid.cellWidth(), rowPx = 40 + 5;       // 컬럼 px / (cellHeight+margin)
  let h = Math.round((n.w * colPx) / (sp.ar[0] / sp.ar[1]) / rowPx);
  h = Math.max(sp.minH, sp.maxH ? Math.min(sp.maxH, h) : h);
  if (h !== n.h) { _arLock = true; grid.update(el, { h }); _arLock = false; }
}

function currentTab() {
  const b = document.querySelector(".tab.active");
  return b ? b.dataset.tab : "control";
}
function layoutKey(tab) { return `asterion.layout.${tab}.v5`; }
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
    spans: Object.fromEntries(els.map((el) =>   // 카드가 차지하는 열 수 (키워 보기)
      [el.dataset.panel, parseInt(el.dataset.span) || (el.classList.contains("w12") ? 99 : 1)])),
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
    if (layout.spans && layout.spans[pid]) { el.dataset.span = layout.spans[pid]; el.classList.remove("w12"); }
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
    `<button class="pt-btn pt-max" title="크게 보기(오버레이)">⛶</button>` +
    `<button class="pt-btn pt-size" title="칸 수 늘리기 (격자 안에서 크게)">⤢</button>` +
    `<button class="pt-btn pt-collapse" title="접기/펼치기">▾</button>`;
  head.appendChild(tools);
  tools.querySelector(".pt-max").onclick = (e) => { e.stopPropagation(); toggleMaximize(item); };
  tools.querySelector(".pt-size").onclick = (e) => {
    e.stopPropagation();
    const N = (grids[tab] && grids[tab]._cols) || colCount(tab);   // 차지할 칸 수 순환 1→2→…→N→1
    const cur = Math.min(N, Math.max(1, parseInt(item.dataset.span) || (item.classList.contains("w12") ? N : 1)));
    item.dataset.span = cur >= N ? 1 : cur + 1;
    item.classList.remove("w12");                                   // 이제 dataset.span이 기준
    grids[tab].refreshItems().layout(true);   // 즉시 — 애니메이션 레이아웃 경쟁 방지
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
  // 1) 행 배정(가로 정렬): 카드를 왼→오로 채우되 각 카드는 span개 열을 차지(키워 보기).
  //    현재 행에 안 들어가면 다음 행. → 더 큰 패널도 격자가 정렬된 채 비율 맞춰 커진다.
  const rowOf = new Array(n), colOf = new Array(n);
  let row = 0, col = 0;
  for (let i = 0; i < n; i++) {
    const el = els[i];
    let span = parseInt(el.dataset.span) || (el.classList.contains("w12") ? N : 1);
    span = Math.min(N, Math.max(1, span));
    if (col > 0 && col + span > N) { row++; col = 0; }   // 이 행에 안 들어가면 다음 행
    rowOf[i] = row; colOf[i] = col;
    let wpx = 0; for (let k = col; k < col + span; k++) wpx += (colW[k] || 0);
    el.style.width = wpx + "px";
    el.style.height = "";
    col += span; if (col >= N) { row++; col = 0; }
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
    slots.push(colX[colOf[i]], rowY[rowOf[i]]);
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
    `</div><button class="gt-tile" title="수동 폭·높이 리사이즈를 해제하고 깔끔히 정렬">⊞ 정렬</button>` +
    `<button class="gt-tile gt-newwin" title="이 탭을 새 창으로 (듀얼모니터)">⧉ 새 창</button>`;
  pane.insertBefore(bar, gridEl);
  bar.querySelectorAll(".gt-btn").forEach((b) => (b.onclick = () => setCols(tab, Number(b.dataset.cols))));
  bar.querySelector(".gt-tile:not(.gt-newwin)").onclick = () => tileGrid(tab);
  bar.querySelector(".gt-newwin").onclick = () =>
    window.open(`${location.origin}/?tab=${tab}&solo=1`, "_blank", "noopener");
  // 관제 탭만 자유 구성 — 다른 탭 패널을 라이브 미러로 가져오는 '+ 패널' 팔레트
  if (tab === "control") {
    const add = document.createElement("button");
    add.className = "gt-tile gt-addpanel";
    add.title = "다른 탭의 패널을 관제 탭으로 가져오기 (라이브 미러)";
    add.textContent = "＋ 패널";
    bar.appendChild(add);
    add.onclick = () => openPanelPalette(tab, add);
  }
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
  const colX = grid._colX || [], colW = grid._colW || [];
  // 행별로 인접 카드 사이 '실제 경계'에만 세로선 세그먼트 → 스팬 카드 위를 안 지나감.
  const rows = {};
  grid.getItems().forEach((it) => {
    const r = it.getElement().getBoundingClientRect();
    const y = Math.round(r.top - cr.top);
    (rows[y] = rows[y] || []).push({ x: r.left - cr.left, right: r.right - cr.left, top: r.top - cr.top, h: r.height });
  });
  const specs = [];
  Object.values(rows).forEach((row) => {
    row.sort((a, b) => a.x - b.x);
    for (let i = 0; i < row.length - 1; i++) {
      const bx = (row[i].right + row[i + 1].x) / 2;          // 두 카드 사이 거터 중앙
      let c = -1;
      for (let k = 0; k < N - 1; k++) if (Math.abs((colX[k] + colW[k]) - bx) < 24) { c = k; break; }
      if (c >= 0) specs.push({ left: colX[c] + colW[c], top: Math.min(row[i].top, row[i + 1].top),
        len: Math.max(row[i].h, row[i + 1].h), c });
    }
  });
  const pool = [...layer.querySelectorAll(".divider")];   // DOM 재사용 → 깜빡임 X
  while (pool.length < specs.length) { const d = document.createElement("div"); d.className = "divider"; layer.appendChild(d); pool.push(d); }
  while (pool.length > specs.length) { layer.removeChild(pool.pop()); }
  specs.forEach((s, i) => {
    const d = pool[i];
    d.className = "divider col-divider"; d.title = "드래그해 열 폭 조절";
    d.style.left = s.left + "px"; d.style.top = s.top + "px"; d.style.height = s.len + "px"; d.style.width = "";
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
  if (tab === "control") injectMirrors(el);   // 저장된 미러 타일을 먼저 DOM에 → Muuri가 흡수
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
  grid.getItems().forEach((it) => wireMirrorRemove(it.getElement(), tab));  // 미러 타일 ✕
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

// ── Gridstack 프로토타입 구현 ────────────────────────────────────────────
function gsKey(tab) { return `asterion.gslayout.${tab}.v3`; }

function saveGSLayout(tab, grid) {
  try {
    const out = grid.getGridItems().map((el) => {
      const n = el.gridstackNode || {};
      return { p: el.getAttribute("gs-id") || el.dataset.panel,
               x: n.x, y: n.y, w: n.w, h: n.h };
    });
    localStorage.setItem(gsKey(tab), JSON.stringify(out));
  } catch (e) { /* noop */ }
}

function applyGSLayout(tab, grid) {
  let saved = null;
  try { saved = JSON.parse(localStorage.getItem(gsKey(tab)) || "null"); }
  catch (e) { return; }
  if (!Array.isArray(saved)) return;
  const by = {}; saved.forEach((s) => { by[s.p] = s; });
  grid.batchUpdate();
  grid.getGridItems().forEach((el) => {
    const s = by[el.getAttribute("gs-id") || el.dataset.panel];
    if (s) grid.update(el, { x: s.x, y: s.y, w: s.w, h: s.h });
  });
  grid.commit();
}

function injectProtoBanner(tab) {
  const pane = document.querySelector(`.tab-pane[data-pane="${tab}"]`);
  if (!pane || pane.querySelector(".proto-banner")) return;
  const b = document.createElement("div");
  b.className = "proto-banner";
  b.innerHTML =
    (tab === "control" ? `<button class="pb-add" title="다른 탭 패널을 관제에 추가">＋ 패널</button>` : "") +
    `<button class="pb-reset" title="이 탭 패널 배치를 기본값으로">↺ 배치 초기화</button>`;
  pane.insertBefore(b, pane.firstChild);
  b.querySelector(".pb-reset").onclick = () => {
    try { localStorage.removeItem(gsKey(tab)); } catch (e) { /* noop */ }
    location.reload();
  };
  const add = b.querySelector(".pb-add");
  if (add) add.onclick = () => openPanelPalette(tab, add);
}

// 한 탭의 Muuri 카드를 Gridstack 위젯으로 변환하고 그리드를 띄운다 (탭이 보일 때 1회).
function ensureGridStack(tab) {
  if (gstacks[tab]) return gstacks[tab];
  const el = document.getElementById(`grid-${tab}`);
  if (!el || typeof GridStack === "undefined") return null;
  if (tab === "control") injectMirrors(el);   // 저장된 미러를 DOM에 먼저 → 아래서 위젯으로 흡수
  const items = [...el.querySelectorAll(":scope > .muuri-item")];
  items.forEach((it) => {
    const pid = it.dataset.panel;
    const defKey = it.dataset.mirror || pid;        // 미러면 원본 패널 키로 PANEL_DEF 조회
    const sp = PANEL_DEF[defKey] || { minW: 3, minH: 2 };
    const pos = PROTO_GS_LAYOUT[pid];
    const w = pos ? pos.w : (sp.defW || (it.classList.contains("w12") ? 12 : it.classList.contains("w8") ? 8
            : it.classList.contains("w4") ? 4 : 6));
    const h = pos ? pos.h : (sp.defH || PROTO_GS_H[pid] || 6);   // 미러: PANEL_DEF.defH (전엔 fallback 6→잘림)
    WIDTHS.forEach((c) => it.classList.remove(c));
    it.classList.remove("muuri-item");
    it.classList.add("grid-stack-item");
    it.setAttribute("gs-id", pid);
    it.setAttribute("gs-w", w);
    it.setAttribute("gs-h", h);
    it.setAttribute("gs-min-w", sp.minW);
    it.setAttribute("gs-min-h", sp.minH);
    if (sp.maxW) it.setAttribute("gs-max-w", sp.maxW);
    if (sp.maxH) it.setAttribute("gs-max-h", sp.maxH);
    if (sp.fills) it.classList.add("gs-fill");   // viz-fill CSS 대상 표시
    if (pos) { it.setAttribute("gs-x", pos.x); it.setAttribute("gs-y", pos.y); }
    else { it.setAttribute("gs-auto-position", "true"); }
    const c = it.querySelector(":scope > .muuri-item-content");
    if (c) { c.classList.remove("muuri-item-content"); c.classList.add("grid-stack-item-content"); }
  });
  el.classList.remove("muuri");
  el.classList.add("grid-stack");
  const BASE_CELL = 40;
  const grid = GridStack.init({
    column: 12, cellHeight: BASE_CELL, margin: 5, float: false,
    handle: ".card-head", draggable: { handle: ".card-head" },
    resizable: { handles: "all" },
  }, el);
  gstacks[tab] = grid;
  applyGSLayout(tab, grid);
  if (tab === "control") grid.getGridItems().forEach((gel) => wireGSMirrorRemove(gel, tab));
  // 폭은 12열로 자동 반응(컬럼 %), 높이(cellHeight)는 고정. 컨테이너 크기 변화 시
  // 캔버스(돔·게이지)만 다시 그린다. (비율 스케일은 콘텐츠 높이와 충돌해 보류 — 후속)
  let roT = 0;
  const ro = new ResizeObserver(() => {
    clearTimeout(roT);
    roT = setTimeout(() => { if (lastStatus) applyStatus(lastStatus); }, 80);
  });
  ro.observe(el);
  // 저장은 드롭/리사이즈 종료 시 1회만 (연속 drag 이벤트엔 저장 금지 → thrashing 방지)
  let st = 0;
  grid.on("change", () => { clearTimeout(st); st = setTimeout(() => saveGSLayout(tab, grid), 200); });
  grid.on("dragstart resizestart", () => document.body.classList.add("gs-dragging"));
  grid.on("resizestop dragstop", () => {
    document.body.classList.remove("gs-dragging");
    if (lastStatus) applyStatus(lastStatus); drawAllCharts();
  });
  grid.on("resize", () => { if (lastStatus) applyStatus(lastStatus); });
  grid.on("resizestop", (ev, el) => aspectSnap(grid, el));   // viz 비율 유지 — 드롭 시에만(드래그 중 호출하면 gridstack 리사이즈 상태가 깨져 멈춤)
  injectProtoBanner(tab);
  relayoutAfter(tab);
  return grid;
}

function showTab(tab) {
  if (!TABS.includes(tab)) tab = "control";
  document.querySelectorAll(".tab").forEach((b) =>
    b.classList.toggle("active", b.dataset.tab === tab));
  document.querySelectorAll(".tab-pane").forEach((p) =>
    p.classList.toggle("active", p.dataset.pane === tab));
  // 솔로(단일탭) 창은 저장 안 함 — 메인 창의 다음 로드 탭을 바꾸지 않도록
  if (!document.body.classList.contains("solo")) {
    try { localStorage.setItem(ACTIVE_TAB_KEY, tab); } catch (e) { /* noop */ }
  }
  if (PROTO_TABS.has(tab) && typeof GridStack !== "undefined") ensureGridStack(tab);
  else ensureGrid(tab);            // 표시된 뒤에야 폭을 측정할 수 있다
  if (tab === "system") refreshDevices();
  if (typeof updateLogDock === "function") updateLogDock();   // 시스템 탭이면 독 숨김
  relayoutAfter(tab);
}

function initWorkspace() {
  const params = new URLSearchParams(location.search);
  document.querySelectorAll(".tab").forEach((b) => {
    b.onclick = () => showTab(b.dataset.tab);
  });
  // 듀얼모니터: ?tab=<탭>으로 특정 탭만, ?solo=1이면 탭바 숨겨 단일탭 전용 창
  const urlTab = params.get("tab");
  if (params.has("solo")) document.body.classList.add("solo");
  let active = "devices";   // 관제 탭은 빈 캔버스 → 첫 진입은 장비 탭(MCT 콕핏)
  if (urlTab && TABS.includes(urlTab)) {
    active = urlTab;
  } else {
    try { active = localStorage.getItem(ACTIVE_TAB_KEY) || "devices"; } catch (e) { /* noop */ }
  }
  showTab(active);
}

// ---------- 관제 탭: 다른 패널 native DOM 복제 ----------
// 사용자 결정: 관제 탭만 자유 구성하되, 같은 패널을 홈 탭과 관제에 *동시에* 띄움(복제식).
// 복제는 native cloneNode — 원본 카드를 복제하고 id를 제거(전역 id 충돌 회피)한다.
// 단일 WS가 원본을 갱신 → syncClones()가 복제본 텍스트를, draw 함수들(.js-c-* 클래스로
// 멀티 인스턴스)이 복제본 캔버스를 그린다. 복제본은 읽기전용(입력/버튼 비활성) 모니터링 뷰.
// 미러 타일은 data-panel="mirror:<key>"라 기존 순서/저장(saveLayout/saveGSLayout)을 그대로 탄다.
const MIRRORS_KEY = "asterion.control.mirrors.v3";   // v3 — 잔존 미러 폐기(빈 캔버스 시작)
// 관제 탭 첫 실행 기본 구성 — 장비 콕핏을 미러로 시드(빈 화면 방지). 사용자가 ✕/추가하면
// 그 시점부터 저장값을 따른다. 장비 패널의 진짜 홈은 '장비' 탭(네이티브, 빠름)이다.
const DEFAULT_CONTROL_MIRRORS = [];   // 빈 캔버스 시작 — 사용자가 ＋ 패널로 직접 구성

function mirrorList() {
  const raw = localStorage.getItem(MIRRORS_KEY);
  if (raw === null) return DEFAULT_CONTROL_MIRRORS.slice();   // 미설정 = 첫 실행 → 기본 콕핏
  try { return JSON.parse(raw) || []; } catch (e) { return []; }
}
function saveMirrors(keys) {
  try { localStorage.setItem(MIRRORS_KEY, JSON.stringify(keys)); } catch (e) { /* noop */ }
}

// 미러로 가져올 수 있는 패널 목록을 DOM에서 1회 수집 (key→{title, homeTab}).
// 관제 탭 자신의 네이티브 패널은 이미 있으므로 제외한다.
let PANEL_REGISTRY = null;
function panelRegistry() {
  if (PANEL_REGISTRY) return PANEL_REGISTRY;
  PANEL_REGISTRY = {};
  document.querySelectorAll(".tab-pane[data-pane] [data-panel]").forEach((el) => {
    const pane = el.closest(".tab-pane")?.dataset.pane;
    const key = el.dataset.panel;
    if (!key || key.startsWith("mirror:") || pane === "control") return;
    PANEL_REGISTRY[key] = {
      title: el.querySelector(".card-title")?.textContent?.trim() || key,
      tag: el.querySelector(".card-tag:not(.conn)")?.textContent?.trim() || "",   // 네이티브 태그(ALL-SKY 등)
      homeTab: pane,
    };
  });
  return PANEL_REGISTRY;
}

// 원본(다른 탭) 패널 요소 — 복제 소스. applyStatus가 늘 이걸 갱신하므로 살아있다.
function findOriginalPanel(key) {
  return document.querySelector(`.tab-pane:not([data-pane="control"]) [data-panel="${key}"]`);
}

// 미러 타일 = 원본 카드의 native DOM 복제 (iframe 아님). id 제거로 전역 id 충돌 회피,
// 캔버스 클래스(js-c-*)는 유지되어 draw 함수가 복제본에도 그린다. 입력/버튼은 비활성(읽기전용 모니터링).
function makeMirrorTile(key) {
  const el = document.createElement("div");
  el.className = "muuri-item w6 panel-mirror";
  el.dataset.panel = `mirror:${key}`;
  el.dataset.mirror = key;
  const orig = findOriginalPanel(key);
  if (!orig) {
    el.innerHTML = `<div class="muuri-item-content card"><div class="card-head"><span class="card-title">${escapeHtml(key)}</span></div></div>`;
    return el;
  }
  const card = orig.querySelector(".card").cloneNode(true);
  card.querySelectorAll("[id]").forEach((n) => n.removeAttribute("id"));   // 전역 id 제거(충돌 방지)
  card.removeAttribute("id");
  card.classList.remove("grid-stack-item-content");
  card.classList.add("muuri-item-content");
  card.querySelectorAll("input, select, button, textarea").forEach((n) => { n.disabled = true; n.tabIndex = -1; });  // 읽기전용
  el.appendChild(card);
  return el;
}

// 복제본의 텍스트/클래스/입력값을 원본에서 매 틱 동기화 (캔버스는 draw 함수가 직접 그림).
function syncClones() {
  document.querySelectorAll('#grid-control .panel-mirror').forEach((el) => {
    const orig = findOriginalPanel(el.dataset.mirror);
    const oCard = orig && orig.querySelector(".card"), cCard = el.querySelector(".card");
    if (!oCard || !cCard) return;
    const o = oCard.querySelectorAll("*");
    const c = [...cCard.querySelectorAll("*")].filter((n) => !n.classList.contains("pt-mirror-x"));  // 우리가 단 ✕ 제외
    if (o.length !== c.length) return;   // 구조 변동(메뉴 토글 등) → 이번 틱 건너뜀
    for (let i = 0; i < o.length; i++) {
      if (o[i].tagName === "CANVAS") continue;   // 캔버스는 draw 함수가 모든 인스턴스에 그림
      if (!o[i].children.length && c[i].textContent !== o[i].textContent) c[i].textContent = o[i].textContent;
      if (c[i].className !== o[i].className) c[i].className = o[i].className;   // 상태색 등
      if ("value" in o[i] && c[i].value !== o[i].value) c[i].value = o[i].value;
    }
  });
}

// 저장된 미러 타일을 그리드 컨테이너에 미리 삽입 (Muuri 생성 전 호출 → 네이티브 아이템처럼 흡수).
function injectMirrors(container) {
  mirrorList().forEach((key) => {
    if (!container.querySelector(`.muuri-item[data-mirror="${key}"]`))
      container.appendChild(makeMirrorTile(key));
  });
}

// 미러 타일 헤더에 ✕(제거) 버튼을 단다 (addPanelTools가 만든 panel-tools 앞).
function wireMirrorRemove(el, tab) {
  if (!el.classList.contains("panel-mirror")) return;
  const head = el.querySelector(".card-head");
  if (head.querySelector(".pt-mirror-x")) return;
  const x = document.createElement("button");
  x.className = "pt-btn pt-mirror-x"; x.title = "미러 제거"; x.textContent = "✕";
  const tools = head.querySelector(".panel-tools");
  if (tools) tools.insertBefore(x, tools.firstChild); else head.appendChild(x);
  x.onclick = (e) => {
    e.stopPropagation();
    const key = el.dataset.mirror, grid = grids[tab];
    saveMirrors(mirrorList().filter((k) => k !== key));
    const item = grid && grid.getItems().find((it) => it.getElement() === el);
    if (item) grid.remove([item], { removeElements: true, layout: true });
    saveLayout(tab); relayoutAfter(tab);
  };
}

// 미러 타일을 Gridstack 위젯 구조로 변환 (muuri-item → grid-stack-item).
function gsifyMirror(el) {
  const key = el.dataset.mirror;
  const sp = PANEL_DEF[key] || {};
  const og = PROTO_GS_LAYOUT[key];   // 원본(device) 박스 = 복제 콘텐츠 동일 → 같은 크기가 맞음(클리핑 없음)
  el.classList.remove("muuri-item", "w6");
  el.classList.add("grid-stack-item");
  el.setAttribute("gs-id", el.dataset.panel);
  el.setAttribute("gs-w", (og && og.w) || sp.defW || 6);
  el.setAttribute("gs-h", (og && og.h) || sp.defH || 8);
  el.setAttribute("gs-min-w", sp.minW || 3);
  el.setAttribute("gs-min-h", sp.minH || 3);
  if (sp.maxW) el.setAttribute("gs-max-w", sp.maxW);
  if (sp.maxH) el.setAttribute("gs-max-h", sp.maxH);
  const c = el.querySelector(":scope > .muuri-item-content");
  if (c) { c.classList.remove("muuri-item-content"); c.classList.add("grid-stack-item-content"); }
}

// (미러 자동높이 제거됨) — iframe ResizeObserver→postMessage→gs.update 가 viz-fill과 물려
// 피드백 루프(패널이 혼자 커짐)를 만들어 폐기. 미러 크기는 gsifyMirror의 PANEL_DEF.defH로 고정,
// 돔/게이지는 viz-fill로 그 고정 박스를 채운다 → 루프 없이 안정.

// Gridstack 미러 타일에 ✕(제거) 버튼.
function wireGSMirrorRemove(el, tab) {
  if (!el.classList.contains("panel-mirror")) return;
  const head = el.querySelector(".card-head");
  if (!head || head.querySelector(".pt-mirror-x")) return;
  const x = document.createElement("button");
  x.className = "pt-btn pt-mirror-x"; x.title = "미러 제거"; x.textContent = "✕";
  head.appendChild(x);
  x.onclick = (e) => {
    e.stopPropagation();
    const key = el.dataset.mirror, gs = gstacks[tab];
    saveMirrors(mirrorList().filter((k) => k !== key));
    if (gs) gs.removeWidget(el, true);
    if (gs) saveGSLayout(tab, gs);
  };
}

// 팔레트에서 패널을 골라 관제 탭에 미러 추가 (Gridstack/Muuri 양쪽 지원)
function addMirror(tab, key) {
  if (mirrorList().includes(key)) return;
  const gs = gstacks[tab];
  if (gs) {                                   // Gridstack(MCT) 경로
    const el = makeMirrorTile(key);
    gsifyMirror(el);
    document.getElementById(`grid-${tab}`).appendChild(el);
    gs.makeWidget(el);
    wireGSMirrorRemove(el, tab);
    saveMirrors([...mirrorList(), key]);
    saveGSLayout(tab, gs);
    if (lastStatus) applyStatus(lastStatus);
    return;
  }
  const grid = grids[tab];                    // Muuri(구) 경로
  if (!grid) return;
  const el = makeMirrorTile(key);
  document.getElementById(`grid-${tab}`).appendChild(el);
  grid.add(el, { layout: false });
  addPanelTools(el, tab);
  wireMirrorRemove(el, tab);
  saveMirrors([...mirrorList(), key]);
  saveLayout(tab);
  grid.refreshItems().layout(true);
  relayoutAfter(tab);
}

// '+ 패널' 드롭다운 — 아직 미러하지 않은 패널을 홈 탭과 함께 나열
function openPanelPalette(tab, anchor) {
  document.querySelector(".panel-palette")?.remove();
  const have = new Set(mirrorList());
  // 탭별로 그룹화 — 탭 헤더 아래 그 탭의 패널들
  const TAB_LABEL = { control: "관제", devices: "장비", env: "기상", plan: "계획", analysis: "분석", system: "시스템" };
  const byTab = {};
  Object.entries(panelRegistry()).forEach(([k, v]) => {
    if (have.has(k)) return;
    (byTab[v.homeTab] = byTab[v.homeTab] || []).push([k, v]);
  });
  const menu = document.createElement("div");
  menu.className = "panel-palette";
  let html = "";
  TABS.forEach((t) => {
    const list = byTab[t];
    if (!list || !list.length) return;
    html += `<button class="pp-group" data-grp="${t}"><span class="pp-chev">▸</span>` +
      `<span class="pp-glabel">${TAB_LABEL[t] || t}</span><span class="pp-count">${list.length}</span></button>`;
    html += `<div class="pp-items" data-grp="${t}" hidden>` +
      list.map(([k, v]) => `<button class="pp-item" data-key="${k}">${escapeHtml(v.title)}</button>`).join("") +
      `</div>`;
  });
  menu.innerHTML = html || `<div class="pp-empty">추가할 패널이 없습니다</div>`;
  document.body.appendChild(menu);
  // 위치: ＋패널 버튼 아래, 화면 밖으로 안 나가게 (좌우)
  const r = anchor.getBoundingClientRect();
  menu.style.top = `${r.bottom + 4}px`;
  menu.style.left = `${Math.max(6, Math.min(r.left, window.innerWidth - menu.offsetWidth - 8))}px`;
  // 탭 그룹 접기/펴기 (기본 접힘 → 원하는 탭만 펴서 본다)
  menu.querySelectorAll(".pp-group").forEach((g) => {
    g.onclick = () => {
      const box = menu.querySelector(`.pp-items[data-grp="${g.dataset.grp}"]`);
      const opening = box.hidden;
      box.hidden = !opening;
      g.querySelector(".pp-chev").textContent = opening ? "▾" : "▸";
    };
  });
  menu.querySelectorAll(".pp-item").forEach((b) =>
    (b.onclick = () => { addMirror(tab, b.dataset.key); menu.remove(); }));
  setTimeout(() => {
    const close = (ev) => {
      if (!menu.contains(ev.target) && ev.target !== anchor) {
        menu.remove(); document.removeEventListener("mousedown", close);
      }
    };
    document.addEventListener("mousedown", close);
  }, 0);
}

// ---------- 시스템 탭: 장비 연결 (ASCOM / PWI4) ----------

let deviceConfig = null;     // /api/system/devices 결과
const ascomCache = {};       // device key -> [{progid, name}]

async function refreshDevices() {
  try {
    deviceConfig = await (await fetch("/api/system/devices")).json();
    renderConnList();
    applyFocuserNudge();   // 포커서 스텝 프리셋 반영
  } catch (e) { /* noop */ }
}

// 시스템 자원 — 버전·업타임·디스크 여유(이미지 저장용). /api/sysinfo 주기 폴링.
async function refreshSysinfo() {
  try {
    const d = await (await fetch("/api/sysinfo")).json();
    const set = (id, v) => { const el = $(id); if (el) el.textContent = v; };
    set("sys-ver", "v" + d.version);
    const u = d.uptime_s || 0, h = Math.floor(u / 3600), m = Math.floor((u % 3600) / 60);
    set("sys-uptime", h > 0 ? `${h}h ${m}m` : `${m}m`);
    if (d.disk) {
      set("sys-disk", `${d.disk.free_gb} GB · 사용 ${d.disk.used_pct}%`);
      const bar = $("sys-disk-bar");
      if (bar) {
        bar.style.width = d.disk.used_pct + "%";
        bar.style.background = d.disk.used_pct > 90 ? "var(--err)"
          : d.disk.used_pct > 80 ? "var(--warn)" : "var(--ok)";
      }
    }
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
  if (!cfg) {
    if (dev.config_kind === "auto")
      cfg = `<div class="conn-dev-cfg"><span class="cfg-lbl">자동 연결 — SDK·장비 자동 탐색 (설정 불필요)</span></div>`;
    else
      cfg = `<div class="conn-dev-cfg"><span class="cfg-lbl">시뮬레이터 전용 — 설정 없음</span></div>`;
  }
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
      <span class="cd-name" data-role="name" title="장비명"></span>
      <span class="cd-state off" data-role="state">미연결</span>
      ${SETUP_SCHEMA[dev.key] ? `<button class="cd-setup" data-act="setup-open" data-dev="${dev.key}" title="Setup">⚙</button>` : ""}
    </div>
    <div class="devmode-only">${backend}${cfg}</div>
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
    // 마스터가 sim이어도 운영자가 고른 REAL 백엔드를 보인다(dev.selected).
    bsel.value = dev.selected || "sim";
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

const deviceActionsPending = new Set();

async function deviceAction(key, act, root) {
  if (act === "setup-open") { openSetupDrawer(key); return; }
  if (deviceActionsPending.has(key)) return;
  deviceActionsPending.add(key);
  const controls = [...root.querySelectorAll("button, select, input")].map(
    (el) => [el, el.disabled],
  );
  controls.forEach(([el]) => {
    el.disabled = true;
  });
  try {
    if (act === "save") {
      const psel = root.querySelector('[data-cfg="progid"]');
      const uinp = root.querySelector('[data-cfg="url"]');
      const body = {};
      if (psel) body.progid = psel.value;
      if (uinp) body.url = uinp.value.trim();
      await saveDeviceCfg(key, body);
    } else if (act === "setup") {
      await post("/api/system/setup", { device: key });
      await refreshDevices();
    } else {
      deviceConfig = await post(`/api/system/${act}`, { device: key });
      renderConnList();
    }
  } catch (e) { /* post()가 이미 로그 */ }
  finally {
    deviceActionsPending.delete(key);
    controls.forEach(([el, wasDisabled]) => {
      if (el.isConnected) el.disabled = wasDisabled;
    });
    renderConnLive();
  }
}

function renderConnList() {
  const host = $("conn-list");
  if (!host || !deviceConfig) return;
  host.innerHTML = deviceConfig.devices.map(connDevHtml).join("");
  deviceConfig.devices.forEach(wireConnDev);
  renderConnLive();
  // 마스터 모드 세그먼트 활성 표시 (Real/Sim)
  document.querySelectorAll("#master-seg .seg-btn").forEach((b) =>
    b.classList.toggle("active", b.dataset.mode === deviceConfig.master_mode));
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
    else if (dev.key === "mount" && d.stale) {
      st.textContent = "좌표 갱신 정지"; st.className = "cd-state warn";
    } else if (dev.key === "filterwheel" && d.moving) {
      st.textContent = "초기화/이동 중"; st.className = "cd-state warn";
    }
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
  if (key === "mount") return !d.stale &&
    (d.alt != null || d.az != null || d.ra_hours != null);
  if (key === "weather") return d.temp != null;
  if (key === "focuser") return d.position != null;
  return true;  // 카메라/필터휠 등은 connected로 충분
}

// ---------- 디바이스 Setup 드로어 (System 탭 ⚙) ----------
// per-device 깊이 설정(필터표·게인·MaxΔT·jog 등). 값=config.local.json setup.{key}.*
// (POST /api/system/setup-config), 능력/범위=describe().caps(connect 시 1회). 폴백 우선이라
// 백엔드 caps가 없어도 라이브 필터명/기본값으로 동작한다.

const SETUP_SCHEMA = {
  filterwheel: { title: "필터휠", kind: "filters" },
  camera: { title: "카메라", kind: "fields", fields: [
    { key: "gain", label: "Gain", type: "number", capsRange: ["gain_min", "gain_max"] },
    { key: "offset", label: "Offset", type: "number", capsRange: ["offset_min", "offset_max"] },
    { key: "default_setpoint_c", label: "기본 Setpoint (°C)", type: "number", capsGate: "can_set_ccd_temperature" },
    { key: "max_dt_c", label: "Max ΔT (°C/min)", type: "number" },
    { key: "readout_mode", label: "Readout mode", type: "select", capsOptions: "readout_modes" },
  ] },
  focuser: { title: "포커서", kind: "fields", fields: [
    { key: "step_presets", label: "Step presets (쉼표로 구분)", type: "csv", placeholder: "10, 100, 1000" },
    { key: "backlash", label: "Backlash", type: "number" },
  ] },
  mount: { title: "마운트", kind: "fields", fields: [
    { key: "jog_mode", label: "Jog 모드", type: "text", placeholder: "rate | step" },
  ] },
};

function ensureSetupDrawer() {
  if ($("setup-drawer")) return;
  const ov = document.createElement("div");
  ov.className = "drawer-overlay"; ov.id = "setup-overlay";
  ov.onclick = () => openSetupDrawer(null);
  const dr = document.createElement("aside");
  dr.className = "drawer"; dr.id = "setup-drawer";
  dr.innerHTML =
    '<div class="drawer-head"><span id="setup-title">디바이스 Setup</span>' +
    '<button class="x" id="setup-close">✕</button></div>' +
    '<div id="setup-body"></div>';
  document.body.append(ov, dr);
  dr.querySelector("#setup-close").onclick = () => openSetupDrawer(null);
}

function openSetupDrawer(key) {
  ensureSetupDrawer();
  const open = !!key;
  if (open) renderSetupBody(key);
  $("setup-drawer").classList.toggle("open", open);
  $("setup-overlay").classList.toggle("open", open);
}

// 필터 행 해석: setup.filters → caps(names+offsets) → 라이브 status.names → []
function resolveFilters(dev, live) {
  const s = dev.setup && dev.setup.filters;
  if (Array.isArray(s) && s.length)
    return s.map((f) => ({ name: f.name || "", focus_offset: +f.focus_offset || 0,
                           flat_factor: f.flat_factor != null ? +f.flat_factor : 1 }));
  const caps = dev.caps || {};
  const names = caps.names || (live && live.names) || [];
  const offs = caps.focus_offsets || [];
  return names.map((n, i) => ({ name: n, focus_offset: +offs[i] || 0, flat_factor: 1 }));
}

function renderSetupBody(key) {
  const dev = ((deviceConfig && deviceConfig.devices) || []).find((d) => d.key === key);
  const schema = SETUP_SCHEMA[key];
  const body = $("setup-body");
  if (!body) return;
  if (!dev || !schema) {
    $("setup-title").textContent = "디바이스 Setup";
    body.innerHTML = '<div class="drawer-sec"><div class="drawer-note">이 장비는 Setup 항목이 없습니다.</div></div>';
    return;
  }
  const live = (lastStatus && lastStatus[snapKeyFor(key)]) || {};
  const connected = !!live.connected;
  const src = (dev.caps && dev.caps.names) ? "드라이버에서 읽음"
            : (dev.setup && Object.keys(dev.setup).length) ? "저장된 설정"
            : connected ? "라이브" : "기본값";
  $("setup-title").textContent = schema.title + " Setup";

  let html = '<div class="setup-id"><span class="cd-dot ' + (connected ? "on" : "off") + '"></span>' +
    "<b>" + escapeHtml(live.device_name || live.name || dev.label) + "</b>" +
    '<span class="setup-src">' + src + "</span></div>";

  if (schema.kind === "filters") {
    const rows = resolveFilters(dev, live);
    html += '<div class="drawer-sec"><div class="drawer-label">필터 슬롯</div>' +
      '<div class="setup-tbl" id="setup-filters">' +
      '<div class="sth">Pos</div><div class="sth">필터명</div><div class="sth">Focus Off</div><div class="sth">Flat ×</div>';
    if (!rows.length)
      html += '<div class="spos">—</div><div class="drawer-note" style="grid-column:2/5;margin:0">연결 후 드라이버에서 필터를 읽어옵니다.</div>';
    rows.forEach((r, i) => {
      html += '<div class="spos">' + (i + 1) + "</div>" +
        '<input class="sf-name" value="' + escapeHtml(r.name) + '">' +
        '<input class="sf-off" type="number" value="' + r.focus_offset + '">' +
        '<input class="sf-flat" type="number" step="0.1" value="' + r.flat_factor + '">';
    });
    html += '</div><div class="drawer-note">필터명·Focus Offset은 연결된 드라이버에서 읽어옵니다. 오토플랫·카메라가 이 표를 소비합니다.</div></div>';
  } else {
    const caps = dev.caps || {};
    const capsKnown = Object.keys(caps).length > 0;
    html += '<div class="drawer-sec"><div class="setup-fields">';
    schema.fields.forEach((f) => {
      // 드라이버가 그 기능을 모르면 숨기지 말고 '비활성'(미지원 표시) — 투명성
      const gated = !!(f.capsGate && capsKnown && !caps[f.capsGate]);
      const dis = gated ? " disabled" : "";
      let v = dev.setup ? dev.setup[f.key] : undefined;
      if (f.type === "csv" && Array.isArray(v)) v = v.join(", ");
      let lab = escapeHtml(f.label);
      if (gated) lab += ' <span class="setup-na">미지원</span>';
      else if (f.capsRange && caps[f.capsRange[0]] != null)
        lab += ' <span class="setup-hint">(' + caps[f.capsRange[0]] + "–" + caps[f.capsRange[1]] + ")</span>";
      const opts = f.capsOptions && caps[f.capsOptions];
      let field;
      if (f.type === "select" && Array.isArray(opts) && opts.length) {
        field = '<select data-skey="' + f.key + '" data-stype="text"' + dis + ">" +
          opts.map((o) => "<option" + (String(v) === String(o) ? " selected" : "") +
            ">" + escapeHtml(String(o)) + "</option>").join("") + "</select>";
      } else {
        const num = f.type === "number";
        let ext = "";
        if (!gated && f.capsRange && caps[f.capsRange[0]] != null)
          ext = ' min="' + caps[f.capsRange[0]] + '" max="' + caps[f.capsRange[1]] + '"';
        field = '<input data-skey="' + f.key + '" data-stype="' + (num ? "number" : "text") + '"' +
          (num ? ' type="number"' : "") + ext + dis +
          ' value="' + (v != null ? escapeHtml(String(v)) : "") + '"' +
          ' placeholder="' + (f.placeholder || "") + '">';
      }
      html += '<label class="setup-field"><span>' + lab + "</span>" + field + "</label>";
    });
    html += "</div>";
    if (!capsKnown)
      html += '<div class="drawer-note">장비 연결 시 드라이버가 범위·드롭다운·지원여부를 채웁니다.</div>';
    html += "</div>";
  }

  html += '<div class="setup-foot"><button class="btn btn-go" id="setup-save">저장</button>';
  if (dev.config_kind === "progid")
    html += '<button class="btn" id="setup-ascom" title="드라이버 자체 설정창">ASCOM 드라이버 창</button>';
  html += '<span class="setup-path">setup.' + key + "</span></div>";
  body.innerHTML = html;

  $("setup-save").onclick = () => saveSetup(key, schema);
  const asc = $("setup-ascom");
  if (asc) asc.onclick = async () => { try { await post("/api/system/setup", { device: key }); } catch (e) { /* logged */ } };
}

async function saveSetup(key, schema) {
  let setup;
  if (schema.kind === "filters") {
    const tbl = $("setup-filters");
    const names = [...tbl.querySelectorAll(".sf-name")];
    const offs = [...tbl.querySelectorAll(".sf-off")];
    const flats = [...tbl.querySelectorAll(".sf-flat")];
    setup = { filters: names.map((n, i) => ({
      name: n.value.trim(), focus_offset: +offs[i].value || 0,
      flat_factor: +flats[i].value || 1 })).filter((f) => f.name) };
  } else {
    setup = {};
    $("setup-body").querySelectorAll("[data-skey]").forEach((el) => {
      if (el.disabled) return;   // 미지원(비활성) 칸은 저장 안 함
      const k = el.dataset.skey, t = el.dataset.stype, raw = el.value.trim();
      if (raw === "") return;
      if (t === "number") setup[k] = +raw;
      else if (t === "csv") setup[k] = raw.split(",").map((x) => +x.trim()).filter((x) => !isNaN(x));
      else setup[k] = raw;
    });
  }
  const btn = $("setup-save");
  if (btn) btn.disabled = true;
  try {
    deviceConfig = await post("/api/system/setup-config", { device: key, setup });
    renderSetupBody(key);
    if (key === "focuser") applyFocuserNudge();   // ± 넛지 버튼 즉시 갱신
  } catch (e) { if (btn) btn.disabled = false; }
}

// ---------- 상태 반영 ----------

let lastStatus = null;
let filterOptionsSignature = "";

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
  $("m-slew").textContent = m.homing ? "HOMING" : (m.stale ? "STALE" : "SLEWING");
  $("m-slew").title = m.detail || "";
  $("m-park").classList.toggle("on", !!m.at_park);
  const mountBusy = !m.connected || !!m.slewing || !!m.homing || !!m.stale;
  $("btn-goto").disabled = mountBusy;
  $("btn-goto-radec").disabled = mountBusy;
  $("btn-resolve").disabled = mountBusy;
  $("btn-tracking").disabled = !m.connected || !!m.homing || !!m.stale;
  $("btn-stop").disabled = !m.connected || (!m.slewing && !m.homing);
  // 조그 패드 — 연속 조그(MoveAxis) 능력으로 게이팅(미지원이면 비활성, 숨김 아님).
  // 조그 유지 중(mountJogDir)에는 버튼 상태를 건드리지 않는다 — 드라이버가 Slewing을
  // 띄워 mountBusy가 참이 돼도 '누르고 있는' 버튼이 풀려버리지 않게.
  if (!mountJogDir) {
    const mcaps = (((deviceConfig && deviceConfig.devices) || [])
      .find((d) => d.key === "mount") || {}).caps || {};
    const canPri = !!mcaps.can_move_axis_primary, canSec = !!mcaps.can_move_axis_secondary;
    document.querySelectorAll(".jog-pad [data-jog]").forEach((b) => {
      const sec = (b.dataset.jog === "N" || b.dataset.jog === "S");
      b.disabled = mountBusy || !(sec ? canSec : canPri);
    });
    document.querySelectorAll("#m-rate-seg .seg-btn").forEach((b) => {
      b.disabled = !(canPri || canSec);
    });
  }
  // 파킹/홈 — 드라이버가 지원할 때만 노출, 상태에 맞게 버튼 활성화
  const parkRow = $("park-row");
  if (parkRow) {
    // 숨기지 않고 비활성만 — 미지원/미연결이어도 버튼은 보이게(카메라 Setup과 동일 원칙)
    $("btn-park").disabled = !m.can_park || !!m.at_park || mountBusy;
    $("btn-unpark").disabled = !m.can_park || !m.at_park;
    $("btn-home").disabled = !m.can_home || !!m.at_park || mountBusy;
    $("btn-setpark").disabled = !m.can_park || mountBusy;
  }

  // 카메라 + 캡처
  const c = s.camera || {};
  $("c-temp").textContent = fmt(c.ccd_temp, 1, " ℃") +
    (c.cooler_on ? " ❄" : "");
  $("c-state").textContent = c.state || "—";
  // 쿨러 램프(Max ΔT) — 쿨다운/웜업 진행 중일 때만 노출
  const cool = s.cooler || {};
  const rampEl = $("c-cooler-ramp");
  if (rampEl) {
    if (cool.ramping) {
      const cmd = cool.commanded == null ? "—" : Number(cool.commanded).toFixed(1) + "°C";
      const tgt = cool.mode === "warming" ? "OFF"
        : (cool.target == null ? "—" : cool.target + "°C");
      const rate = cool.max_dt_c ? ` · ≤${cool.max_dt_c}°C/min` : "";
      rampEl.textContent =
        `❄ ${cool.mode === "warming" ? "웜업" : "냉각"} ${cmd} → ${tgt}${rate}`;
      rampEl.hidden = false;
    } else {
      rampEl.hidden = true;
    }
  }
  const f = s.filter || {};
  $("c-filter").textContent = f.moving ? "초기화/이동 중…" : (f.name || "—");
  const filterSignature = JSON.stringify(f.names || []);
  if (filterSignature !== filterOptionsSignature &&
      Array.isArray(f.names) && f.names.length) {
    $("sel-filter").innerHTML = f.names.map((n, i) =>
      `<option value="${i}">${n}</option>`).join("");
    filterOptionsSignature = filterSignature;
  }
  $("btn-filter").disabled = !f.connected || !!f.moving;
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
  $("f-moving").textContent = fo.moving ? "이동 중" : "정지";
  $("f-temp").textContent = fmt(fo.temperature, 1, " ℃");
  if (fo.position !== null && fo.position !== undefined && fo.max_position) {
    $("f-bar").style.width =
      clamp(fo.position / fo.max_position * 100, 0, 100) + "%";
  }
  // STOP/HOME 게이팅(미지원/미연결이면 비활성 — 숨기지 않음) + 위치 단위(steps/µm)
  const fcaps = (((deviceConfig && deviceConfig.devices) || [])
    .find((d) => d.key === "focuser") || {}).caps || {};
  $("btn-f-stop").disabled = !fo.connected || !fcaps.can_halt;
  $("btn-f-home").disabled = !fo.connected || !fcaps.can_home || !!fo.moving;
  const heroU = document.querySelector('[data-panel="focuser"] .hero-u');
  if (heroU && fcaps.unit) heroU.textContent = fcaps.unit;

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
  drawSpark("js-c-sp-temp", sparkBuf.temp, "#38bdf8");
  drawSpark("js-c-sp-hum", sparkBuf.hum, "#34d399");
  drawSpark("js-c-sp-wind", sparkBuf.wind, "#fbbf24");

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

  syncClones();   // 관제 탭 복제본에 원본 텍스트/상태 동기화 (캔버스는 draw 함수가 이미 그림)
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

// WS 메시지 한 건 처리 — 실제 WS와 (미러용) 부모 전달 postMessage가 공유한다.
function handleWSData(data) {
  if (data.type === "status") applyStatus(data.status);
  else if (data.type === "log") logLine(data);
  else if (data.type === "frame") prependRow("tbl-frames", frameRow(data.frame));
  else if (data.type === "action") prependRow("tbl-actions", actionRow(data.action));
  else if (data.type === "preview") updatePreview(data.token, data.meta);
}

function connectWS() {
  const ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onopen = () => $("ws-dot").classList.add("on");
  ws.onclose = () => {
    $("ws-dot").classList.remove("on");
    setTimeout(connectWS, 2000);
  };
  ws.onmessage = (ev) => handleWSData(JSON.parse(ev.data));
}
// (iframe 미러 제거됨) — 관제 탭 복제는 이제 native DOM 복제(makeMirrorTile→cloneNode)다.
// 단일 WS 한 개가 원본 패널을 갱신하고, syncClones()가 복제본 텍스트를, draw 함수들이
// 복제본 캔버스를 그린다. broadcastToMirrors/setupMirror*/isMirrorEmbed/mountPanelSolo 폐기.

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
  refreshDevices();   // 시작 시 디바이스 caps/setup 로드 (탭 무관 — 포커서 STOP/HOME 게이팅·단위 등)
  refreshSysinfo();
  setInterval(refreshSysinfo, 15000);   // 시스템 자원 15초 주기
  kickSky();                            // 돔 애니메이션 (필요할 때만)
}

// ---------- 버튼 핸들러 ----------

// 망원경
$("btn-goto").onclick = () => {
  const alt = Number($("in-alt").value), az = Number($("in-az").value);
  if (Number.isNaN(alt) || $("in-alt").value === "") return;
  $("btn-goto").disabled = true;
  skyTarget = { alt, az: az % 360, ts: Date.now() };
  kickSky();
  post("/api/actions/mount/goto", { alt, az }).catch(() => {
    skyTarget = null;
    $("btn-goto").disabled = false;
  });
};
$("btn-goto-radec").onclick = () => {
  const ra = $("in-ra").value.trim(), dec = $("in-dec").value.trim();
  if (!ra || !dec) return;
  $("btn-goto-radec").disabled = true;
  post("/api/actions/mount/goto_radec", { ra, dec }).catch(() => {
    $("btn-goto-radec").disabled = false;
  });
};
$("btn-resolve").onclick = async () => {
  const name = $("in-target").value.trim();
  if (!name) return;
  const line = $("resolve-line");
  line.style.color = "";
  line.textContent = "검색 중…";
  $("btn-resolve").disabled = true;
  try {
    const r = await fetch(`/api/resolve?name=${encodeURIComponent(name)}`);
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
    const d = await r.json();
    $("in-ra").value = d.ra_str;
    $("in-dec").value = d.dec_str;
    line.textContent = `${name} → ${d.ra_str} ${d.dec_str} · 이동`;
    fetchTrack(d.ra_str, d.dec_str, name);  // 타임라인에 고도 곡선
    await post("/api/actions/mount/goto_radec", { ra: d.ra_str, dec: d.dec_str });  // 검색→바로 이동
  } catch (e) {
    line.style.color = "var(--err)";
    line.textContent = `해석 실패: ${e.message}`;
  } finally {
    $("btn-resolve").disabled = false;
  }
};
$("in-target").addEventListener("keydown", (e) => {
  if (e.key === "Enter") $("btn-resolve").click();
});
// 조준 세그먼트(대상 / RA·Dec / Alt·Az) — 활성 모드의 입력만 표시
document.querySelectorAll("#goto-seg .seg-btn").forEach((b) => {
  b.onclick = () => {
    document.querySelectorAll("#goto-seg .seg-btn").forEach((x) => x.classList.toggle("active", x === b));
    document.querySelectorAll(".goto-mode").forEach((m) => { m.hidden = m.dataset.gotoMode !== b.dataset.goto; });
  };
});
// 가대 조그 — PWI4식 연속(velocity) 조그. 버튼을 유지하는 동안 축을 속도 슬루
// (MoveAxis)하고, 떼면 정지. 방향→축 매핑은 서버가 단일 소스로 잡고(N/S=secondary,
// E/W=primary), 클라는 N/S/E/W + rate만 보낸다. 안전: pointerup/leave/cancel·창 blur·
// 탭 종료(pagehide)·■ STOP가 모두 정지로 수렴 + 서버 데드맨(keepalive 끊기면 자동정지).
let mountJogRate = "normal";
let mountJogTimer = null;   // 데드맨 keepalive 인터벌
let mountJogDir = null;     // 현재 유지 중인 방향(N/S/E/W) | null
function stopMountJog() {
  if (mountJogTimer) { clearInterval(mountJogTimer); mountJogTimer = null; }
  document.querySelectorAll(".jog-pad [data-jog].held").forEach((b) => b.classList.remove("held"));
  if (mountJogDir !== null) { post("/api/actions/mount/jog_stop"); mountJogDir = null; }
}
function applyMountJog() {
  document.querySelectorAll(".jog-pad [data-jog]").forEach((b) => {
    b.addEventListener("pointerdown", (e) => {
      if (b.disabled) return;
      e.preventDefault();
      stopMountJog();
      b.classList.add("held");
      mountJogDir = b.dataset.jog;
      post("/api/actions/mount/move_axis", { direction: mountJogDir, rate: mountJogRate });
      mountJogTimer = setInterval(                       // 데드맨 재무장 (서버 1.6s / 여기 0.5s)
        () => post("/api/actions/mount/jog_keepalive"), 500);
    });
    ["pointerup", "pointerleave", "pointercancel"].forEach((ev) =>
      b.addEventListener(ev, stopMountJog));
  });
}
applyMountJog();
window.addEventListener("blur", stopMountJog);            // 창 포커스 잃으면 안전 정지
window.addEventListener("pagehide", () => {               // 탭 종료/이동
  if (mountJogDir !== null && navigator.sendBeacon)
    navigator.sendBeacon("/api/actions/mount/jog_stop");  // 언로드 중 신뢰 가능한 정지 신호
  stopMountJog();                                         // 로컬 상태(타이머/.held/dir) 정리
});
// 조그 속도(rate) — 패널에서 조절 (서버가 가대 caps로 deg/s 해석)
document.querySelectorAll("#m-rate-seg .seg-btn").forEach((b) => {
  b.onclick = () => {
    mountJogRate = b.dataset.rate;
    document.querySelectorAll("#m-rate-seg .seg-btn").forEach((x) => x.classList.toggle("active", x === b));
    if (mountJogDir !== null)   // 유지 중 속도 변경 → 즉시 새 속도로 재명령
      post("/api/actions/mount/move_axis", { direction: mountJogDir, rate: mountJogRate });
  };
});
$("btn-tracking").onclick = () =>
  post("/api/actions/mount/tracking", { on: !(lastStatus?.mount?.tracking) });
$("btn-stop").onclick = () => { stopMountJog(); skyTarget = null; post("/api/actions/mount/stop"); };
$("btn-park").onclick = () => { skyTarget = null; post("/api/actions/mount/park"); };
$("btn-unpark").onclick = () => post("/api/actions/mount/unpark");
$("btn-home").onclick = () => {
  skyTarget = null;
  $("btn-home").disabled = true;
  post("/api/actions/mount/home").catch(() => { $("btn-home").disabled = false; });
};
$("btn-setpark").onclick = () => post("/api/actions/mount/setpark");

// 하늘 돔 클릭
$("sky-canvas").addEventListener("click", skyClickHandler);
// 돔 방위 반전 버튼 (E↔W, N↔S)
[["btn-flip-ew", "ew"], ["btn-flip-ns", "ns"]].forEach(([id, key]) => {
  const b = $(id); if (!b) return;
  b.classList.toggle("on", !!skyFlip[key]);
  b.onclick = () => {
    skyFlip[key] = !skyFlip[key];
    b.classList.toggle("on", !!skyFlip[key]);
    saveSkyFlip();
    if (lastStatus) drawSky(lastStatus);
  };
});

// 카메라 / 캡처
$("btn-filter").onclick = () => {
  $("btn-filter").disabled = true;
  post("/api/actions/filter", {
    position: Number($("sel-filter").value),
  }).catch(() => { $("btn-filter").disabled = false; });
};
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
$("btn-f-stop").onclick = () => { stopFocHold(); post("/api/actions/focuser/stop"); };  // 유지 정지 + 이동 중단
$("btn-f-home").onclick = () => post("/api/actions/focuser/home");
// 포커서 ± 넛지 — setup.focuser.step_presets로 버튼 생성(기본 100·1000). PWI3식 동작:
// 탭=1회 이동, 누르고 유지=속도(rate)대로 반복 이동, 유지하는 동안 버튼이 '눌린 상태'로 보임.
let focuserRateMs = 180;   // 유지 시 반복 간격 (느림/보통/빠름)
let focHoldTimer = null;
function stopFocHold() {
  if (focHoldTimer) { clearInterval(focHoldTimer); focHoldTimer = null; }
  document.querySelectorAll("#f-nudge .held").forEach((b) => b.classList.remove("held"));
}
function applyFocuserNudge() {
  const host = $("f-nudge");
  if (!host) return;
  let presets = [100, 1000];
  const dev = ((deviceConfig && deviceConfig.devices) || []).find((d) => d.key === "focuser");
  const sp = dev && dev.setup && dev.setup.step_presets;
  if (Array.isArray(sp) && sp.length)
    presets = sp.map(Number).filter((n) => n > 0).sort((a, b) => a - b);
  const deltas = [...presets].reverse().map((p) => -p).concat(presets);
  host.innerHTML = deltas.map((d) =>
    '<button class="btn" data-fn="' + d + '">' + (d > 0 ? "+" : "−") + Math.abs(d) + "</button>").join("");
  host.querySelectorAll("[data-fn]").forEach((b) => {
    const delta = Number(b.dataset.fn);
    b.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      stopFocHold();
      b.classList.add("held");
      post("/api/actions/focuser/nudge", { delta });                                  // 즉시 1회(=탭)
      focHoldTimer = setInterval(                                                       // 유지=반복 이동
        () => post("/api/actions/focuser/nudge", { delta }), focuserRateMs);
    });
    ["pointerup", "pointerleave", "pointercancel"].forEach((ev) =>
      b.addEventListener(ev, stopFocHold));
  });
}
applyFocuserNudge();
window.addEventListener("blur", stopFocHold);   // 창 포커스 잃으면 안전 정지
// 유지 반복 속도(rate) — 패널에서 조절
document.querySelectorAll("#f-rate-seg .seg-btn").forEach((b) => {
  b.onclick = () => {
    focuserRateMs = Number(b.dataset.rate);
    document.querySelectorAll("#f-rate-seg .seg-btn").forEach((x) => x.classList.toggle("active", x === b));
  };
});

// 시스템 탭 — 마스터 모드(Sim/Real) + 전체 연결/해제 (운영자 노출, ACI식)
const sysReload = async (url, body) => {
  try { await post(url, body || {}); await refreshDevices(); } catch (e) { /* logged */ }
};
$("btn-connect-all").onclick = () => sysReload("/api/system/connect-all");
$("btn-disconnect-all").onclick = () => sysReload("/api/system/disconnect-all");
document.querySelectorAll("#master-seg .seg-btn").forEach((b) => {
  b.onclick = () => sysReload("/api/dev/mode", { mode: b.dataset.mode });
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
function updateLogDock() {
  const dock = $("log-dock"); if (!dock) return;
  const sys = currentTab() === "system";        // 시스템 탭엔 로그가 패널로 있으니 독 숨김
  dock.style.display = sys ? "none" : "";
  const open = !dock.classList.contains("collapsed");
  // 펼치면 콘텐츠가 독 위로 스크롤되도록 body 하단 여백 확보 (안 가리게)
  document.body.style.paddingBottom = sys ? "0px" : (open ? "286px" : "38px");
}
function setLogDock(open) {
  const dock = $("log-dock"); if (!dock) return;
  dock.classList.toggle("collapsed", !open);
  try { localStorage.setItem(LOGDOCK_KEY, open ? "1" : "0"); } catch (e) { /* noop */ }
  updateLogDock();
  if (open) { const l = $("log"); if (l) l.scrollTop = l.scrollHeight; }
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
