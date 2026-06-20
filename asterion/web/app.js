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
function radecToAltaz(raH, dec, lat, lstH) {   // 목표 마커가 하늘과 함께 움직이게
  let ha = (lstH - raH) * 15;
  ha = (((ha + 180) % 360) + 360) % 360 - 180;
  const h = ha * D2R, d = dec * D2R, p = lat * D2R;
  const sinAlt = Math.sin(d) * Math.sin(p) + Math.cos(d) * Math.cos(p) * Math.cos(h);
  const alt = Math.asin(clamp(sinAlt, -1, 1));
  const cosAz = (Math.sin(d) - Math.sin(alt) * Math.sin(p)) /
                Math.max(1e-9, Math.cos(alt) * Math.cos(p));
  let az = Math.acos(clamp(cosAz, -1, 1));
  if (Math.sin(h) > 0) az = TAU - az;
  return [alt / D2R, az / D2R];
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

// {alt, az, ra, dec, name, kind, state} — 클릭/선택한 목표.
// state: proposed(주황·제안) → slewing(초록·이동) → tracking(파랑·추종)
let skyTarget = null;
let skyGeom = null;     // {cx, cy, R} — 마지막 그리기 기하 (클릭 역변환용)
// 조향(steerable) 뷰 — 하늘 자체를 조향: 시야중심(보는 방향 alt/az) + 시야각(fov°) + 롤(rad).
// 드래그=중심 이동(재투영), 휠=fov, Shift+드래그=롤. 기본=천정 중심·fov 180°(=올스카이).
// 기본 = 천정 살짝 아래(89°)·남향 — 정확한 천정(특이점)을 피해 기저가 항상 연속이라
// 줌/조향해도 방위(NSEW)가 뒤집히지 않는다. (89°,180°가 N-위·E-오른쪽으로 나옴.)
// fovRaw = 미클램프 줌 누산기(휠이 여기에 쌓임); fov = 렌더용 clamp(fovRaw,4,220).
// 둘로 나눈 이유: fov를 직접 클램프하면 경계(220/4)에 박힌 만큼 잃어 줌아웃→줌인 복귀가 안 됐다.
let skyView = { cAlt: 89, cAz: 180, fov: 180, fovRaw: 180, roll: 0 };
function resetSkyView() {
  skyView = { cAlt: 89, cAz: 180, fov: 180, fovRaw: 180, roll: 0 };
  if (lastStatus) drawSky(lastStatus);
}
const SKYTARGET_COL = { proposed: "#fb923c", slewing: "#34d399", tracking: "#4cc9f0" };
// 돔 방위 반전 (SkyX/PWI처럼 E↔W, N↔S 뒤집기). proj·역투영·방위라벨에 반영.
let skyFlip = (() => { try { return JSON.parse(localStorage.getItem("asterion.skyflip") || "{}") || {}; } catch (e) { return {}; } })();
function saveSkyFlip() { try { localStorage.setItem("asterion.skyflip", JSON.stringify(skyFlip)); } catch (e) { /* noop */ } }
// Sky 표시 커스텀 — 카탈로그 레이어/한계등급/궤적 토글 (localStorage 영속, 드로어로 편집)
const SKYCUSTOM_DEF = { messier: true, ngc: true, stars: true, planets: true,
                        labels: true, constellations: true, starMag: 4.5, dsoMag: 12,
                        grid: true, reticle: true, track: false, trackH: 3 };
let skyCustom = (() => {
  try { return Object.assign({}, SKYCUSTOM_DEF, JSON.parse(localStorage.getItem("asterion.skycustom") || "{}")); }
  catch (e) { return Object.assign({}, SKYCUSTOM_DEF); }
})();
function saveSkyCustom() { try { localStorage.setItem("asterion.skycustom", JSON.stringify(skyCustom)); } catch (e) { /* noop */ } }
// DSO 종류별 색 — 은하/구상/산개/행성상/발광/초신성잔해
const DSO_STYLE = { gx: "#e8c86a", gc: "#f0b860", oc: "#7fd0e8",
                    pn: "#5fd3a0", neb: "#e87a92", snr: "#c79be0", dbl: "#cdd6e6" };
// DSO 글리프 — 종류별 기호(은하=타원, 구상=원+십자, 산개=점선원, 행성상=원+점, 성운=사각)
function drawDsoGlyph(ctx, x, y, t, s, col) {
  ctx.strokeStyle = col; ctx.fillStyle = col; ctx.lineWidth = 1.1;
  if (t === "gx") {
    ctx.save(); ctx.translate(x, y); ctx.rotate(-Math.PI / 6);
    ctx.beginPath(); ctx.ellipse(0, 0, s * 1.7, s * 0.7, 0, 0, TAU); ctx.stroke();
    ctx.restore();
  } else if (t === "gc") {
    ctx.beginPath(); ctx.arc(x, y, s, 0, TAU); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(x - s, y); ctx.lineTo(x + s, y);
    ctx.moveTo(x, y - s); ctx.lineTo(x, y + s); ctx.stroke();
  } else if (t === "oc") {
    ctx.setLineDash([1.6, 1.6]); ctx.beginPath(); ctx.arc(x, y, s, 0, TAU); ctx.stroke(); ctx.setLineDash([]);
  } else if (t === "pn") {
    ctx.beginPath(); ctx.arc(x, y, s, 0, TAU); ctx.stroke();
    ctx.beginPath(); ctx.arc(x, y, 1, 0, TAU); ctx.fill();
  } else if (t === "neb" || t === "snr") {
    if (t === "snr") ctx.setLineDash([1.6, 1.6]);
    ctx.strokeRect(x - s, y - s, s * 2, s * 2); ctx.setLineDash([]);
  } else {
    ctx.beginPath(); ctx.arc(x, y, s * 0.8, 0, TAU); ctx.stroke();
  }
}
// 별자리선 — 막대그림 선분(카탈로그 마커 아래·격자 위). 양 끝 다 시야 안·지평선 위일 때만.
function drawConstLines(ctx, proj, fovHalf, lat, lstH) {
  const C = window.SKY_CONSTLINES; if (!C) return;
  ctx.strokeStyle = "rgba(116,150,205,.34)"; ctx.lineWidth = 1; ctx.setLineDash([]);
  const cache = {};
  const pos = (k) => {
    if (k in cache) return cache[k];
    const sc = C.s[k]; if (!sc) return (cache[k] = null);
    const [alt, az] = radecToAltaz(sc[0], sc[1], lat, lstH);
    return (cache[k] = { alt, q: proj(alt, az) });
  };
  ctx.beginPath();
  for (const name in C.fig) for (const seg of C.fig[name]) {
    const pa = pos(seg[0]), pb = pos(seg[1]);
    if (!pa || !pb || pa.alt < -2 || pb.alt < -2) continue;     // 지평선 아래 끝 → 생략
    if (pa.q[2] > fovHalf || pb.q[2] > fovHalf) continue;       // 시야 밖
    ctx.moveTo(pa.q[0], pa.q[1]); ctx.lineTo(pb.q[0], pb.q[1]);
  }
  ctx.stroke();
}
// 카탈로그 레이어 렌더 — 켜진 그룹만, RA/Dec→Alt/Az(LST·위도) 직접 변환.
function drawSkyCatalog(ctx, proj, fovHalf, lat, lstH) {
  const C = window.SKY_CATALOG; if (!C) return;
  ctx.textAlign = "center"; ctx.textBaseline = "top";
  const labelAll = skyView.fov < 60;            // 줌인했을 때만 전부 라벨(과밀 방지)
  const dso = [];
  if (skyCustom.messier) dso.push(C.messier);
  if (skyCustom.ngc) dso.push(C.ngc);
  dso.forEach((grp) => grp.forEach((o) => {
    if (o.mag > skyCustom.dsoMag) return;        // DSO 한계등급
    const [alt, az] = radecToAltaz(o.ra, o.dec, lat, lstH);
    if (alt < -2) return;                        // 지평선 아래는 생략
    const q = proj(alt, az); if (q[2] > fovHalf) return;
    ctx.globalAlpha = alt < 0 ? 0.28 : 0.92;
    drawDsoGlyph(ctx, q[0], q[1], o.t, 4.2, DSO_STYLE[o.t] || DSO_STYLE.dbl);
    if (skyCustom.labels && (labelAll || o.name || o.mag < 6.5)) {
      ctx.fillStyle = "rgba(206,216,235,.66)"; ctx.font = "8.5px " + SKYFONT;
      ctx.fillText(o.id, q[0], q[1] + 6);
    }
    ctx.globalAlpha = 1;
  }));
  if (skyCustom.stars) C.stars.forEach((o) => {
    if (o.mag > skyCustom.starMag) return;
    const [alt, az] = radecToAltaz(o.ra, o.dec, lat, lstH);
    if (alt < -2) return;
    const q = proj(alt, az); if (q[2] > fovHalf) return;
    const r = clamp(2.8 - 0.44 * o.mag, 0.8, 3.4);
    ctx.globalAlpha = alt < 0 ? 0.3 : 1;
    if (o.mag < 1.2 && alt > 0) { ctx.shadowColor = "#dbe6ff"; ctx.shadowBlur = 5; }
    ctx.beginPath(); ctx.arc(q[0], q[1], r, 0, TAU); ctx.fillStyle = "#eaf0fb"; ctx.fill();
    ctx.shadowBlur = 0;
    if (skyCustom.labels && (labelAll || o.mag < 1.6)) {
      ctx.fillStyle = "rgba(190,205,232,.6)"; ctx.font = "8px " + SKYFONT;
      ctx.fillText(o.name || o.id, q[0], q[1] + r + 1.5);
    }
    ctx.globalAlpha = 1;
  });
}
// 대상 궤적 — RA/Dec를 LST±trackH시간에 걸쳐 투영(지평선 평행 호) + 정시 틱
function drawSkyTrack(ctx, proj, fovHalf, ra, dec, lat, lstH, rgb) {
  const pts = [];          // [screenQ, alt, dh]
  for (let dh = -skyCustom.trackH; dh <= skyCustom.trackH + 1e-6; dh += 0.2) {
    const [alt, az] = radecToAltaz(ra, dec, lat, lstH + dh);
    pts.push([proj(alt, az), alt, dh]);
  }
  // 본선 — 어두운 케이싱 위에 밝은 색 점선(분주한 카탈로그 배경에서도 또렷)
  const stroke = (style, lw, dash) => {
    ctx.strokeStyle = style; ctx.lineWidth = lw; ctx.setLineDash(dash);
    ctx.beginPath(); let pen = false;
    for (const [q, alt] of pts) {
      if (q[2] > fovHalf * 1.2 || alt < -2) { pen = false; continue; }
      if (!pen) { ctx.moveTo(q[0], q[1]); pen = true; } else ctx.lineTo(q[0], q[1]);
    }
    ctx.stroke();
  };
  stroke("rgba(6,9,15,.85)", 3.6, []);              // 케이싱(가독)
  stroke(`rgba(${rgb},.95)`, 1.8, [5, 4]);          // 본선(밝게·굵게)
  ctx.setLineDash([]);
  // 정시 틱 + 라벨('지금'·양끝만 → 클러터 방지)
  ctx.textAlign = "center"; ctx.textBaseline = "middle"; ctx.font = "700 8.5px " + SKYFONT;
  for (const [q, alt, dh] of pts) {
    if (Math.abs(dh - Math.round(dh)) > 0.01 || alt < 0 || q[2] > fovHalf) continue;
    const now = Math.abs(dh) < 0.01;
    ctx.beginPath(); ctx.arc(q[0], q[1], now ? 3.6 : 2.4, 0, TAU);
    ctx.fillStyle = "rgba(6,9,15,.9)"; ctx.fill();                 // 틱 케이싱
    ctx.beginPath(); ctx.arc(q[0], q[1], now ? 2.7 : 1.7, 0, TAU);
    ctx.fillStyle = `rgb(${rgb})`; ctx.fill();                     // 틱 색
    if (now || Math.abs(dh) === skyCustom.trackH) {                // 라벨(외곽선으로 가독)
      const lab = now ? "지금" : (dh > 0 ? `+${dh}h` : `${dh}h`);
      const ly = q[1] - (now ? 10 : 9);
      ctx.lineWidth = 2.6; ctx.strokeStyle = "rgba(6,9,15,.9)"; ctx.strokeText(lab, q[0], ly);
      ctx.fillStyle = `rgb(${rgb})`; ctx.fillText(lab, q[0], ly);
    }
  }
}

// Sky Panel 천체 렌더 — 글꼴/행성 스타일/달 위상 글리프
// 라틴/숫자는 B612 Mono, 한글(달·금성 등)은 Pretendard로 폴백 → 앱 전체와 글꼴 일치
const SKYFONT = '"B612 Mono", "Pretendard", system-ui, sans-serif';
const PLANET_STYLE = {
  venus:   { c: "#f4f0e0", r: 3.2, ko: "금성" },
  mars:    { c: "#e0764a", r: 3.0, ko: "화성" },
  jupiter: { c: "#dccaa0", r: 3.7, ko: "목성" },
  saturn:  { c: "#e8d9a6", r: 3.3, ko: "토성" },
};
// 달 위상 글리프 — illum 0(신월)~1(보름), waxing면 밝은 쪽이 오른쪽
function drawMoonGlyph(ctx, x, y, r, illum, waxing) {
  illum = clamp(illum == null ? 0.5 : illum, 0, 1);
  ctx.save();
  ctx.translate(x, y);
  if (!waxing) ctx.scale(-1, 1);                 // 이지러짐 = 좌우 반전
  ctx.beginPath(); ctx.arc(0, 0, r, 0, TAU);
  ctx.fillStyle = "rgba(118,128,150,.42)"; ctx.fill();           // 어두운 면
  const ex = r * (1 - 2 * illum);                                // +r(신월)…0(반월)…−r(보름)
  ctx.beginPath();
  ctx.arc(0, 0, r, -Math.PI / 2, Math.PI / 2, false);            // 밝은 림(오른쪽 반원)
  ctx.ellipse(0, 0, Math.abs(ex), r, 0, Math.PI / 2, -Math.PI / 2, ex > 0);
  ctx.closePath();
  ctx.fillStyle = "#e9eef7"; ctx.fill();                         // 밝은 면
  ctx.restore();
  ctx.beginPath(); ctx.arc(x, y, r, 0, TAU);
  ctx.strokeStyle = "rgba(222,230,246,.55)"; ctx.lineWidth = 0.8; ctx.stroke();
}

// ── 조향 가능한 구면 투영 (방위등거리). 시야중심 벡터 + fov + roll로 sky→화면 ──
function _v3(alt, az) { const A = alt * D2R, Z = az * D2R, c = Math.cos(A);
  return [c * Math.cos(Z), c * Math.sin(Z), Math.sin(A)]; }            // (북,동,천정)
function skyBasis() {                                                   // 시야 직교기저 {f,right,up}
  const f = _v3(skyView.cAlt, skyView.cAz);
  let up;
  if (Math.abs(f[2]) > 0.99999) up = [1, 0, 0];                         // 천정/천저 → 북이 위
  else { const k = f[2]; const u = [-k * f[0], -k * f[1], 1 - k * k];   // (천정) - (f·천정)f
         const m = Math.hypot(u[0], u[1], u[2]) || 1e-9; up = [u[0]/m, u[1]/m, u[2]/m]; }
  const r = [f[1]*up[2]-f[2]*up[1], f[2]*up[0]-f[0]*up[2], f[0]*up[1]-f[1]*up[0]];  // f×up
  const rm = Math.hypot(r[0], r[1], r[2]) || 1e-9;
  return { f, right: [r[0]/rm, r[1]/rm, r[2]/rm], up };
}
// (alt,az) → [화면x, 화면y, c(중심각거리 rad)]. geom·flip은 인자로.
function skyProj(cx, cy, R, sgx, sgy, B, alt, az) {
  const p = _v3(alt, az);
  const d = p[0]*B.f[0] + p[1]*B.f[1] + p[2]*B.f[2];
  const c = Math.acos(clamp(d, -1, 1));
  const x = p[0]*B.right[0] + p[1]*B.right[1] + p[2]*B.right[2];
  const y = p[0]*B.up[0] + p[1]*B.up[1] + p[2]*B.up[2];
  const hyp = Math.hypot(x, y) || 1e-9;
  const sr = (c / ((skyView.fov * 0.5) * D2R)) * R;                     // 방위등거리: fov/2 → R
  const cr = Math.cos(skyView.roll), srr = Math.sin(skyView.roll);
  const rx = sr * (x / hyp), ry = sr * (y / hyp);
  return [cx + sgx * (rx * cr - ry * srr), cy - sgy * (rx * srr + ry * cr), c];
}
// '하늘을 잡고 끄는' 느낌 — 시야중심 alt/az를 직접 이동.
// 천정/천저를 넘기면 '롤오버': 고도 반사(90° 기준=시선방향 보존) + 방위 180° + roll에 π
// → 죽은 방향 없이 연속(천정에서 ~1° 미세 스킵만). 한 이벤트 고도변화는 ±80°로 캡 →
// 빠른 플릭이 한 번에 양극을 넘어 텔레포트되는 일 방지(작은 패널에서 발생 가능했음).
function panByScreen(ddx, ddy) {
  if (!skyGeom) return;
  const sgx = skyFlip.ew ? -1 : 1, sgy = skyFlip.ns ? -1 : 1;
  const ux = ddx * sgx, uy = -ddy * sgy;                                // flip·y반전 해제
  const cr = Math.cos(-skyView.roll), srr = Math.sin(-skyView.roll);
  const right = ux * cr - uy * srr, up = ux * srr + uy * cr;            // roll 해제
  const deg = (skyView.fov * 0.5) / skyGeom.R;                          // px당 각(°)
  const dAlt = clamp(up * deg, -80, 80);                                // 1이벤트 고도 변화 캡
  let na = skyView.cAlt - dAlt;                                         // 위로 끌면 시선 내려감
  let naz = skyView.cAz - right * deg;                                  // 오른쪽으로 끌면 왼쪽 봄
  if (na > 90) { na = 180 - na; naz += 180; skyView.roll += Math.PI; }  // 천정 넘김(캡 덕에 1회면 충분)
  else if (na < -90) { na = -180 - na; naz += 180; skyView.roll += Math.PI; }  // 천저 넘김
  skyView.cAlt = clamp(na, -89.5, 89.5);                               // 특이점 밴드(>89.74°) 회피
  skyView.cAz = ((naz % 360) + 360) % 360;
}

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
  // 디스크 기본 = '땅'(지평선 아래) 색. 하늘만 위에 그라디언트로 덮어 구분되게.
  ctx.beginPath(); ctx.arc(cx, cy, R, 0, TAU); ctx.fillStyle = "#0c0a07"; ctx.fill();

  const sgx = skyFlip.ew ? -1 : 1, sgy = skyFlip.ns ? -1 : 1;   // 방위 반전
  const B = skyBasis();
  const fovHalf = (skyView.fov * 0.5) * D2R;
  const proj = (alt, az) => skyProj(cx, cy, R, sgx, sgy, B, alt, az);
  const lat = (s.geo && s.geo.lat) != null ? s.geo.lat : 36.6;   // 카탈로그 RA/Dec→Alt/Az용
  const lstH = (s.time && s.time.lst_hours) != null ? s.time.lst_hours : 0;

  // 시야 원판으로 클립 — 밖은 안 그림(항상 패널을 꽉 채움)
  ctx.save();
  ctx.beginPath(); ctx.arc(cx, cy, R, 0, TAU); ctx.clip();

  // 하늘(지평선 위) 영역만 그라디언트로. 지평선(alt=0) 폐곡선이 천정쪽을 감싸므로
  // 채우면 하늘만 칠해지고, 지평선 아래는 땅색이 남아 한눈에 구분된다.
  ctx.beginPath();
  for (let az = 0; az <= 360; az += 3) {
    const q = proj(0, az);
    if (az === 0) ctx.moveTo(q[0], q[1]); else ctx.lineTo(q[0], q[1]);
  }
  ctx.closePath(); ctx.fillStyle = g; ctx.fill();

  // 격자(조향 시 곡선) — 폴리라인 투영, 시야 밖 구간은 끊는다
  const skyPath = (pts, style, lw) => {
    ctx.strokeStyle = style; ctx.lineWidth = lw; ctx.beginPath();
    let pen = false;
    for (const [al, az] of pts) {
      const q = proj(al, az);
      if (q[2] > fovHalf * 1.3) { pen = false; continue; }
      if (!pen) { ctx.moveTo(q[0], q[1]); pen = true; } else ctx.lineTo(q[0], q[1]);
    }
    ctx.stroke();
  };
  if (skyCustom.grid) for (let az = 0; az < 360; az += 30) {   // 방위선(자오선) — 격자 토글
    const pts = []; for (let al = 0; al <= 88; al += 3) pts.push([al, az]);
    skyPath(pts, "rgba(150,180,225,.08)", 1);
  }
  // 고도선 — 지평선(0)은 항상(하늘/땅 경계), 30·60°는 격자 토글에 따름
  (skyCustom.grid ? [0, 30, 60] : [0]).forEach((al) => {
    const pts = []; for (let az = 0; az <= 360; az += 3) pts.push([al, az]);
    skyPath(pts, al === 0 ? "rgba(150,180,225,.34)" : "rgba(150,180,225,.13)", al === 0 ? 1.4 : 1);
    if (al > 0) {
      const q = proj(al, skyView.cAz);
      if (q[2] <= fovHalf) {                              // 외곽선 케이싱으로 가독성 확보
        ctx.font = "700 10px " + SKYFONT;
        ctx.textAlign = "center"; ctx.textBaseline = "middle";
        ctx.lineWidth = 2.8; ctx.strokeStyle = "rgba(6,9,15,.85)"; ctx.strokeText(`${al}°`, q[0], q[1]);
        ctx.fillStyle = "rgba(180,202,238,.85)"; ctx.fillText(`${al}°`, q[0], q[1]);
      }
    }
  });
  // 방위 라벨 N/E/S/W — 림 안쪽으로 당겨 잘림 방지 + 외곽선 케이싱
  ctx.font = "700 13px " + SKYFONT;
  ctx.textAlign = "center"; ctx.textBaseline = "middle";
  [["N", 0], ["E", 90], ["S", 180], ["W", 270]].forEach(([lab, az]) => {
    const q = proj(2, az);
    if (q[2] > fovHalf) return;
    const dx = q[0] - cx, dy = q[1] - cy, d = Math.hypot(dx, dy) || 1;
    const inset = clamp(d - (R - 14), 0, 14);              // 림에서 14px 이내면 그만큼 안으로
    const fx = q[0] - dx / d * inset, fy = q[1] - dy / d * inset;
    ctx.lineWidth = 3; ctx.strokeStyle = "rgba(6,9,15,.9)"; ctx.strokeText(lab, fx, fy);
    ctx.fillStyle = "rgba(208,224,250,.95)"; ctx.fillText(lab, fx, fy);
  });

  // 별자리선 (격자 위, 카탈로그 마커 아래)
  if (skyCustom.constellations) drawConstLines(ctx, proj, fovHalf, lat, lstH);
  // 카탈로그 레이어 (메시에·NGC·별) — 격자 위, 태양/달 아래
  drawSkyCatalog(ctx, proj, fovHalf, lat, lstH);

  // 태양
  if (sun.az != null && sun.alt != null) {
    const q = proj(sun.alt, sun.az);
    if (q[2] <= fovHalf) {
      const below = sun.alt < 0;
      if (!below) { ctx.shadowColor = "#ffce6b"; ctx.shadowBlur = 20; }
      ctx.beginPath(); ctx.arc(q[0], q[1], below ? 5 : 8, 0, TAU);
      ctx.fillStyle = below ? "rgba(255,170,90,.4)" : "#ffd884"; ctx.fill();
      ctx.shadowBlur = 0;
    }
  }

  // 달·행성 (백엔드 30초 캐시). 지평선 아래여도 시야 안이면 흐리게 표시.
  (skyCustom.planets ? (s.sky_bodies || []) : []).forEach((bd) => {
    if (bd.az == null || bd.alt == null
        || Number.isNaN(bd.alt) || Number.isNaN(bd.az)) return;
    const q = proj(bd.alt, bd.az);
    if (q[2] > fovHalf) return;                    // 시야 밖
    const bx = q[0], by = q[1], up = bd.alt > 0;
    ctx.globalAlpha = up ? 1 : 0.3;
    ctx.textAlign = "center"; ctx.textBaseline = "top";
    if (bd.kind === "moon") {
      drawMoonGlyph(ctx, bx, by, 7, bd.illum, bd.waxing);
      ctx.fillStyle = "rgba(223,231,247,.9)"; ctx.font = "9px " + SKYFONT;
      ctx.fillText("달", bx, by + 9);
    } else {
      const st = PLANET_STYLE[bd.name] || { c: "#cdd6e6", r: 3, ko: bd.name };
      if (up) { ctx.shadowColor = st.c; ctx.shadowBlur = 6; }
      ctx.beginPath(); ctx.arc(bx, by, st.r, 0, TAU);
      ctx.fillStyle = st.c; ctx.fill();
      ctx.shadowBlur = 0;
      ctx.fillStyle = "rgba(208,217,233,.82)"; ctx.font = "8.5px " + SKYFONT;
      ctx.fillText(st.ko, bx, by + st.r + 2.5);
    }
    ctx.globalAlpha = 1;
  });

  const hasMount = !m.stale && m.alt != null && m.az != null &&
                   !Number.isNaN(m.alt) && !Number.isNaN(m.az);
  const md = hasMount ? { alt: m.alt, az: m.az } : null;  // 라이브 위치(실시간, 보간 없음)

  // 목표 마커 — 상태별 색: 제안(주황) → 이동(초록) → 추종(파랑). RA/Dec면 하늘 따라 이동.
  if (skyTarget) {
    let tAlt = skyTarget.alt, tAz = skyTarget.az;
    if (skyTarget.ra != null && skyTarget.dec != null) {
      [tAlt, tAz] = radecToAltaz(skyTarget.ra, skyTarget.dec, lat, lstH);
    }
    const [gx, gy] = proj(tAlt, tAz);
    const tc = SKYTARGET_COL[skyTarget.state] || SKYTARGET_COL.proposed;
    const rgb = skyTarget.state === "slewing" ? "52,211,153"
              : skyTarget.state === "tracking" ? "76,201,240" : "251,146,60";
    // 대상 궤적(±trackH시간) — 켜졌고 RA/Dec 있을 때
    if (skyCustom.track && skyTarget.ra != null && skyTarget.dec != null)
      drawSkyTrack(ctx, proj, fovHalf, skyTarget.ra, skyTarget.dec, lat, lstH, rgb);
    if (md) {
      const [tx, ty] = proj(md.alt, md.az);
      ctx.setLineDash([4, 4]);
      ctx.beginPath(); ctx.moveTo(tx, ty); ctx.lineTo(gx, gy);
      ctx.strokeStyle = `rgba(${rgb},.55)`; ctx.lineWidth = 1.2; ctx.stroke();
      ctx.setLineDash([]);
    }
    ctx.save();
    ctx.translate(gx, gy); ctx.rotate(Math.PI / 4);
    ctx.strokeStyle = tc; ctx.lineWidth = 1.7;
    ctx.strokeRect(-5, -5, 10, 10);
    ctx.restore();
    const pulse = (Date.now() % 1600) / 1600;
    ctx.beginPath(); ctx.arc(gx, gy, 7 + pulse * 9, 0, TAU);
    ctx.strokeStyle = `rgba(${rgb},${0.55 * (1 - pulse)})`;
    ctx.lineWidth = 1.2; ctx.stroke();
    if (skyTarget.name) {                          // 대상 이름 라벨
      ctx.fillStyle = tc; ctx.font = "9.5px " + SKYFONT;
      ctx.textAlign = "center"; ctx.textBaseline = "bottom";
      ctx.fillText(skyTarget.name, gx, gy - 11);
    }
    skyTarget._alt = tAlt; skyTarget._az = tAz;    // 도달 판정용 현재 위치
  }

  // 망원경 포인팅 — 레티클(토글)은 '그리기'만 게이팅, 슬루→추종 상태기계는 항상 동작.
  if (md) {
    const [tx, ty] = proj(md.alt, md.az);
    if (skyCustom.reticle) {                                // 청록 십자 / 슬루 중 호박색
      const col = m.slewing ? "#fbbf24" : "#4cc9f0";
      const soft = m.slewing ? "rgba(251,191,36,.28)" : "rgba(76,201,240,.26)";
      ctx.beginPath(); ctx.arc(tx, ty, 11, 0, TAU);          // 외곽 희미한 링
      ctx.strokeStyle = soft; ctx.lineWidth = 1; ctx.stroke();
      ctx.strokeStyle = col; ctx.lineWidth = 1.6;            // 본 레티클
      ctx.beginPath(); ctx.arc(tx, ty, 6.5, 0, TAU); ctx.stroke();
      ctx.beginPath();                                       // 갭 십자
      ctx.moveTo(tx - 13, ty); ctx.lineTo(tx - 4, ty);
      ctx.moveTo(tx + 4, ty); ctx.lineTo(tx + 13, ty);
      ctx.moveTo(tx, ty - 13); ctx.lineTo(tx, ty - 4);
      ctx.moveTo(tx, ty + 4); ctx.lineTo(tx, ty + 13);
      ctx.stroke();
      ctx.beginPath(); ctx.arc(tx, ty, 1.6, 0, TAU); ctx.fillStyle = col; ctx.fill();
      ctx.fillStyle = col; ctx.font = "9px " + SKYFONT;      // 위치 라벨(alt/az)
      ctx.textAlign = "center"; ctx.textBaseline = "top";
      ctx.fillText(`${m.alt.toFixed(0)}° / ${m.az.toFixed(0)}°`, tx, ty + 15);
    }

    // 슬루 완료 → 추종(파랑)으로 전환 (마커 유지 — 추적 대상 표시; 레티클 off여도 동작)
    if (skyTarget && skyTarget.state === "slewing" && !m.slewing) {
      const ta = skyTarget._alt != null ? skyTarget._alt : skyTarget.alt;
      const tz = skyTarget._az != null ? skyTarget._az : skyTarget.az;
      const dAlt = Math.abs(m.alt - ta);
      const dAz = Math.abs(((m.az - tz + 540) % 360) - 180) * Math.cos(m.alt * D2R);
      if (Math.hypot(dAlt, dAz) < 0.7) skyTarget.state = "tracking";
    }
  }

  ctx.restore();                                   // 클립 해제
  ctx.beginPath(); ctx.arc(cx, cy, R, 0, TAU);     // 시야 테두리
  ctx.strokeStyle = "rgba(150,180,225,.4)"; ctx.lineWidth = 1.5; ctx.stroke();
}

// 클릭 → alt/az 역변환 → GoTo 메뉴
// 화면(px,py, 원본 캔버스 기준) → 돔 alt/az (뷰포트 줌·팬·회전 역적용). 돔 밖이면 null.
function skyInv(px, py) {           // 화면 → alt/az (조향 투영 역변환). 시야 밖이면 null.
  if (!skyGeom) return null;
  const { cx, cy, R } = skyGeom;
  const sgx = skyFlip.ew ? -1 : 1, sgy = skyFlip.ns ? -1 : 1;
  const ux = (px - cx) * sgx, uy = -(py - cy) * sgy;          // flip·y반전 해제
  const cr = Math.cos(-skyView.roll), srr = Math.sin(-skyView.roll);
  const x = ux * cr - uy * srr, y = ux * srr + uy * cr;        // roll 해제
  const sr = Math.hypot(x, y);
  const c = (sr / R) * ((skyView.fov * 0.5) * D2R);            // 중심 각거리
  if (c > Math.PI) return null;
  const nx = x / (sr || 1e-9), ny = y / (sr || 1e-9);
  const B = skyBasis(), cc = Math.cos(c), ss = Math.sin(c);
  const p = [B.f[0]*cc + (B.right[0]*nx + B.up[0]*ny)*ss,
             B.f[1]*cc + (B.right[1]*nx + B.up[1]*ny)*ss,
             B.f[2]*cc + (B.right[2]*nx + B.up[2]*ny)*ss];
  return { alt: Math.asin(clamp(p[2], -1, 1)) / D2R,
           az: ((Math.atan2(p[1], p[0]) / D2R) + 360) % 360 };
}
// 클릭 위치에서 가장 가까운 천체 — 달/행성 + 카탈로그(메시에·NGC·별, 켜진 그룹만).
// 반환 {alt, az, kind, name, ra?, dec?, id?} — 카탈로그는 RA/Dec 동봉(정확한 좌표).
function skyHitBody(px, py) {
  if (!skyGeom || !lastStatus) return null;
  const { cx, cy, R } = skyGeom;
  const sgx = skyFlip.ew ? -1 : 1, sgy = skyFlip.ns ? -1 : 1;
  const B = skyBasis(), fovHalf = (skyView.fov * 0.5) * D2R;
  const lat = (lastStatus.geo && lastStatus.geo.lat) != null ? lastStatus.geo.lat : 36.6;
  const lstH = (lastStatus.time && lastStatus.time.lst_hours) != null ? lastStatus.time.lst_hours : 0;
  let best = null, bestD = 15;
  const test = (alt, az, mk) => {
    if (alt == null || az == null || Number.isNaN(alt) || Number.isNaN(az)) return;
    const q = skyProj(cx, cy, R, sgx, sgy, B, alt, az);
    if (q[2] > fovHalf) return;
    const d = Math.hypot(q[0] - px, q[1] - py);
    if (d < bestD) { bestD = d; best = Object.assign({ alt, az }, mk); }
  };
  (lastStatus.sky_bodies || []).forEach((bd) => test(bd.alt, bd.az, {
    kind: bd.kind, name: bd.kind === "moon" ? "달" : ((PLANET_STYLE[bd.name] || {}).ko || bd.name) }));
  const C = window.SKY_CATALOG;
  if (C) {
    const grp = [];
    if (skyCustom.messier) grp.push(["dso", C.messier]);
    if (skyCustom.ngc) grp.push(["dso", C.ngc]);
    if (skyCustom.stars) grp.push(["star", C.stars]);
    grp.forEach(([kind, arr]) => arr.forEach((o) => {
      if (kind === "star" && o.mag > skyCustom.starMag) return;
      const [alt, az] = radecToAltaz(o.ra, o.dec, lat, lstH);
      test(alt, az, { kind, ra: o.ra, dec: o.dec, id: o.id, name: o.name || o.id });
    }));
  }
  return best;
}
// 좌클릭 선택 — 객체 우선, 없으면 빈 하늘. 제안(주황) 마커 + GoTo 메뉴.
function skySelectAt(px, py, clientX, clientY) {
  if (!skyGeom || !lastStatus) return;
  const lat = (lastStatus.geo && lastStatus.geo.lat) != null ? lastStatus.geo.lat : 36.6;
  const lstH = (lastStatus.time && lastStatus.time.lst_hours) != null ? lastStatus.time.lst_hours : 0;
  let name = null, kind = null, alt, az, ra = null, dec = null, desig = null;
  const hit = skyHitBody(px, py);
  if (hit) {
    alt = hit.alt; az = hit.az; kind = hit.kind; name = hit.name;
    if (hit.ra != null) { ra = hit.ra; dec = hit.dec; }     // 카탈로그 = 참 좌표 사용
    if (hit.id && hit.id !== name) desig = hit.id;           // 이름과 다른 지정번호
  } else {
    const aa = skyInv(px, py);
    if (!aa) { hideSkyMenu(); return; }
    alt = aa.alt; az = aa.az;
  }
  if (alt < 0) { hideSkyMenu(); return; }   // 지평선 아래(땅)는 선택 불가 — 못 가리킴
  if (ra == null) [ra, dec] = altazToRadec(alt, az, lat, lstH);
  skyTarget = { ra, dec, alt, az, _alt: alt, _az: az, name, kind, id: hit && hit.id,
                state: "proposed", ts: Date.now() };
  kickSky();
  const menu = $("sky-menu");
  menu.innerHTML =
    (name ? `<div class="sm-line">${name}${desig ? ` <span class="sm-desig">${desig}</span>` : ""}</div>` : "") +
    `<div class="sm-line${name ? " sm-sub" : ""}">ALT ${alt.toFixed(1)}° · AZ ${az.toFixed(1)}°</div>` +
    `<div class="sm-line sm-sub">RA ${fmtRa(ra)} · DEC ${fmtDec(dec)}</div>` +
    `<div class="ctrl-row"><button class="btn btn-go btn-sm" id="sm-goto">GoTo</button>` +
    `<button class="btn btn-sm" id="sm-close">✕</button></div>`;
  // 클릭 지점에서 디스크 중심 '반대' 사분면으로 펼친다 → 마커/대상을 안 가림.
  menu.style.display = "block";
  const wrap = $("sky-wrap").getBoundingClientRect();
  const relX = clientX - wrap.left, relY = clientY - wrap.top, gap = 12;
  const mw = menu.offsetWidth, mh = menu.offsetHeight;
  const left = relX > wrap.width / 2 ? relX - mw - gap : relX + gap;
  const top = relY > wrap.height / 2 ? relY - mh - gap : relY + gap;
  menu.style.left = clamp(left, 4, Math.max(4, wrap.width - mw - 4)) + "px";
  menu.style.top = clamp(top, 4, Math.max(4, wrap.height - mh - 4)) + "px";
  $("sm-goto").onclick = async () => {
    hideSkyMenu();
    if (skyTarget) skyTarget.state = "slewing";       // 주황→초록
    kickSky();
    try { await post("/api/actions/mount/goto_radec", { ra: ra.toFixed(5), dec: dec.toFixed(4) }); }
    catch (e) { if (skyTarget) skyTarget.state = "proposed"; }
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
let tlHoverT = null;   // 타임라인 호버 시각(마우스) — 크로스헤어+값 표시

async function fetchTimeline() {
  try {
    timelineData = await (await fetch("/api/night/timeline")).json();
    tnData = null;   // 새 타임라인 → 오늘밤 베스트 재계산
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
  refreshTonight();
}
// 박명 밴드 — 태양 고도(td.sun_alt) → 차분한 네이비 단계. 표본 간격이 좁아
// 밴드가 잘게 칠해져 경계가 자연스럽게 부드러움(별도 블러 불필요).
function _bandColor(a) {
  return a > 0 ? "#272f43" : a > -6 ? "#20283c" : a > -12 ? "#181f30"
       : a > -18 ? "#111626" : "#080b12";
}
function drawTimelineOn(cv) {
  const { ctx, w, h } = hidpi(cv);
  ctx.clearRect(0, 0, w, h);
  const td = timelineData;
  const t0 = td.start, t1 = td.end;
  const padL = 34, padR = 12, padT = 12, padB = 20;
  const X = (t) => padL + (t - t0) / (t1 - t0) * (w - padL - padR);
  // 0°가 baseline(바닥), 90°가 위. 지평선 아래(음수)는 바닥에 클램프.
  const Y = (a) => (h - padB) - (Math.max(0, Math.min(90, a)) / 90) * (h - padT - padB);
  const plotH = h - padT - padB;
  const nowT = Date.now() / 1000;
  ctx.lineJoin = "round"; ctx.lineCap = "round";

  // 마우스 호버 → 크로스헤어 + 시각/고도 (캔버스당 1회 바인딩)
  if (!cv.dataset.tlw) {
    cv.dataset.tlw = "1"; cv.style.cursor = "crosshair";
    cv.addEventListener("mousemove", (e) => {
      const td2 = timelineData; if (!td2) return;
      const r = cv.getBoundingClientRect();
      const frac = (e.clientX - r.left - padL) / (r.width - padL - padR);
      tlHoverT = td2.start + Math.max(0, Math.min(1, frac)) * (td2.end - td2.start);
      drawTimeline();
    });
    cv.addEventListener("mouseleave", () => { tlHoverT = null; drawTimeline(); });
  }

  // 박명 밴드 (일몰박명→천문야간)
  for (let i = 0; i < td.t.length - 1; i++) {
    ctx.fillStyle = _bandColor(td.sun_alt[i]);
    ctx.fillRect(X(td.t[i]), padT, X(td.t[i + 1]) - X(td.t[i]) + 1, plotH);
  }
  // 플랫 창
  (td.flat_windows || []).forEach((wd) => {
    ctx.fillStyle = "rgba(52,211,153,.15)";
    ctx.fillRect(X(wd.start), padT, X(wd.end) - X(wd.start), plotH);
  });
  // 60° 보조선
  ctx.strokeStyle = "rgba(120,140,170,.16)"; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(padL, Y(60)); ctx.lineTo(w - padR, Y(60)); ctx.stroke();
  // 지평선 0° (초록 점선)
  ctx.strokeStyle = "rgba(131,196,90,.5)"; ctx.setLineDash([5, 4]); ctx.lineWidth = 1.1;
  ctx.beginPath(); ctx.moveTo(padL, Y(0)); ctx.lineTo(w - padR, Y(0)); ctx.stroke();
  // 30° 관측 한계 (앰버 점선)
  ctx.strokeStyle = "rgba(205,163,95,.7)"; ctx.setLineDash([4, 3]);
  ctx.beginPath(); ctx.moveTo(padL, Y(30)); ctx.lineTo(w - padR, Y(30)); ctx.stroke();
  ctx.setLineDash([]);

  // 대상 곡선 (코랄) + peak + 현재고도점
  if (trackData && trackData.t && trackData.t.length) {
    ctx.beginPath();
    trackData.t.forEach((t, i) => {
      const x = X(t), y = Y(trackData.alt[i]);
      i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
    });
    ctx.strokeStyle = "#ec7a73"; ctx.lineWidth = 2.4; ctx.stroke();
    // peak
    let pi = 0;
    for (let i = 1; i < trackData.alt.length; i++)
      if (trackData.alt[i] > trackData.alt[pi]) pi = i;
    const px = X(trackData.t[pi]), py = Y(trackData.alt[pi]);
    ctx.fillStyle = "#080b12"; ctx.strokeStyle = "#ec7a73"; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.arc(px, py, 3.5, 0, 7); ctx.fill(); ctx.stroke();
    ctx.fillStyle = "#f0a098"; ctx.textAlign = "center";
    ctx.font = "600 11px Pretendard,system-ui,sans-serif";
    ctx.fillText(Math.round(trackData.alt[pi]) + "°", px, py - 7);
    // 현재 고도점 (곡선 위 보간)
    const tt = trackData.t, aa = trackData.alt;
    if (nowT > tt[0] && nowT < tt[tt.length - 1]) {
      let j = 0; while (j < tt.length - 1 && tt[j + 1] < nowT) j++;
      const f = (nowT - tt[j]) / ((tt[j + 1] - tt[j]) || 1);
      const na = aa[j] + (aa[j + 1] - aa[j]) * f;
      ctx.fillStyle = "#ec7a73"; ctx.strokeStyle = "#080b12"; ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.arc(X(nowT), Y(na), 3.8, 0, 7); ctx.fill(); ctx.stroke();
    }
  }
  // 현재 시각 세로선
  if (nowT > t0 && nowT < t1) {
    ctx.strokeStyle = "rgba(216,223,231,.6)"; ctx.setLineDash([2, 3]); ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(X(nowT), padT); ctx.lineTo(X(nowT), h - padB); ctx.stroke();
    ctx.setLineDash([]);
  }
  // 호버 크로스헤어 + 시각/고도 읽기
  if (tlHoverT != null && tlHoverT > t0 && tlHoverT < t1) {
    const hx = X(tlHoverT);
    ctx.strokeStyle = "rgba(231,237,242,.45)"; ctx.setLineDash([2, 2]); ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(hx, padT); ctx.lineTo(hx, h - padB); ctx.stroke(); ctx.setLineDash([]);
    const d = new Date(tlHoverT * 1000);
    let label = String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
    if (trackData && trackData.t && trackData.t.length) {
      const tt = trackData.t, aa = trackData.alt;
      if (tlHoverT >= tt[0] && tlHoverT <= tt[tt.length - 1]) {
        let j = 0; while (j < tt.length - 1 && tt[j + 1] < tlHoverT) j++;
        const f = (tlHoverT - tt[j]) / ((tt[j + 1] - tt[j]) || 1);
        const na = aa[j] + (aa[j + 1] - aa[j]) * f;
        ctx.fillStyle = "#fff"; ctx.strokeStyle = "#ec7a73"; ctx.lineWidth = 1.5;
        ctx.beginPath(); ctx.arc(hx, Y(na), 3.4, 0, 7); ctx.fill(); ctx.stroke();
        label += " · " + Math.max(0, Math.round(na)) + "°";
      }
    }
    ctx.font = "600 10.5px Pretendard,system-ui,sans-serif";
    const tw = ctx.measureText(label).width + 12;
    let lx = hx + 7; if (lx + tw > w - padR) lx = hx - 7 - tw;
    ctx.fillStyle = "rgba(8,11,18,.86)"; ctx.fillRect(lx, padT + 2, tw, 17);
    ctx.fillStyle = "#e7edf2"; ctx.textAlign = "left"; ctx.fillText(label, lx + 6, padT + 14);
  }
  // 축 라벨 (앱 글꼴 · 30°만 앰버)
  ctx.textAlign = "right"; ctx.font = "500 10px Pretendard,system-ui,sans-serif";
  [0, 30, 60, 90].forEach((a) => {
    ctx.fillStyle = a === 30 ? "#cda35f" : "#aab4bf";
    ctx.fillText(a + "°", padL - 4, Y(a) + 3);
  });
  ctx.fillStyle = "#828c99"; ctx.textAlign = "center";
  for (let t = Math.ceil(t0 / 10800) * 10800; t < t1; t += 10800) {
    const d = new Date(t * 1000);
    ctx.fillText(String(d.getHours()).padStart(2, "0") + "h", X(t), h - 5);
  }
}

// ---------- 오늘 밤 베스트 (PLAN 탭) ----------
// 카탈로그(catalog.js) 대상의 밤 고도곡선을 클라이언트에서 계산(LST 보간) → 지금
// 관측 가능한(최고고도≥30°) 대상을 그리드/리스트/스플릿으로. 미니 그래프는 타임라인과
// 같은 박명밴드+코랄곡선. 대상 클릭 시 위 '야간 타임라인'에 그 곡선을 그린다(fetchTrack).
const TN_TYPE = { gx: "은하", gc: "구상성단", oc: "산개성단", pn: "행성상성운",
  neb: "성운", snr: "초신성잔해", dbl: "이중성", star: "항성" };
const TN_GLOW = { gx: "rgba(220,205,175,.5)", gc: "rgba(220,212,188,.55)",
  oc: "rgba(150,185,220,.5)", pn: "rgba(120,200,180,.5)", neb: "rgba(200,155,175,.5)",
  snr: "rgba(215,150,125,.5)", dbl: "rgba(195,205,230,.5)", star: "rgba(232,230,200,.6)" };
const tnState = { view: "grid", sort: "alt", q: "", limit: 50 };
let tnData = null, tnSel = null, tnWired = false;

function _tnLST(t, lstNow, tNow) {
  return ((lstNow + (t - tNow) / 3600 * 1.0027379) % 24 + 24) % 24;
}
function _tnHHMM(t) {
  const d = new Date(t * 1000);
  return String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
}
function computeTonight() {
  if (!lastStatus || !timelineData || !window.SKY_CATALOG) return [];
  const lat = (lastStatus.geo && lastStatus.geo.lat) != null ? lastStatus.geo.lat : 36.6;
  const lstNow = (lastStatus.time && lastStatus.time.lst_hours) != null ? lastStatus.time.lst_hours : 0;
  const tNow = Date.now() / 1000, td = timelineData, ts = td.t;
  const cat = [].concat(window.SKY_CATALOG.messier || [], window.SKY_CATALOG.ngc || []);
  const out = [];
  cat.forEach((o) => {
    let maxAlt = -90, trT = ts[0];
    const curve = new Array(ts.length);
    for (let i = 0; i < ts.length; i++) {
      const aa = radecToAltaz(o.ra, o.dec, lat, _tnLST(ts[i], lstNow, tNow));
      curve[i] = aa[0];
      if (td.sun_alt[i] < 0 && aa[0] > maxAlt) { maxAlt = aa[0]; trT = ts[i]; }
    }
    if (maxAlt >= 30) out.push({ o, maxAlt, trT, curve });
  });
  return out;
}
function drawTnMini(cv, item) {
  if (!cv || cv.offsetParent === null) return;
  const { ctx, w, h } = hidpi(cv); ctx.clearRect(0, 0, w, h);
  const td = timelineData, ts = td.t, t0 = td.start, t1 = td.end;
  const X = (t) => (t - t0) / (t1 - t0) * w, Y = (a) => (h - 2) - ((a - (-30)) / 120) * (h - 4);
  for (let i = 0; i < ts.length - 1; i++) {
    ctx.fillStyle = _bandColor(td.sun_alt[i]);
    ctx.fillRect(X(ts[i]), 0, X(ts[i + 1]) - X(ts[i]) + 1, h);
  }
  ctx.strokeStyle = "rgba(205,163,95,.45)"; ctx.setLineDash([3, 2]);
  ctx.beginPath(); ctx.moveTo(0, Y(30)); ctx.lineTo(w, Y(30)); ctx.stroke(); ctx.setLineDash([]);
  ctx.beginPath();
  item.curve.forEach((a, i) => { const px = X(ts[i]), py = Y(a); i ? ctx.lineTo(px, py) : ctx.moveTo(px, py); });
  ctx.strokeStyle = "#ec7a73"; ctx.lineWidth = 1.6; ctx.lineJoin = "round"; ctx.lineCap = "round"; ctx.stroke();
}
function tnGlow(o) { return TN_GLOW[o.t] || TN_GLOW.gx; }
// 실제 DSS2 컬러 컷아웃 (CDS hips2fits, ra는 도 단위). 로드 실패/오프라인이면
// 뒤의 글로우 그라데이션이 자동 폴백(배경 레이어).
function tnImg(o) {
  return "https://alasky.u-strasbg.fr/hips-image-services/hips2fits?hips=CDS/P/DSS2/color&ra="
    + (o.ra * 15).toFixed(4) + "&dec=" + o.dec.toFixed(4)
    + "&fov=0.6&width=320&height=200&format=jpg&projection=TAN";
}
function tnBg(o) {   // 글로우 폴백(뒤 레이어). DSS는 아래 tnImgTag로 지연로딩.
  return "radial-gradient(circle at 50% 50%," + tnGlow(o) + ",transparent 66%),#090b0e";
}
function tnImgTag(o) {   // 지연로딩 DSS 썸네일 — 화면 밖 카드는 로드 안 함, 실패 시 제거(글로우 노출)
  return '<img class="tn-pimg" loading="lazy" alt="" src="' + tnImg(o) + '" onerror="this.remove()">';
}
function tnSorted() {
  const a = (tnData || []).slice();
  const s = tnState.sort;
  a.sort((x, y) => s === "mag" ? x.o.mag - y.o.mag : s === "tr" ? x.trT - y.trT : y.maxAlt - x.maxAlt);
  return a;
}
function renderTonightHTML() {
  const host = document.getElementById("tn-content"); if (!host) return;
  let all = tnSorted();
  const q = (tnState.q || "").trim().toLowerCase();
  if (q) all = all.filter((it) => ((it.o.name || "") + " " + it.o.id).toLowerCase().includes(q));
  const cnt = document.getElementById("tn-count"); if (cnt) cnt.textContent = all.length + " 대상";
  const top = tnState.limit ? all.slice(0, tnState.limit) : all;
  let html = "";
  if (tnState.view === "grid") {
    html = '<div class="tn-grid">' + top.map((it, k) => {
      const o = it.o;
      return '<div class="tn-card" data-ra="' + o.ra + '" data-dec="' + o.dec + '" data-nm="' + (o.name || o.id) + '">'
        + '<div class="tn-photo" style="background:' + tnBg(o) + '">'
        + tnImgTag(o)
        + '<div class="tn-fade2"></div>'
        + '<div class="tn-ov"><div class="tn-nm2"><b>' + (o.name || o.id) + '</b> <span>' + o.id + ' · ' + (TN_TYPE[o.t] || "") + '</span></div>'
        + '<div class="tn-meta2">★ ' + o.mag.toFixed(1) + '등 · 최고 <b>' + Math.round(it.maxAlt) + '°</b> · 통과 ' + _tnHHMM(it.trT) + '</div></div></div>'
        + '<canvas class="tn-mini" data-k="' + k + '"></canvas></div>';
    }).join("") + '</div>';
  } else if (tnState.view === "list") {
    html = '<div class="tn-list">' + top.map((it, k) => {
      const o = it.o;
      return '<div class="tn-row" data-ra="' + o.ra + '" data-dec="' + o.dec + '" data-nm="' + (o.name || o.id) + '">'
        + '<div class="tn-th" style="background:' + tnBg(o) + '">' + tnImgTag(o) + '</div>'
        + '<span class="tn-rnm"><b>' + (o.name || o.id) + '</b> <span>' + o.id + ' · ' + (TN_TYPE[o.t] || "") + '</span></span>'
        + '<span class="tn-rmag">★' + o.mag.toFixed(1) + '</span>'
        + '<canvas class="tn-mini sm" data-k="' + k + '"></canvas>'
        + '<span class="tn-ralt">' + Math.round(it.maxAlt) + '°</span>'
        + '<span class="tn-rtr">' + _tnHHMM(it.trT) + '</span></div>';
    }).join("") + '</div>';
  } else {
    if (!top.some((x) => x.o.id === tnSel)) tnSel = top[0] && top[0].o.id;
    const sit = top.filter((x) => x.o.id === tnSel)[0] || top[0];
    const left = '<div class="tn-slist">' + top.map((it) => {
      const o = it.o;
      return '<div class="tn-sli' + (o.id === tnSel ? ' on' : '') + '" data-id="' + o.id + '">'
        + '<div class="tn-th sm" style="background:' + tnBg(o) + '">' + tnImgTag(o) + '</div>'
        + '<span class="tn-slnm"><b>' + (o.name || o.id) + '</b></span><span class="tn-ralt">' + Math.round(it.maxAlt) + '°</span></div>';
    }).join("") + '</div>';
    let right = '<div class="tn-sdet">대상을 선택하세요</div>';
    if (sit) {
      const o = sit.o;
      right = '<div class="tn-sdet"><div class="tn-photo big" style="background:' + tnBg(o) + '">'
        + tnImgTag(o)
        + '<div class="tn-fade"></div><div class="tn-nm"><b>' + (o.name || o.id) + '</b><span>' + o.id + ' · ' + (TN_TYPE[o.t] || "") + '</span></div></div>'
        + '<canvas class="tn-mini big" data-k="sel"></canvas>'
        + '<div class="tn-sstats"><span>최고 <b>' + Math.round(sit.maxAlt) + '°</b></span><span>통과 <b>' + _tnHHMM(sit.trT) + '</b></span><span>등급 <b>' + o.mag.toFixed(1) + '</b></span></div>'
        + '<button class="tn-show" data-ra="' + o.ra + '" data-dec="' + o.dec + '" data-nm="' + (o.name || o.id) + '">야간 타임라인에 표시</button></div>';
    }
    html = '<div class="tn-split">' + left + right + '</div>';
  }
  host.innerHTML = html;
  host.querySelectorAll("canvas.tn-mini").forEach((cv) => {
    const k = cv.getAttribute("data-k");
    const it = k === "sel" ? (tnSorted().filter((x) => x.o.id === tnSel)[0]) : top[+k];
    if (it) drawTnMini(cv, it);
  });
  host.querySelectorAll(".tn-card,.tn-row,.tn-show").forEach((el) => {
    el.onclick = () => tnPick(+el.dataset.ra, +el.dataset.dec, el.dataset.nm);
  });
  host.querySelectorAll(".tn-sli").forEach((r) => {
    r.onclick = () => { tnSel = r.dataset.id; renderTonightHTML(); };
  });
}
function refreshTonight() {
  if (!document.getElementById("tn-content") || !timelineData) return;
  if (!tnData) tnData = computeTonight();
  renderTonightHTML();
}
function wireTonight() {
  if (tnWired) return; tnWired = true;
  const vt = document.getElementById("tn-view");
  if (vt) vt.querySelectorAll("button").forEach((b) => {
    b.onclick = () => {
      vt.querySelectorAll("button").forEach((x) => x.classList.remove("on"));
      b.classList.add("on"); tnState.view = b.dataset.v; renderTonightHTML();
    };
  });
  const so = document.getElementById("tn-sort");
  if (so) so.onchange = () => { tnState.sort = so.value; renderTonightHTML(); };
  const qi = document.getElementById("tn-q");
  if (qi) qi.oninput = () => { tnState.q = qi.value; renderTonightHTML(); };
  const li = document.getElementById("tn-lim");
  if (li) li.onchange = () => { tnState.limit = +li.value; renderTonightHTML(); };
}

// ---------- FOV 시뮬레이션 (PLAN 탭) ----------
// 선택한 대상의 DSS2 위에 (망원경 초점거리+센서)로 정해지는 카메라 화각 사각형을 얹는다.
// 대상은 '오늘 밤 베스트' 카드 클릭(tnPick)으로 설정.
let fovTarget = null, fovZoom = 1, fovPanX = 0, fovPanY = 0, fovBase = null;
const FOV_BASE_DEG = 4;   // 베이스 이미지 화각(°) — 대상당 1회만 로드, 줌/팬은 CSS변환
function setFovTarget(ra, dec, nm) {
  fovTarget = { ra: +ra, dec: +dec, nm: nm }; fovZoom = 1; fovPanX = 0; fovPanY = 0; renderFov();
}
function tnPick(ra, dec, nm) { fetchTrack(+ra, +dec, nm); setFovTarget(ra, dec, nm); loadDossier(nm); }
function fovCam() {
  const focal = +(($("fov-focal") || {}).value) || 530;
  const p = (($("fov-sensor") || {}).value || "23.5,15.7").split(",").map(Number);
  return { w: p[0] / focal * 57.2958, h: p[1] / focal * 57.2958, focal, sw: p[0], sh: p[1] };
}
function loadFovImage() {   // 대상 이미지 1회 로드 (줌/팬으로는 재요청 안 함 → 깜빡임 X)
  const img = $("fov-img"); if (!img || !fovTarget) return;
  fovBase = { ra: fovTarget.ra, dec: fovTarget.dec, fov: FOV_BASE_DEG };
  const url = "https://alasky.u-strasbg.fr/hips-image-services/hips2fits?hips=CDS/P/DSS2/color&ra="
    + (fovTarget.ra * 15).toFixed(4) + "&dec=" + fovTarget.dec.toFixed(4)
    + "&fov=" + FOV_BASE_DEG + "&width=900&height=900&format=jpg&projection=TAN";
  if (img.getAttribute("src") !== url) img.setAttribute("src", url);
}
function applyFovView() {   // 줌/팬/카메라 → CSS 변환·사각형·중심좌표 (네트워크 0)
  const img = $("fov-img"); if (!img || !fovBase) return;
  img.style.transform = "translate(" + fovPanX.toFixed(1) + "px," + fovPanY.toFixed(1) + "px) scale(" + fovZoom.toFixed(3) + ")";
  const cam = fovCam(), dispFov = fovBase.fov / fovZoom, rect = $("fov-rect");
  if (rect) {
    rect.style.width = Math.min(100, cam.w / dispFov * 100).toFixed(1) + "%";
    rect.style.height = Math.min(100, cam.h / dispFov * 100).toFixed(1) + "%";
    rect.style.transform = "translate(-50%,-50%) rotate(" + ((+($("fov-rot") || {}).value) || 0) + "deg)";
  }
  const W0 = ($("fov-stage") || {}).clientWidth || 500;
  const offX = (-fovPanX / fovZoom) * fovBase.fov / W0;   // 중심 십자선이 가리키는 하늘 좌표
  const offY = (-fovPanY / fovZoom) * fovBase.fov / W0;
  const cdec = fovBase.dec - offY;
  const cra = fovBase.ra - (offX / 15) / Math.cos(fovBase.dec * Math.PI / 180);
  if ($("fov-coord")) $("fov-coord").textContent =
    (fovTarget && fovTarget.nm ? fovTarget.nm + " · " : "") + "중심 RA " + fmtRa(cra) + " · DEC " + fmtDec(cdec);
  if ($("fov-label")) $("fov-label").textContent =
    "FOV " + cam.w.toFixed(2) + "° × " + cam.h.toFixed(2) + "° · " + cam.focal + "mm · 줌 " + fovZoom.toFixed(1) + "×";
}
function renderFov() {
  if (!fovTarget && window.SKY_CATALOG) {
    const m = (window.SKY_CATALOG.messier || []).find((o) => o.id === "M31")
      || (window.SKY_CATALOG.messier || [])[0];
    if (m) fovTarget = { ra: m.ra, dec: m.dec, nm: m.name || m.id };
  }
  if (!fovTarget) return;
  loadFovImage(); applyFovView();
}
function wireFov() {
  ["fov-focal", "fov-sensor", "fov-rot"].forEach((id) => {
    const el = $(id); if (el) { el.oninput = applyFovView; el.onchange = applyFovView; }
  });
  const st = $("fov-stage");
  if (st && !st.dataset.fw) {
    st.dataset.fw = "1";
    st.addEventListener("wheel", (e) => {
      e.preventDefault();
      const old = fovZoom;
      fovZoom = Math.max(1, Math.min(16, fovZoom * (e.deltaY < 0 ? 1.12 : 1 / 1.12)));
      const r = fovZoom / old; fovPanX *= r; fovPanY *= r;   // 중심 기준 줌(중심 좌표 고정)
      applyFovView();
    }, { passive: false });
    let drag = false, lx = 0, ly = 0;
    st.addEventListener("mousedown", (e) => { drag = true; lx = e.clientX; ly = e.clientY; st.classList.add("drag"); e.preventDefault(); });
    window.addEventListener("mousemove", (e) => {
      if (!drag) return;
      fovPanX += e.clientX - lx; fovPanY += e.clientY - ly; lx = e.clientX; ly = e.clientY; applyFovView();
    });
    window.addEventListener("mouseup", () => { if (drag) { drag = false; st.classList.remove("drag"); } });
  }
  renderFov();
}

// ---------- AI 야간 계획 (PLAN 탭) ----------
// 스케줄러(plan_night)가 만든 비겹침 ObservationPlan 슬롯을 마스터 타임라인(슬롯별
// 고도곡선이 끊겼다 이어지는 핸드오프)과 순서표로. 슬롯=params.slot_start/end("HH:MM"
// KST). 행마다 승인/실행/삭제·FOV(클릭 시 아래 FOV·야간타임라인에 반영).
const schState = { status: "draft" };
let schPlans = [], schGoal = null, schSegsCache = [], schWired = false;

function _slotMin(hhmm) {   // "HH:MM"(KST) → 야간분(저녁<자정<새벽 순서 보존: 12시 미만은 +24h)
  if (!hhmm || String(hhmm).indexOf(":") < 0) return null;
  const p = String(hhmm).split(":"), h = +p[0], m = +p[1];
  return (h < 12 ? h + 24 : h) * 60 + m;
}
function _schEsc(s) {
  return String(s == null ? "" : s).replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}
function _peakAlt(ra, dec) {   // 대상 밤시간 최고고도 — params에 없을 때 클라 계산(computeTonight식)
  if (!lastStatus || !timelineData || ra == null) return null;
  const lat = (lastStatus.geo && lastStatus.geo.lat) != null ? lastStatus.geo.lat : 36.6;
  const lstNow = (lastStatus.time && lastStatus.time.lst_hours) != null ? lastStatus.time.lst_hours : 0;
  const tNow = Date.now() / 1000, td = timelineData;
  let mx = -90;
  for (let i = 0; i < td.t.length; i++)
    if (td.sun_alt[i] < 0) {
      const a = radecToAltaz(ra, dec, lat, _tnLST(td.t[i], lstNow, tNow))[0];
      if (a > mx) mx = a;
    }
  return mx > -90 ? mx : null;
}

async function loadSchedule() {
  try {
    const q = schState.status ? "?status=" + schState.status : "";
    schPlans = await (await fetch("/api/meridian/plans" + q)).json();
    if (!Array.isArray(schPlans)) schPlans = [];
  } catch (e) { schPlans = []; }
  try { schGoal = await (await fetch("/api/meridian/goal")).json(); } catch (e) { schGoal = null; }
  renderSchedule();
}

function _schSegs() {
  return (schPlans || []).map((p) => {
    const pr = p.params || {}, t = p.target || {};
    return {
      p, a: _slotMin(pr.slot_start), b: _slotMin(pr.slot_end),
      name: t.name || p.target_name || "—",
      ra: t.ra_hours, dec: t.dec_degs,
      peak: pr.slot_peak_alt != null ? pr.slot_peak_alt : _peakAlt(t.ra_hours, t.dec_degs),
      moon: pr.slot_moon_sep, status: p.approval_status,
    };
  });
}

function renderSchedule() {
  const goalEl = $("sch-goal");
  if (goalEl) {
    const g = schGoal && schGoal.goal_type;
    const setn = g && schGoal.params && schGoal.params.set;
    goalEl.textContent = g
      ? ("목표 · " + (g === "campaign" ? "캠페인" + (setn ? " " + setn : "") : g))
      : "목표 미설정";
    goalEl.classList.toggle("on", !!g);
  }
  const moonEl = $("sch-moon");   // 달 위상·고도(Phase3) — 그 밤 계획 전체 공통값
  if (moonEl) {
    const mp = (schPlans || []).map((p) => p.params || {}).find((pr) => pr.slot_moon_illum != null);
    if (mp) {
      moonEl.hidden = false;
      moonEl.textContent = "달 " + mp.slot_moon_illum + "%"
        + (mp.slot_moon_alt != null ? " · 고도 " + mp.slot_moon_alt + "°" : "")
        + (mp.slot_moon_alt != null && mp.slot_moon_alt <= 0 ? " (짐)" : "");
    } else { moonEl.hidden = true; }
  }
  schSegsCache = _schSegs().sort((x, y) =>   // 시간표는 슬롯 시작순(슬롯없는 건 뒤로)
    x.a == null ? (y.a == null ? 0 : 1) : (y.a == null ? -1 : x.a - y.a));
  const empty = $("sch-empty"), cv = $("sch-canvas");
  if (empty) empty.hidden = schSegsCache.length > 0;
  if (cv) cv.style.display = schSegsCache.length ? "" : "none";
  if (cv && schSegsCache.length)
    drawScheduleTimeline(cv, schSegsCache.filter((s) => s.a != null && s.b != null));
  renderScheduleTable(schSegsCache);
}

const _SCH_ST = { draft: ["초안", "st-draft"], approved: ["승인", "st-ok"],
  done: ["완료", "st-done"], running: ["실행중", "st-run"], aborted: ["중단", "st-draft"] };
function renderScheduleTable(segs) {
  const tb = $("sch-tbody"); if (!tb) return;
  tb.innerHTML = "";
  segs.forEach((s, i) => {
    const pr = s.p.params || {};
    const exp = pr.exposure_s != null ? pr.exposure_s + "s×" + (pr.count_per_filter || 1) : "";
    const filt = ((pr.filters || []).join("·") + " " + exp).trim() || "—";
    const slot = (pr.slot_start || "—") + "–" + (pr.slot_end || "—");
    const st = _SCH_ST[s.status] || [s.status || "—", "st-draft"];
    const can = s.ra != null && s.dec != null;
    const act = s.status === "draft"
      ? `<button class="sch-x act" data-act="approve" data-id="${s.p.id}">승인</button>`
      : (s.status === "approved" ? `<button class="sch-x act go" data-act="run" data-id="${s.p.id}">실행</button>` : "");
    const tr = document.createElement("tr");
    tr.innerHTML =
      `<td class="sch-n">${i + 1}</td>` +
      `<td class="mono">${slot}</td>` +
      `<td>${_schEsc(s.name)}</td>` +
      `<td class="sch-dim">${_schEsc(filt)}</td>` +
      `<td class="mono">${s.peak != null ? Math.round(s.peak) + "°" : "—"}</td>` +
      `<td class="mono">${s.moon != null ? s.moon + "°" : "—"}</td>` +
      `<td><span class="sch-badge ${st[1]}">${st[0]}</span></td>` +
      `<td class="sch-acts">${can ? `<button class="sch-x" data-act="fov" data-id="${s.p.id}">FOV</button>` : ""}${act}` +
      `<button class="sch-x del" data-act="del" data-id="${s.p.id}" title="삭제">✕</button></td>`;
    tb.appendChild(tr);
  });
  tb.querySelectorAll("button[data-act]").forEach((b) => {
    b.onclick = () => schAction(b.dataset.act, +b.dataset.id);
  });
}

async function schAction(act, id) {
  if (act === "fov") {
    const s = schSegsCache.find((x) => x.p.id === id);
    if (s && s.ra != null) tnPick(s.ra, s.dec, s.name);
    return;
  }
  try {
    if (act === "approve") await post(`/api/meridian/plans/${id}/approve`);
    else if (act === "run") await post(`/api/meridian/plans/${id}/run`);
    else if (act === "del") {
      const r = await fetch(`/api/meridian/plans/${id}`, { method: "DELETE" });
      if (!r.ok) throw new Error("삭제 실패");
    }
  } catch (e) { /* post가 UI 로그 남김 */ }
  loadSchedule();
}

function drawScheduleTimeline(cv, segs) {
  const { ctx, w, h } = hidpi(cv); ctx.clearRect(0, 0, w, h);
  if (!segs.length) return;
  const padL = 26, padR = 10, padT = 12, padB = 16;
  let t0 = Math.min.apply(null, segs.map((s) => s.a));
  let t1 = Math.max.apply(null, segs.map((s) => s.b));
  const span = Math.max(45, t1 - t0); t0 -= span * 0.04; t1 += span * 0.04;
  const X = (m) => padL + (m - t0) / (t1 - t0) * (w - padL - padR);
  const Y = (a) => (h - padB) - clamp(a, 0, 90) / 90 * (h - padT - padB);
  // 기준선: 지평선 0°(초록) · 관측한계 30°(앰버)
  ctx.lineWidth = 1;
  ctx.strokeStyle = "rgba(131,196,90,.4)"; ctx.setLineDash([5, 4]);
  ctx.beginPath(); ctx.moveTo(padL, Y(0)); ctx.lineTo(w - padR, Y(0)); ctx.stroke();
  ctx.strokeStyle = "rgba(205,163,95,.55)"; ctx.setLineDash([4, 3]);
  ctx.beginPath(); ctx.moveTo(padL, Y(30)); ctx.lineTo(w - padR, Y(30)); ctx.stroke();
  ctx.setLineDash([]);
  // 현재 야간분 + LST 보간용 기준
  const nd = new Date(); let nowM = nd.getHours() * 60 + nd.getMinutes();
  if (nd.getHours() < 12) nowM += 1440;
  const tNow = Date.now() / 1000;
  const haveSky = lastStatus && timelineData;
  const lat = (haveSky && lastStatus.geo && lastStatus.geo.lat != null) ? lastStatus.geo.lat : 36.6;
  const lstNow = (haveSky && lastStatus.time && lastStatus.time.lst_hours != null) ? lastStatus.time.lst_hours : 0;
  // 슬롯 블록 + 고도곡선 (끊겼다 이어지는 핸드오프)
  segs.forEach((s) => {
    const xa = X(s.a), xb = X(s.b), bw = Math.max(2, xb - xa);
    ctx.fillStyle = "rgba(236,122,115,.10)";
    ctx.fillRect(xa, padT, bw, h - padT - padB);
    ctx.strokeStyle = "rgba(236,122,115,.45)";
    ctx.beginPath(); ctx.moveTo(xa, padT); ctx.lineTo(xa, h - padB); ctx.stroke();
    if (s.ra != null && haveSky) {
      ctx.beginPath();
      const N = 14;
      for (let k = 0; k <= N; k++) {
        const m = s.a + (s.b - s.a) * k / N;
        const alt = radecToAltaz(s.ra, s.dec, lat, _tnLST(tNow + (m - nowM) * 60, lstNow, tNow))[0];
        const x = X(m), y = Y(alt);
        k ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
      }
      ctx.strokeStyle = "#ec7a73"; ctx.lineWidth = 2.2; ctx.stroke(); ctx.lineWidth = 1;
    }
    ctx.fillStyle = "#e7edf2"; ctx.textAlign = "left";
    ctx.font = "600 10.5px 'IBM Plex Sans KR',Pretendard,system-ui,sans-serif";
    if (bw > 28) {
      const nm = s.name.length > 13 ? s.name.slice(0, 12) + "…" : s.name;
      ctx.fillText(nm, xa + 4, padT + 11);
    }
    if (bw > 42) {
      ctx.fillStyle = "#8a94a1"; ctx.font = "500 9px 'IBM Plex Mono',ui-monospace,monospace";
      ctx.fillText(s.p.params.slot_start || "", xa + 4, h - padB - 4);
    }
  });
  // 현재 시각 세로선
  if (nowM > t0 && nowM < t1) {
    ctx.strokeStyle = "rgba(216,223,231,.6)"; ctx.setLineDash([2, 3]);
    ctx.beginPath(); ctx.moveTo(X(nowM), padT); ctx.lineTo(X(nowM), h - padB); ctx.stroke();
    ctx.setLineDash([]);
  }
  // 고도 축
  ctx.textAlign = "right"; ctx.font = "500 9.5px 'IBM Plex Sans KR',Pretendard,sans-serif";
  [0, 30, 60, 90].forEach((a) => {
    ctx.fillStyle = a === 30 ? "#cda35f" : "#8a94a1";
    ctx.fillText(a + "°", padL - 3, Y(a) + 3);
  });
  // 시 눈금 (KST)
  ctx.fillStyle = "#6b7280"; ctx.textAlign = "center";
  ctx.font = "500 9px 'IBM Plex Mono',ui-monospace,monospace";
  for (let m = Math.ceil(t0 / 60) * 60; m < t1; m += 60) {
    const hr = ((Math.floor(m / 60)) % 24 + 24) % 24;
    ctx.fillText(String(hr).padStart(2, "0") + "h", X(m), h - 3);
  }
}

function wireSchedule() {
  if (schWired) return; schWired = true;
  const seg = $("sch-filter");
  if (seg) seg.querySelectorAll("button").forEach((b) => {
    b.onclick = () => {
      seg.querySelectorAll("button").forEach((x) => x.classList.remove("on"));
      b.classList.add("on"); schState.status = b.dataset.s; loadSchedule();
    };
  });
  const rf = $("sch-refresh"); if (rf) rf.onclick = loadSchedule;
}

// ---------- 대상 페이지 (Skygraph dossier, PLAN 탭) ----------
// /api/targets/{name}의 dossier(관측요청·프레임·품질·가시성·추천)를 한 패널에. '오늘 밤
// 베스트' 카드 클릭(tnPick)이나 검색으로 진입. 대상명/번호/라벨 모두 백엔드가 해석.
let tpWired = false, tpCurrent = null;
function _fmtUtcShort(s) { return !s ? "—" : String(s).replace("T", " ").slice(0, 16); }
function _fmtInteg(s) {
  s = s || 0; if (s < 60) return s + "s";
  const m = Math.floor(s / 60), h = Math.floor(m / 60);
  return h ? h + "h " + (m % 60) + "m" : m + "m";
}
async function loadDossier(name) {
  if (!name) return;
  tpCurrent = name;
  const body = $("tp-body");
  if (body) body.innerHTML = '<div class="hint">불러오는 중… ' + _schEsc(name) + "</div>";
  try {
    const d = await (await fetch("/api/targets/" + encodeURIComponent(name))).json();
    renderDossier(d);
    if (d.stats && d.stats.n_lights) loadLightCurve(d.name);   // 라이트커브
    loadFeedback(d.name);                                       // 학습 피드백(A2)
  } catch (e) { if (body) body.innerHTML = '<div class="hint">조회 실패</div>'; }
}
async function loadFeedback(name) {
  const box = $("tp-fb"); if (!box) return;
  try {
    const d = await (await fetch("/api/feedback/" + encodeURIComponent(name))).json();
    const recs = d.recommendations || [];
    if (!recs.length) { box.innerHTML = ""; return; }
    const hint = d.exposure_hint;
    const ht = hint === "increase" ? "노출 늘리기" : hint === "decrease" ? "노출 줄이기" : "현 설정 유지";
    box.innerHTML = '<div class="tp-fb-h">학습 피드백 · ' + _schEsc(ht) + "</div>"
      + recs.map((r) => '<div class="tp-fb-r">• ' + _schEsc(r) + "</div>").join("");
  } catch (e) { /* noop */ }
}
async function loadLightCurve(name) {
  const cv = $("tp-lc"); if (!cv) return;
  try {
    const d = await (await fetch("/api/photometry/" + encodeURIComponent(name))).json();
    drawLightCurve(cv, (d.points || []).filter((p) => p.mag != null && p.date_obs_utc));
  } catch (e) { /* noop */ }
}
function drawLightCurve(cv, pts) {
  const { ctx, w, h } = hidpi(cv); ctx.clearRect(0, 0, w, h);
  if (!pts.length) {
    ctx.fillStyle = "#69727e"; ctx.font = "12px Pretendard,system-ui,sans-serif";
    ctx.fillText("측광 가능한 LIGHT 프레임이 없습니다 (FITS 파일 필요)", 10, 22); return;
  }
  const ts = pts.map((p) => (Date.parse(p.date_obs_utc) || 0) / 1000);
  const mags = pts.map((p) => p.mag);
  const padL = 38, padR = 12, padT = 12, padB = 22;
  let t0 = Math.min(...ts), t1 = Math.max(...ts); if (t1 === t0) { t0 -= 1; t1 += 1; }
  let m0 = Math.min(...mags), m1 = Math.max(...mags);
  const mp = (m1 - m0) * 0.12 || 0.1; m0 -= mp; m1 += mp;
  const X = (t) => padL + (t - t0) / (t1 - t0) * (w - padL - padR);
  const Y = (m) => padT + (m - m0) / (m1 - m0) * (h - padT - padB);   // mag↓(밝음)=위
  ctx.strokeStyle = "rgba(120,140,170,.18)"; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(padL, padT); ctx.lineTo(padL, h - padB); ctx.lineTo(w - padR, h - padB); ctx.stroke();
  ctx.beginPath();
  pts.forEach((p, i) => { const x = X(ts[i]), y = Y(p.mag); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); });
  ctx.strokeStyle = "#ec7a73"; ctx.lineWidth = 1.8; ctx.stroke();
  ctx.fillStyle = "#f0a098";
  pts.forEach((p, i) => { ctx.beginPath(); ctx.arc(X(ts[i]), Y(p.mag), 2.6, 0, 7); ctx.fill(); });
  ctx.fillStyle = "#8a94a1"; ctx.font = "10px 'IBM Plex Mono',ui-monospace,monospace"; ctx.textAlign = "right";
  ctx.fillText(m0.toFixed(1), padL - 4, padT + 8);
  ctx.fillText(m1.toFixed(1), padL - 4, h - padB);
  ctx.save(); ctx.translate(11, h / 2); ctx.rotate(-Math.PI / 2);
  ctx.textAlign = "center"; ctx.fillStyle = "#69727e";
  ctx.fillText("등급 — 위=밝음", 0, 0); ctx.restore();
  ctx.textAlign = "center"; ctx.fillStyle = "#69727e";
  ctx.fillText(_fmtUtcShort(pts[0].date_obs_utc), X(ts[0]) + 14, h - 6);
  if (pts.length > 1) ctx.fillText(_fmtUtcShort(pts[pts.length - 1].date_obs_utc), X(ts[ts.length - 1]) - 14, h - 6);
}
function renderDossier(d) {
  const body = $("tp-body"); if (!body) return;
  const t = d.target || {}, v = d.visibility || {}, st = d.stats || {};
  if ($("tp-rec")) $("tp-rec").textContent = d.recommendation || "";
  const obsBadge = v.observable ? '<span class="tp-ok">관측가능</span>'
    : '<span class="tp-no">고도부족</span>';
  const coord = (t.ra_hours != null)
    ? "RA " + fmtRa(t.ra_hours) + " · DEC " + fmtDec(t.dec_degs) : "좌표 불명";
  const filt = Object.entries(st.by_filter || {}).map(([k, n]) =>
    '<span class="tp-chip">' + _schEsc(k) + " ×" + n + "</span>").join("") || "—";
  const reqRows = (d.requests || []).map((p) =>
    "<tr><td>#" + p.id + "</td><td>" + _schEsc(p.status) + '</td><td class="sch-dim">'
    + _schEsc((p.filters || []).join("·")) + " "
    + (p.exposure_s != null ? p.exposure_s + "s×" + (p.count_per_filter || 1) : "")
    + '</td><td class="mono">' + (p.slot_start ? p.slot_start + "–" + p.slot_end : "—")
    + '</td><td class="sch-dim">' + _fmtUtcShort(p.created_utc) + "</td></tr>").join("");
  const frRows = (d.frames || []).slice(0, 20).map((f) => {
    const ok = (!f.flag || f.flag === "ok") && (!f.verdict || f.verdict === "ok");
    return "<tr><td>#" + f.id + "</td><td>" + _schEsc(f.image_type || "") + "</td><td>"
      + _schEsc(f.filter || "") + '</td><td class="mono">'
      + (f.exposure_s != null ? f.exposure_s + "s" : "—") + '</td><td class="mono">'
      + (f.median_adu != null ? Math.round(f.median_adu) : "—") + "</td><td><span class=\""
      + (ok ? "tp-fok" : "tp-fbad") + '">' + _schEsc(f.verdict || f.flag || "ok")
      + '</span></td><td class="sch-dim">' + _fmtUtcShort(f.date_obs_utc) + "</td></tr>";
  }).join("");
  body.innerHTML =
    '<div class="tp-head"><div class="tp-name">' + _schEsc(d.name)
    + (d.in_db ? "" : ' <span class="tp-tag">카탈로그</span>') + '</div><div class="tp-sub">'
    + _schEsc(t.type_ko || t.type || "—") + (t.magnitude != null ? " · " + t.magnitude + "등급" : "")
    + " · " + coord + "</div></div>"
    + '<div class="tp-grid">'
    + '<div class="tp-stat"><span>최고고도</span><b>' + (v.transit_alt != null ? v.transit_alt + "°" : "—") + "</b>" + obsBadge + "</div>"
    + '<div class="tp-stat"><span>LIGHT</span><b>' + (st.n_lights || 0) + "</b><i>" + (st.bad_lights ? st.bad_lights + " 불량" : "양호") + "</i></div>"
    + '<div class="tp-stat"><span>총 적분</span><b>' + _fmtInteg(st.integration_s) + "</b></div>"
    + '<div class="tp-stat"><span>관측요청</span><b>' + (st.n_requests || 0) + "</b></div></div>"
    + '<div id="tp-fb" class="tp-fb"></div>'
    + '<div class="tp-filt">필터 ' + filt + "</div>"
    + (st.n_lights ? '<div class="tp-sec">라이트커브 · 경량 조리개 측광</div><canvas id="tp-lc" class="tp-lc"></canvas>' : "")
    + (reqRows ? '<div class="tp-sec">관측 요청</div><div class="tbl-scroll tp-scroll"><table class="tbl sch-tbl"><thead><tr><th>#</th><th>상태</th><th>필터·노출</th><th>슬롯</th><th>생성</th></tr></thead><tbody>' + reqRows + "</tbody></table></div>" : "")
    + (frRows ? '<div class="tp-sec">프레임 (최근 20)</div><div class="tbl-scroll tp-scroll"><table class="tbl sch-tbl"><thead><tr><th>#</th><th>종류</th><th>필터</th><th>노출</th><th>ADU</th><th>품질</th><th>UTC</th></tr></thead><tbody>' + frRows + "</tbody></table></div>" : '<div class="hint">아직 이 대상의 프레임이 없습니다.</div>');
}
function wireTarget() {
  if (tpWired) return; tpWired = true;
  const go = $("tp-go"), q = $("tp-q");
  if (go && q) go.onclick = () => loadDossier(q.value.trim());
  if (q) q.addEventListener("keydown", (e) => { if (e.key === "Enter") loadDossier(q.value.trim()); });
}

// ---------- Forge 전처리 카드 (ANALYSIS 탭) ----------
// 백엔드 Forge(실시간 bias/dark/flat 보정)의 얼굴 — /api/status.forge를 그리고 토글로 제어.
function renderForge(fg) {
  const onB = $("fg-on"); if (!fg || !onB) return;
  const saveB = $("fg-save");
  onB.querySelector("b").textContent = fg.enabled ? "ON" : "OFF";
  onB.classList.toggle("on", !!fg.enabled);
  if (saveB) {
    saveB.querySelector("b").textContent = fg.save_calibrated ? "ON" : "OFF";
    saveB.classList.toggle("on", !!fg.save_calibrated);
  }
  if ($("fg-stat")) $("fg-stat").textContent = "마스터 캐시 " + (fg.masters_cached || 0);
  const body = $("fg-body"); if (!body) return;
  const src = fg.sources || {};
  const srcRow = ["bias", "dark", "flat"].map((k) =>
    '<div class="fg-src"><span>' + k + "</span><b>" + _schEsc(src[k] || "—") + "</b></div>").join("");
  const warns = (fg.warnings || []).map((w) => '<div class="fg-warn">⚠ ' + _schEsc(w) + "</div>").join("");
  const last = fg.last && typeof fg.last === "object" ? fg.last : null;
  body.innerHTML = '<div class="fg-grid">' + srcRow + "</div>" + warns
    + (last ? '<div class="fg-last">최근 보정 · ' + _schEsc(last.filter || "?")
        + " · 적용 " + _schEsc((last.applied || []).join(", ") || "없음")
        + (last.median_before != null ? " · 중앙값 " + Math.round(last.median_before)
            + "→" + Math.round(last.median_after) : "") + "</div>" : "");
}
async function forgeToggle(which) {
  const fg = (lastStatus && lastStatus.forge) || {};
  const body = which === "on" ? { on: !fg.enabled, save: !!fg.save_calibrated }
                              : { on: !!fg.enabled, save: !fg.save_calibrated };
  try { renderForge(await post("/api/forge/toggle", body)); } catch (e) { /* post가 로그 */ }
}
function wireForge() {
  const on = $("fg-on"), save = $("fg-save");
  if (on && !on.dataset.w) { on.dataset.w = "1"; on.onclick = () => forgeToggle("on"); }
  if (save && !save.dataset.w) { save.dataset.w = "1"; save.onclick = () => forgeToggle("save"); }
}

// ---------- 프레임 뷰어 (픽셀 분석, ANALYSIS 탭) ----------
// 저장된 FITS의 히스토그램·라인프로파일·통계 + Sentinel 품질(FWHM·별 수)을 본다.
// framedata 백엔드(/api/analysis/frames/*) 활용. 점광원 측광/별검출은 W1.
// ---------- 품질 추이 (Quality Timeseries) — 분석 탭, PP된 프레임 ----------
// 픽셀/히스토그램 표시는 외부도구(MaxIm/NINA/SharpCap) 몫 → 제거. 여기선 /api/timeseries로
// 밤새 하늘밝기·시잉·별 수 추세. 채운 점=PP(보정), 빈 점=raw(미보정). 기본 PP만, 토글로 raw 포함.
let qvShowRaw = false;
function drawTimeSeries(cv, points, key, color) {
  if (!cv) return;
  const { ctx, w, h } = hidpi(cv); ctx.clearRect(0, 0, w, h);
  const pts = points.map((p) => ({ v: p[key], cal: !!p.calibrated })).filter((p) => p.v != null);
  if (!pts.length) return;
  const pad = 7, n = pts.length;
  let lo = Math.min.apply(null, pts.map((p) => p.v));
  let hi = Math.max.apply(null, pts.map((p) => p.v));
  if (hi === lo) { hi = lo + 1; lo -= 1; }
  const X = (i) => pad + (n > 1 ? i / (n - 1) : 0.5) * (w - pad * 2);
  const Y = (v) => h - pad - (v - lo) / (hi - lo) * (h - pad * 2);
  ctx.beginPath();
  pts.forEach((p, i) => { const x = X(i), y = Y(p.v); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); });
  ctx.strokeStyle = color || "#4cc9f0"; ctx.lineWidth = 1.4; ctx.stroke();
  pts.forEach((p, i) => {   // 점: PP=채움, raw=속빈 회색
    ctx.beginPath(); ctx.arc(X(i), Y(p.v), 2.3, 0, 2 * Math.PI);
    if (p.cal) { ctx.fillStyle = color || "#4cc9f0"; ctx.fill(); }
    else { ctx.strokeStyle = "#888"; ctx.lineWidth = 1; ctx.stroke(); }
  });
  ctx.fillStyle = "#869"; ctx.font = "10px monospace";
  ctx.fillText(String(Math.round(hi)), 2, 10);
  ctx.fillText(String(Math.round(lo)), 2, h - 3);
}
async function qvLoad() {
  const tgt = $("qv-target"), flt = $("qv-filter");
  const params = new URLSearchParams();
  if (tgt && tgt.value) params.set("target", tgt.value);
  if (flt && flt.value) params.set("filter", flt.value);
  if (qvShowRaw) params.set("show_raw", "true");
  let pts = [];
  try {
    const d = await (await fetch("/api/timeseries?" + params.toString())).json();
    pts = d.points || [];
  } catch (e) { pts = []; }
  const empty = $("qv-empty"); if (empty) empty.hidden = pts.length > 0;
  const note = $("qv-note");
  if (note) {
    const cal = pts.filter((p) => p.calibrated).length;
    note.textContent = pts.length ? `${pts.length}프레임 · PP ${cal} / raw ${pts.length - cal}` : "데이터 없음";
  }
  drawTimeSeries($("qv-bg"), pts, "background_adu", "#4cc9f0");
  drawTimeSeries($("qv-fwhm"), pts, "fwhm", "#f0b6ad");
  drawTimeSeries($("qv-stars"), pts, "star_count", "#34d399");
}
async function qvLoadTargets() {
  const sel = $("qv-target"); if (!sel || sel.dataset.f) return;
  try {
    const d = await (await fetch("/api/targets")).json();
    const arr = Array.isArray(d) ? d : (d.targets || d.items || []);
    if (arr.length) {
      sel.innerHTML = '<option value="">전체 대상</option>' + arr.map((t) => {
        const nm = _schEsc(t.name || t.target || String(t));
        return `<option value="${nm}">${nm}</option>`;
      }).join("");
    }
    sel.dataset.f = "1";
  } catch (e) { /* noop — 전체 대상만 */ }
}
function qvWire() {
  const rl = $("qv-reload"); if (rl && !rl.dataset.w) { rl.dataset.w = "1"; rl.onclick = qvLoad; }
  const tgt = $("qv-target"); if (tgt && !tgt.dataset.w) { tgt.dataset.w = "1"; tgt.onchange = qvLoad; }
  const flt = $("qv-filter"); if (flt && !flt.dataset.w) { flt.dataset.w = "1"; flt.onchange = qvLoad; }
  const mode = $("qv-mode");
  if (mode && !mode.dataset.w) {
    mode.dataset.w = "1";
    mode.querySelectorAll("button").forEach((b) => {
      b.onclick = () => {
        mode.querySelectorAll("button").forEach((x) => x.classList.remove("on"));
        b.classList.add("on"); qvShowRaw = b.dataset.r === "1"; qvLoad();
      };
    });
  }
}

// ---------- 위험 알림 (무인 운영 안전 루프) ----------
// WS type:"alert"를 받아 토스트 + CRITICAL 경보음(WebAudio) + 미확인 배지. 종(bell) 클릭 시 전체 확인.
let alertUnacked = 0;
function onAlert(a) {
  if (!a) return;
  alertUnacked += 1;
  updateAlertBadge();
  showToast(a);
  playSound(a.level === "critical" ? "alert_critical" : "alert_warn");
}
function updateAlertBadge() {
  const bell = $("alert-bell"), b = $("alert-badge");
  if (bell) bell.hidden = alertUnacked === 0;
  if (b) b.textContent = alertUnacked > 99 ? "99+" : String(alertUnacked);
}
function showToast(a) {
  let host = $("toast-host");
  if (!host) { host = document.createElement("div"); host.id = "toast-host"; document.body.appendChild(host); }
  const t = document.createElement("div");
  const crit = a.level === "critical";
  t.className = "toast " + (crit ? "crit" : "warn");
  t.innerHTML = "<b>" + (crit ? "⚠ 위험" : "주의") + "</b> " + _schEsc(a.title || "")
    + (a.detail ? '<span class="toast-d">' + _schEsc(a.detail) + "</span>" : "");
  host.appendChild(t);
  setTimeout(() => { t.classList.add("out"); setTimeout(() => t.remove(), 400); }, crit ? 12000 : 7000);
}
async function loadActiveAlerts() {
  try {
    const a = await (await fetch("/api/alerts/active")).json();
    alertUnacked = Array.isArray(a) ? a.length : 0;
    updateAlertBadge();
  } catch (e) { /* noop */ }
}
function wireAlerts() {
  const bell = $("alert-bell");
  if (bell && !bell.dataset.w) {
    bell.dataset.w = "1";
    bell.onclick = async () => {
      try { await post("/api/alerts/acknowledge", {}); } catch (e) { /* noop */ }
      alertUnacked = 0; updateAlertBadge();
    };
  }
  loadActiveAlerts();
}

// ---------- 무인 운영 (NightRunner) — 운영 탭 콕핏 ----------
// /api/nightrunner status·start·stop. 상태=active/held/idle, 큐/현재/완료·실패·스킵 집계.
// 운영 탭 활성 동안 3s 폴링(showTab에서 start/stop). 백엔드 무수정 — 읽기·시작·중지만.
function _nrSlot(it) {
  const s = it && it.slot_start, e = it && it.slot_end;
  return s ? (s + (e ? "–" + e : "")) : "";
}
function _nrRow(it, label, cls) {
  return `<tr><td>#${it.plan_id}</td><td class="sch-dim">${_schEsc(_nrSlot(it) || "—")}</td>`
    + `<td>${_schEsc(it.target || "")}</td><td><span class="sch-badge ${cls}">${label}</span></td></tr>`;
}
function renderNightRunner(s) {
  s = s || {};
  const badge = s.held ? ["보류", "st-run"] : (s.active ? ["운영중", "st-ok"] : ["대기", "st-draft"]);
  const stEl = $("nr-state"); if (stEl) { stEl.textContent = badge[0]; stEl.className = "sch-badge " + badge[1]; }
  const ph = $("nr-phase"); if (ph) ph.textContent = s.held ? (s.reason || "안전 보류") : (s.active ? (s.phase || "—") : "—");
  const done = s.done || [], failed = s.failed || [], skipped = s.skipped || [], queue = s.queue || [];
  const cnt = $("nr-counts");
  if (cnt) cnt.innerHTML = `완료 <b>${done.length}</b> · 실패 <b>${failed.length}</b> · 스킵 <b>${skipped.length}</b> · 남은 <b>${queue.length}</b>`;
  const cur = $("nr-current");
  if (cur) {
    if (s.current) { cur.hidden = false; cur.innerHTML = `▶ #${s.current.plan_id} <b>${_schEsc(s.current.target || "")}</b> <span class="sch-dim">${_schEsc(_nrSlot(s.current))}</span>`; }
    else { cur.hidden = true; cur.innerHTML = ""; }
  }
  const rows = [];
  if (s.current) rows.push(_nrRow(s.current, "실행중", "st-run"));
  queue.forEach((it) => rows.push(_nrRow(it, "대기", "st-draft")));
  done.slice(-6).forEach((it) => rows.push(_nrRow(it, "완료", "st-done")));
  failed.forEach((it) => rows.push(_nrRow(it, "실패", "st-run")));
  skipped.forEach((it) => rows.push(_nrRow(it, "스킵", "st-draft")));
  const tb = $("nr-tbody"); if (tb) tb.innerHTML = rows.join("");
  const empty = $("nr-empty"); if (empty) empty.hidden = rows.length > 0;
}
async function loadNightRunner() {
  try {
    const r = await fetch("/api/nightrunner/status");
    if (!r.ok) return;
    renderNightRunner(await r.json());
  } catch (e) { /* noop */ }
}
let nrTimer = null;
function nrStartPoll() { if (!nrTimer) nrTimer = setInterval(loadNightRunner, 3000); }
function nrStopPoll() { if (nrTimer) { clearInterval(nrTimer); nrTimer = null; } }
function wireNightRunner() {
  const st = $("nr-start"), sp = $("nr-stop");
  if (st && !st.dataset.w) {
    st.dataset.w = "1";
    st.onclick = async () => { try { await post("/api/nightrunner/start", {}); loadNightRunner(); } catch (e) { /* noop */ } };
  }
  if (sp && !sp.dataset.w) {
    sp.dataset.w = "1";
    sp.onclick = async () => { try { await post("/api/nightrunner/stop", {}); loadNightRunner(); } catch (e) { /* noop */ } };
  }
}

// ---------- 멀티나잇 캠페인 — 계획 탭 최상위 ----------
// /api/campaigns list·create·plan-night·status(PATCH). 진행률(완료/잔여/퍼센트/예상밤) +
// plan-night→잔여만 시간표로 배분(스케줄 패널 동시 갱신). 백엔드 무수정 — 기존 REST만.
const _CMP_ST = { active: ["진행중", "st-ok"], paused: ["일시정지", "st-draft"], done: ["완료", "st-done"] };
function renderCampaigns(list) {
  list = list || [];
  const empty = $("cmp-empty"); if (empty) empty.hidden = list.length > 0;
  const wrap = $("cmp-list"); if (!wrap) return;
  wrap.innerHTML = list.map((c) => {
    const st = _CMP_ST[c.status] || [c.status || "—", "st-draft"];
    const pct = Math.max(0, Math.min(100, Number(c.percent) || 0));
    const nights = c.est_nights ? `${c.est_nights}밤` : "—";
    return `<div class="cmp-row">
      <div class="cmp-main">
        <div class="cmp-top"><b>${_schEsc(c.name)}</b> <span class="sch-dim">· ${_schEsc(c.target_set)}</span>
          <span class="sch-badge ${st[1]} cmp-st" data-cmp-st="${c.id}" data-status="${_schEsc(c.status)}" title="클릭: 진행 ↔ 일시정지">${st[0]}</span></div>
        <div class="cmp-bar"><div class="cmp-fill" style="width:${pct}%"></div></div>
      </div>
      <div class="cmp-stat"><span>${c.done}/${c.total} <b>${pct}%</b></span><span class="sch-dim">잔여 ${c.remaining} · ${nights}</span></div>
      <button class="btn btn-sm go" data-cmp-plan="${c.id}" title="오늘 밤 잔여 대상을 비겹침 시간표로 배분">▶ plan-night</button>
    </div>`;
  }).join("");
}
async function loadCampaigns() {
  try {
    const r = await fetch("/api/campaigns");
    if (!r.ok) { renderCampaigns([]); return; }   // 실패 시 stale 잔존 대신 빈 상태로
    const d = await r.json();
    renderCampaigns(d.campaigns || []);
  } catch (e) { renderCampaigns([]); }
}
function wireCampaigns() {
  const nb = $("cmp-new"), form = $("cmp-form");
  if (nb && !nb.dataset.w) {
    nb.dataset.w = "1";
    nb.onclick = () => { if (form) form.hidden = !form.hidden; };
  }
  const cr = $("cmp-create");
  if (cr && !cr.dataset.w) {
    cr.dataset.w = "1";
    cr.onclick = async () => {
      const nameEl = $("cmp-name"), name = (nameEl.value || "").trim();
      if (!name) { nameEl.focus(); return; }
      try {
        await post("/api/campaigns", { name, target_set: $("cmp-set").value, per_night: Number($("cmp-pernight").value) || 6 });
        nameEl.value = ""; if (form) form.hidden = true;
        loadCampaigns();
      } catch (e) { /* noop (post가 로그) */ }
    };
  }
  const list = $("cmp-list");
  if (list && !list.dataset.w) {
    list.dataset.w = "1";
    list.onclick = async (e) => {
      const pb = e.target.closest("[data-cmp-plan]");
      if (pb) {
        pb.disabled = true;
        try {
          const r = await post(`/api/campaigns/${pb.dataset.cmpPlan}/plan-night`, {});
          loadCampaigns();
          if (typeof loadSchedule === "function") loadSchedule();   // 새 draft가 스케줄에 즉시 보이게
          logLine({ ts: nowts(), source: "ui", level: "info", msg: `캠페인 plan-night — ${r && r.count != null ? r.count : "?"}개 슬롯 배분` });
        } catch (err) { /* noop */ } finally { pb.disabled = false; }
        return;
      }
      const sb = e.target.closest("[data-cmp-st]");
      if (sb) {
        const next = sb.dataset.status === "active" ? "paused" : "active";
        try {
          await fetch(`/api/campaigns/${sb.dataset.cmpSt}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status: next }) });
          loadCampaigns();
        } catch (err) { /* noop */ }
      }
    };
  }
}

// ---------- 기상예보 — 기상 탭 (스케줄러 게이팅과 동일 소스) ----------
// /api/forecast 정시별 구름/강수확률을 24h 막대로. 색=강수확률 임계(틸<50%·앰버≥50%·레드≥80%)
// = 스케줄러 페널티/하드스킵 경계와 동일. 읽기 전용.
function renderForecast(d) {
  const g = $("fc-graph"); if (!g) return;
  const hrs = (d && d.hours) || [];
  const prov = $("fc-provider"); if (prov) prov.textContent = (d && d.provider) ? ("provider: " + d.provider) : "";
  g.innerHTML = hrs.map((h) => {
    const cloud = Math.round((Number(h.cloud_frac) || 0) * 100);
    const pp = Number(h.precip_prob) || 0;
    const pct = Math.round(pp * 100);
    const cls = pp >= 0.8 ? "red" : (pp >= 0.5 ? "amber" : "teal");
    const hhmm = (h.time_utc || "").slice(11, 16);
    // 강수확률은 색으로만 구분되므로 aria-label로 텍스트 대체 제공(WCAG 1.4.1)
    return `<div class="fc-bar ${cls}" style="height:${Math.max(3, cloud)}%" role="img" aria-label="${hhmm} 구름 ${cloud}% 강수 ${pct}%" title="${hhmm} UTC · 구름 ${cloud}% · 강수 ${pct}%"></div>`;
  }).join("");
}
async function loadForecast() {
  try {
    const r = await fetch("/api/forecast?hours=24");
    if (!r.ok) { renderForecast({ hours: [] }); return; }
    renderForecast(await r.json());
  } catch (e) { renderForecast({ hours: [] }); }
}

// ---------- 분산 기상 소스 — 기상 패널 하단(어느 PC가 무엇을 언제 올렸나) ----------
// /api/weather/sources(소스별 최신). 신선도(age) 뱃지: 10분↑이면 STALE. 소스 없으면 섹션 숨김.
function _wAgo(iso) {
  try {
    const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
    if (s < 60) return Math.round(s) + "초 전";
    if (s < 3600) return Math.round(s / 60) + "분 전";
    return Math.round(s / 3600) + "시간 전";
  } catch (e) { return ""; }
}
function renderWeatherSources(list) {
  list = list || [];
  const box = $("w-src"); if (box) box.hidden = list.length === 0;
  const n = $("w-src-n"); if (n) n.textContent = list.length ? `(${list.length})` : "";
  const wrap = $("w-src-list"); if (!wrap) return;
  wrap.innerHTML = list.map((s) => {
    const t = new Date(s.utc).getTime();
    const bad = isNaN(t);                                       // 잘못된 ISO → 신선으로 위장 금지
    const stale = !bad && (Date.now() - t) > 600000;            // 10분↑ = STALE
    const cls = (stale || bad) ? "st-stale" : "st-ok";          // STALE은 '실행중'(st-run) 아님 — 전용 색
    const tc = Number(s.temp_c);
    const temp = (s.temp_c == null || isNaN(tc)) ? "—" : Math.round(tc) + "°C";
    const cc = Number(s.cloud_score);
    const cloud = (s.cloud_score == null || isNaN(cc)) ? "" : " · 구름 " + cc.toFixed(2);
    const age = bad ? "시각 미상" : _wAgo(s.utc);
    return `<div class="w-src-row"><span class="w-src-id">${_schEsc(s.source_id)}</span>`
      + `<span class="sch-dim">${temp}${cloud}</span>`
      + `<span class="sch-badge ${cls}">${age}${stale ? " · STALE" : ""}</span></div>`;
  }).join("");
}
async function loadWeatherSources() {
  try {
    const r = await fetch("/api/weather/sources");
    if (!r.ok) { renderWeatherSources([]); return; }
    renderWeatherSources(await r.json());
  } catch (e) { renderWeatherSources([]); }
}

// ---------- 사운드 기반 (WebAudio 합성 — 오프라인 OK, mp3 불필요) ----------
// 이벤트별 짧은 소리를 등록제로. 새 소리는 SOUNDS에 [freq, 시작offset(s), 길이(s)] 시퀀스만 추가.
// 촬영음(프레임 저장)·알림음(경고/위험)·성공/오류·연결/해제. 음소거는 localStorage에 영속.
const SOUNDS = {
  capture:        { type: "square",   vol: 0.07, tones: [[2200, 0.0, 0.022], [1700, 0.03, 0.018]] },
  alert_warn:     { type: "square",   vol: 0.12, tones: [[880, 0.0, 0.18]] },
  alert_critical: { type: "square",   vol: 0.15, tones: [[880, 0.0, 0.14], [660, 0.18, 0.14], [880, 0.36, 0.18]] },
  success:        { type: "sine",     vol: 0.10, tones: [[660, 0.0, 0.09], [880, 0.09, 0.15]] },
  error:          { type: "sawtooth", vol: 0.10, tones: [[320, 0.0, 0.16], [200, 0.17, 0.22]] },
  connect:        { type: "sine",     vol: 0.08, tones: [[440, 0.0, 0.05], [880, 0.06, 0.08]] },
  disconnect:     { type: "sine",     vol: 0.08, tones: [[880, 0.0, 0.05], [440, 0.06, 0.10]] },
};
let _audioCtx = null, _soundMuted = false, _soundVol = 0.8;
function _ctx() {
  if (!_audioCtx) {
    const C = window.AudioContext || window.webkitAudioContext;
    if (C) _audioCtx = new C();
  }
  if (_audioCtx && _audioCtx.state === "suspended") { try { _audioCtx.resume(); } catch (e) { /**/ } }
  return _audioCtx;
}
function playSound(name) {
  if (_soundMuted) return;
  const spec = SOUNDS[name]; if (!spec) return;
  const ctx = _ctx(); if (!ctx) return;
  try {
    spec.tones.forEach((tn) => {
      const o = ctx.createOscillator(), g = ctx.createGain();
      o.type = spec.type || "square"; o.frequency.value = tn[0];
      o.connect(g); g.connect(ctx.destination);
      const t0 = ctx.currentTime + tn[1], d = tn[2], vol = (spec.vol || 0.1) * _soundVol;
      g.gain.setValueAtTime(0.0001, t0);
      g.gain.exponentialRampToValueAtTime(vol, t0 + 0.008);
      g.gain.exponentialRampToValueAtTime(0.0001, t0 + d);
      o.start(t0); o.stop(t0 + d + 0.02);
    });
  } catch (e) { /* 자동재생 차단 등 — 무시(시각 피드백은 남음) */ }
}
function updateSoundBtn() {
  const b = $("snd-btn");
  if (b) { b.textContent = _soundMuted ? "🔇" : "🔊"; b.classList.toggle("muted", _soundMuted); }
}
function toggleSound() {
  _soundMuted = !_soundMuted;
  try { localStorage.setItem("asterion.sound.muted", _soundMuted ? "1" : "0"); } catch (e) { /**/ }
  updateSoundBtn();
  if (!_soundMuted) playSound("success");   // 켤 때 확인음
}
function initSound() {
  try { _soundMuted = localStorage.getItem("asterion.sound.muted") === "1"; } catch (e) { /**/ }
  updateSoundBtn();
  const b = $("snd-btn");
  if (b && !b.dataset.w) { b.dataset.w = "1"; b.onclick = toggleSound; }
  // 자동재생 정책: 첫 사용자 제스처에 오디오 컨텍스트 활성화
  const unlock = () => { _ctx(); document.removeEventListener("pointerdown", unlock); };
  document.addEventListener("pointerdown", unlock, { once: true });
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
const TABS = ["control", "devices", "env", "plan", "ops", "analysis", "system"];
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
  schedule: 14, timeline: 6, target: 16, plots: 9, frames: 7, actions: 7,
  forge: 8, pixview: 12, connections: 7, "log-sys": 7, sysinfo: 5,
  nightrunner: 14, campaign: 12, forecast: 7,
};
// devices 탭 기본 배치 — 비대칭 미션컨트롤: 큰 Sky 모니터(좌측 세로) + 우측 계기
// 클러스터(오토플랫·마운트·카메라) + 하단 와이드(포커서·프레임 뷰어). 12열 무빈칸 타일.
const PROTO_GS_LAYOUT = {
  // 장비(devices) — 컴팩트. 상단 [Sky | 오토플랫] (큰 viz), 중단 [마운트|카메라|포커서]
  // 좁은 3열(readout이 space-between으로 폭 채워 narrow가 딱 맞음), 하단 Imaging 풀폭.
  sky:     { x: 0, y: 0,  w: 6, h: 11 },
  skyflat: { x: 6, y: 0,  w: 6, h: 11 },
  mount:   { x: 0, y: 11, w: 4, h: 15 },
  camera:  { x: 4, y: 11, w: 4, h: 15 },
  focuser: { x: 8, y: 11, w: 4, h: 9  },
  image:   { x: 8, y: 20, w: 4, h: 8  },
  // 기상(env) — 좌측 안전·기상(바닥 y18), 우측 위성·CCTV 와이드(바닥 y18)
  safety:       { x: 0, y: 0, w: 4, h: 8  },
  weather:      { x: 0, y: 8, w: 4, h: 10 },
  "embed-sat":  { x: 4, y: 0, w: 8, h: 9  },
  "embed-cctv": { x: 4, y: 9, w: 8, h: 9  },
  forecast:     { x: 0, y: 18, w: 12, h: 7 },
  // 계획(plan) — 캠페인(여러밤)이 최상위, 그 아래 스케줄(오늘밤)→타임라인→FOV→추천→대상
  campaign: { x: 0, y: 0,  w: 12, h: 12 },
  schedule: { x: 0, y: 12, w: 12, h: 14 },
  timeline: { x: 0, y: 26, w: 12, h: 9 },
  fov:      { x: 0, y: 35, w: 12, h: 13 },
  tonight:  { x: 0, y: 48, w: 12, h: 17 },
  target:   { x: 0, y: 65, w: 12, h: 16 },
  // 운영(ops)
  nightrunner: { x: 0, y: 0, w: 12, h: 14 },
  // 분석(analysis) — 차트 풀폭 상단, 프레임·액션 하단 2열(바닥 맞춤)
  plots:   { x: 0, y: 0,  w: 12, h: 10 },
  frames:  { x: 0, y: 10, w: 6,  h: 8  },
  actions: { x: 6, y: 10, w: 6,  h: 8  },
  forge:   { x: 0, y: 18, w: 12, h: 8  },
  pixview: { x: 0, y: 26, w: 12, h: 12 },
  // 시스템(system) — 연결 + 로그 상단(바닥 맞춤), 시스템정보 풀폭 하단
  connections: { x: 0, y: 0,  w: 8,  h: 20 },
  "log-sys":   { x: 8, y: 0,  w: 4,  h: 20 },
  sysinfo:     { x: 0, y: 20, w: 12, h: 11 },
};

// 패널별 sizing 정의 — 비율잠금은 viz만(폼은 내용이 안 늘어나 무의미), control은 min/max로
// '내용 이상 못 늘어나게' 막아 여백 제거. ar=[w,h]는 viz의 박스 목표비(잠금/스냅 대상).
// fills: true=viz채움, 'gauge'/'scroll'=부분채움, false=폼. (defH는 라이브 측정 반영)
const PANEL_DEF = {
  sky:          { klass: "viz",     fills: true,     ar: [6, 7],  minW: 4, minH: 9,  defW: 6,  defH: 12, maxW: 9 },
  skyflat:      { klass: "mixed",   fills: "gauge",  ar: null,    minW: 6, minH: 9,  defW: 6,  defH: 10, maxW: 8 },
  mount:        { klass: "control", fills: false,    ar: null,    minW: 4, minH: 9,  defW: 6,  defH: 14, maxW: 6, maxH: 16 },
  camera:       { klass: "control", fills: false,    ar: null,    minW: 4, minH: 10, defW: 6,  defH: 15, maxW: 6, maxH: 17 },
  focuser:      { klass: "control", fills: false,    ar: null,    minW: 4, minH: 5,  defW: 6,  defH: 9,  maxW: 8, maxH: 10 },
  image:        { klass: "viz",     fills: true,     ar: [3, 2],  minW: 4, minH: 6,  defW: 6,  defH: 9,  maxW: 12 },
  safety:       { klass: "control", fills: false,    ar: null,    minW: 4, minH: 6,  defW: 5,  defH: 8,  maxW: 6, maxH: 8 },
  weather:      { klass: "mixed",   fills: false,    ar: null,    minW: 4, minH: 9,  defW: 5,  defH: 11, maxW: 7 },
  "embed-sat":  { klass: "viz",     fills: true,     ar: [16, 9], minW: 5, minH: 6,  defW: 7,  defH: 8,  maxW: 12 },
  "embed-cctv": { klass: "viz",     fills: true,     ar: [16, 9], minW: 5, minH: 6,  defW: 7,  defH: 8,  maxW: 12 },
  schedule:     { klass: "control", fills: "scroll", ar: null,    minW: 8, minH: 11, defW: 12, defH: 14, maxW: 12 },
  timeline:     { klass: "viz",     fills: true,     ar: [12, 3], minW: 8, minH: 5,  defW: 12, defH: 6,  maxW: 12 },
  nightrunner:  { klass: "control", fills: "scroll", ar: null,    minW: 8, minH: 10, defW: 12, defH: 14, maxW: 12 },
  campaign:     { klass: "control", fills: "scroll", ar: null,    minW: 8, minH: 8,  defW: 12, defH: 12, maxW: 12 },
  forecast:     { klass: "mixed",   fills: false,    ar: null,    minW: 6, minH: 5,  defW: 12, defH: 7,  maxW: 12 },
  fov:          { klass: "viz",     fills: false,    ar: null,    minW: 8, minH: 10, defW: 12, defH: 13, maxW: 12 },
  tonight:      { klass: "control", fills: "scroll", ar: null,    minW: 8, minH: 12, defW: 12, defH: 17, maxW: 12 },
  target:       { klass: "control", fills: "scroll", ar: null,    minW: 8, minH: 10, defW: 12, defH: 16, maxW: 12 },
  plots:        { klass: "viz",     fills: true,     ar: [12, 5], minW: 7, minH: 6,  defW: 12, defH: 9,  maxW: 12 },
  frames:       { klass: "control", fills: false,    ar: null,    minW: 4, minH: 6,  defW: 6,  defH: 9,  maxW: 12 },
  actions:      { klass: "control", fills: false,    ar: null,    minW: 4, minH: 6,  defW: 6,  defH: 9,  maxW: 12 },
  forge:        { klass: "control", fills: false,    ar: null,    minW: 6, minH: 5,  defW: 12, defH: 8,  maxW: 12 },
  pixview:      { klass: "control", fills: false,    ar: null,    minW: 8, minH: 8,  defW: 12, defH: 12, maxW: 12 },
  connections:  { klass: "control", fills: false,    ar: null,    minW: 5, minH: 8,  defW: 8,  defH: 20, maxW: 12 },
  "log-sys":    { klass: "control", fills: "scroll", ar: null,    minW: 3, minH: 8,  defW: 4,  defH: 20, maxW: 12 },
  sysinfo:      { klass: "control", fills: false,    ar: null,    minW: 6, minH: 4,  defW: 12, defH: 11, maxW: 12, maxH: 13 },
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
  if (tab === "env") { loadForecast(); loadWeatherSources(); }   // 기상예보 + 분산 소스 — 표시 시 최신
  if (tab === "plan") { wireCampaigns(); loadCampaigns(); loadSchedule(); }   // 캠페인 + AI 야간 계획
  if (tab === "ops") { wireNightRunner(); loadNightRunner(); nrStartPoll(); } else { nrStopPoll(); }   // 무인 운영 — 활성 동안만 폴링
  if (tab === "analysis") { qvWire(); qvLoadTargets(); qvLoad(); }   // 품질 추이 — PP 시계열
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
  // 미러 캔버스(Sky 등)는 읽기전용 — 클릭/줌 핸들러가 원본에만 붙으므로 상호작용 차단
  // (안 그러면 crosshair 커서로 클릭 가능해 보이지만 아무 반응 없음).
  card.querySelectorAll("canvas").forEach((c) => { c.style.pointerEvents = "none"; c.style.cursor = "default"; });
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
    applyCameraSetup();    // 비닝 옵션(caps.max_bin) + 기본값 반영
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
  if (s.forge) { wireForge(); renderForge(s.forge); }   // Forge 전처리 카드(ANALYSIS)

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
    // 수치(step) 모드는 offset 기반이라 move_axis 능력 없이도 동작 → 연결만 되면 활성.
    const stepMode = (typeof mountJogMode !== "undefined" && mountJogMode === "step");
    document.querySelectorAll(".jog-pad [data-jog]").forEach((b) => {
      const sec = (b.dataset.jog === "N" || b.dataset.jog === "S");
      b.disabled = mountBusy || (!stepMode && !(sec ? canSec : canPri));
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
  const camCon = !!c.connected;                                  // On=파랑 / Off=빨강(연결 시)
  $("btn-cooler").classList.toggle("cool-on", camCon && !!c.cooler_on);
  $("btn-cooler").classList.toggle("cool-off", camCon && !c.cooler_on);
  // 쿨러 상태 + 파워(%) — 켜졌을 때만. 램프 중=냉각/웜업, 도달=유지. 80%↑ 경고(실관측).
  const cool = s.cooler || {};
  const cstat = $("cooler-stat");
  if (cstat) {
    if (c.cooler_on) {
      cstat.hidden = false;
      let label;
      if (cool.ramping) {
        const cmd = cool.commanded == null ? "" : ` ${Number(cool.commanded).toFixed(1)}°`;
        label = cool.mode === "warming" ? `웜업${cmd}` : `냉각 중${cmd}`;
      } else {
        const stable = cool.target == null || c.ccd_temp == null
          || Math.abs(c.ccd_temp - cool.target) <= 0.5;
        label = stable ? "유지" : "냉각 중";
      }
      $("c-cooler-state").textContent = "❄ " + label;
      const pw = c.cooler_power, pe = $("c-cooler-power"), pb = $("c-power-bar");
      if (pw == null) { pe.textContent = "파워 —"; pe.className = ""; pb.style.width = "0%"; }
      else {
        pe.textContent = `파워 ${pw.toFixed(0)}%`;
        pe.className = pw >= 80 ? "err" : pw >= 70 ? "warn" : "";
        pb.style.width = clamp(pw, 0, 100) + "%";
        pb.style.background = pw >= 80 ? "var(--err)" : pw >= 70 ? "var(--warn)" : "var(--ok)";
      }
    } else { cstat.hidden = true; }
  }
  const f = s.filter || {};
  $("c-filter").textContent = f.moving ? "이동 중…" : (f.name || "—");
  const filterSignature = JSON.stringify(f.names || []);
  if (filterSignature !== filterOptionsSignature &&
      Array.isArray(f.names) && f.names.length) {
    $("sel-filter").innerHTML = f.names.map((n, i) =>
      `<option value="${i}">${n}</option>`).join("");
    filterOptionsSignature = filterSignature;
  }
  $("sel-filter").disabled = !f.connected || !!f.moving;   // 이동 중 잠금(이동=선택 즉시)
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
  else if (data.type === "frame") { prependRow("tbl-frames", frameRow(data.frame)); playSound("capture"); }
  else if (data.type === "action") {
    prependRow("tbl-actions", actionRow(data.action));
    if (data.action && data.action.success === false) playSound("error");
  }
  else if (data.type === "preview") updatePreview(data.token, data.meta);
  else if (data.type === "alert") onAlert(data.alert);
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
  wireTonight();                        // 오늘밤 베스트 토글/정렬 바인딩
  wireFov();                            // FOV 시뮬레이션 입력 바인딩
  wireSchedule();                       // AI 야간 계획 — 상태필터/새로고침 바인딩
  wireTarget();                         // 대상 페이지(Skygraph dossier) 검색 바인딩
  wireAlerts();                         // 위험 알림 종/배지 + 초기 미확인 로드
  wireNightRunner();                    // 무인 운영 시작/중지 버튼(운영 탭)
  wireCampaigns();                      // 멀티나잇 캠페인 생성/plan-night(계획 탭)
  initSound();                          // 사운드 기반(촬영음·알림음) + 음소거 토글
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
let mountJogMode = "speed";  // speed(press-hold 속도) | step(이산 수치)
let mountJogTimer = null;   // 데드맨 keepalive 인터벌
let mountJogDir = null;     // 현재 유지 중인 방향(N/S/E/W) | null
function mountStepArcsec() {           // 수치 모드 1회 이동량(arcsec) = 값 × 단위
  const v = Number(($("m-step-val") || {}).value) || 0;
  const u = Number(($("m-step-unit") || {}).value) || 1;
  return Math.max(0.1, v * u);
}
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
      if (mountJogMode === "step") {                   // 수치 모드 = 누를 때마다 이산 이동
        b.classList.add("held"); setTimeout(() => b.classList.remove("held"), 160);
        post("/api/actions/mount/jog", { direction: b.dataset.jog, arcsec: mountStepArcsec() });
        return;
      }
      stopMountJog();                                  // 속도 모드 = 유지=연속 슬루(velocity)
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
// 조그 모드 토글 — 속도(press-hold velocity) ↔ 수치(이산 스텝)
document.querySelectorAll("#m-mode-seg .seg-btn").forEach((b) => {
  b.onclick = () => {
    stopMountJog();
    mountJogMode = b.dataset.mode;
    document.querySelectorAll("#m-mode-seg .seg-btn").forEach((x) => x.classList.toggle("active", x === b));
    const step = mountJogMode === "step";
    const rw = $("m-rate-seg"), sw = $("m-step-wrap");
    if (rw) rw.hidden = step;      // 속도 모드에서만 rate 세그
    if (sw) sw.hidden = !step;     // 수치 모드에서만 스텝 입력
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

// 하늘 돔 — 좌클릭=선택, 드래그=팬, Shift/우클릭 드래그=회전, 휠=줌(커서 고정).
// 클릭/드래그는 이동량(4px)으로 구분 → 드래그 끝에 안 움직였으면 선택으로 처리.
(() => {
  const cv = $("sky-canvas");
  if (!cv) return;
  let drag = null, drawReq = 0;
  const requestDraw = () => {        // rAF 합치기 — 빠른 휠/드래그 중복 redraw 방지
    if (drawReq) return;
    drawReq = requestAnimationFrame(() => { drawReq = 0; if (lastStatus) drawSky(lastStatus); });
  };
  cv.addEventListener("pointerdown", (ev) => {
    ev.preventDefault();
    drag = { id: ev.pointerId, x0: ev.clientX, y0: ev.clientY,
             lastX: ev.clientX, lastY: ev.clientY,
             moved: false, mode: (ev.shiftKey || ev.button === 2) ? "rotate" : "pan" };
    try { cv.setPointerCapture(ev.pointerId); } catch (e) { /* noop */ }
  });
  cv.addEventListener("pointermove", (ev) => {
    if (!drag || drag.id !== ev.pointerId) return;     // 시작한 포인터만(멀티터치 누수 방지)
    if (Math.hypot(ev.clientX - drag.x0, ev.clientY - drag.y0) > 4) drag.moved = true;
    if (drag.mode === "rotate" && skyGeom) {           // Shift/우클릭 = 롤
      const rect = cv.getBoundingClientRect();
      const a0 = Math.atan2(drag.lastY - rect.top - skyGeom.cy, drag.lastX - rect.left - skyGeom.cx);
      const a1 = Math.atan2(ev.clientY - rect.top - skyGeom.cy, ev.clientX - rect.left - skyGeom.cx);
      skyView.roll += (a1 - a0);
    } else {                                           // 드래그 = 하늘 잡고 끌기(시야중심 이동)
      const G = 0.5;                                   // 감도(너무 빠르지 않게)
      panByScreen((ev.clientX - drag.lastX) * G, (ev.clientY - drag.lastY) * G);
    }
    drag.lastX = ev.clientX; drag.lastY = ev.clientY;
    requestDraw();
  });
  const endDrag = (ev) => {
    if (!drag || drag.id !== ev.pointerId) return;
    const wasClick = !drag.moved; drag = null;
    try { cv.releasePointerCapture(ev.pointerId); } catch (e) { /* noop */ }
    if (wasClick) {
      const rect = cv.getBoundingClientRect();
      skySelectAt(ev.clientX - rect.left, ev.clientY - rect.top, ev.clientX, ev.clientY);
    }
  };
  cv.addEventListener("pointerup", endDrag);
  cv.addEventListener("pointercancel", (ev) => { if (drag && drag.id === ev.pointerId) drag = null; });
  window.addEventListener("blur", () => { drag = null; });   // 탭 이탈 → 드래그 상태 정리
  cv.addEventListener("contextmenu", (e) => e.preventDefault());   // 우클릭 롤용
  cv.addEventListener("wheel", (ev) => {              // 휠 = 시야각(FOV), 중심 기준.
    ev.preventDefault();                              // 중심 고정 → 줌인/아웃 정확히 복귀(경계 넘겨도)
    // 미클램프 누산기에 쌓고 렌더 fov만 클램프 → 220/4에 박혔다 되돌려도 원위치 복귀.
    skyView.fovRaw = clamp((skyView.fovRaw || skyView.fov) * Math.exp(ev.deltaY * 0.0015), 0.5, 6000);
    skyView.fov = clamp(skyView.fovRaw, 4, 220);      // 휠↑=줌인(fov↓)
    requestDraw();
  }, { passive: false });
})();
// 뷰 리셋 (줌·팬·회전 초기화)
{ const rb = $("btn-sky-reset"); if (rb) rb.onclick = resetSkyView; }
// 표시 설정 드로어 — 앱 공용 스위치/슬라이더. 레이어·한계등급·기준선·방위·궤적.
(() => {
  const cfgBtn = $("btn-sky-cfg"), drawer = $("sky-custom"), closeBtn = $("sc-close");
  if (!cfgBtn || !drawer) return;
  const setOpen = (open) => {
    if (open) drawer.removeAttribute("hidden"); else drawer.setAttribute("hidden", "");
    cfgBtn.classList.toggle("on", open);
  };
  cfgBtn.onclick = () => setOpen(drawer.hasAttribute("hidden"));
  if (closeBtn) closeBtn.onclick = () => setOpen(false);
  const redraw = () => { if (lastStatus) drawSky(lastStatus); };
  // 레이어/기준선 토글 → skyCustom
  [["sc-messier", "messier"], ["sc-ngc", "ngc"], ["sc-stars", "stars"],
   ["sc-constel", "constellations"], ["sc-planets", "planets"], ["sc-labels", "labels"],
   ["sc-grid", "grid"], ["sc-reticle", "reticle"], ["sc-track", "track"]
  ].forEach(([id, key]) => {
    const el = $(id); if (!el) return;
    el.checked = !!skyCustom[key];
    el.onchange = () => { skyCustom[key] = el.checked; saveSkyCustom(); redraw(); };
  });
  // 방위 반전 토글 → skyFlip (별도 저장소)
  [["sc-flipew", "ew"], ["sc-flipns", "ns"]].forEach(([id, key]) => {
    const el = $(id); if (!el) return;
    el.checked = !!skyFlip[key];
    el.onchange = () => { skyFlip[key] = el.checked; saveSkyFlip(); redraw(); };
  });
  // 한계등급/궤적 슬라이더
  const slider = (id, vid, key, fmt) => {
    const el = $(id), v = $(vid); if (!el) return;
    el.value = skyCustom[key]; if (v) v.textContent = fmt(skyCustom[key]);
    el.oninput = () => { skyCustom[key] = Number(el.value); if (v) v.textContent = fmt(skyCustom[key]); saveSkyCustom(); redraw(); };
  };
  slider("sc-starmag", "sc-starmag-v", "starMag", (x) => Number(x).toFixed(1));
  slider("sc-dsomag", "sc-dsomag-v", "dsoMag", (x) => Number(x).toFixed(1));
  slider("sc-trackh", "sc-trackh-v", "trackH", (x) => x + "h");
})();

// 카메라 / 캡처
// 필터: 드롭다운 선택 = 즉시 이동 (별도 '이동' 버튼 없음)
$("sel-filter").onchange = () =>
  post("/api/actions/filter", { position: Number($("sel-filter").value) });
// 쿨러 On/Off 토글 — 셋포인트는 안 건드린다(예전: 토글이 OFF→15°로 튀던 버그 분리).
$("btn-cooler").onclick = () => {
  const on = !(lastStatus?.camera?.cooler_on);
  const sp = $("c-setpoint").value;
  post("/api/actions/camera/cooler", { on, setpoint: (on && sp !== "") ? Number(sp) : null });
};
// 온도 설정 — 입력값으로 쿨러 켜고 셋포인트 적용(토글과 분리, 끄지 않음).
$("btn-cooler-set").onclick = () => {
  const sp = $("c-setpoint").value;
  if (sp !== "") post("/api/actions/camera/cooler", { on: true, setpoint: Number(sp) });
};
function captureBody(count) {
  return {
    exposure_s: Number($("cap-exp").value),
    frame_type: $("cap-type").value,
    count,
    interval_s: Number($("cap-interval").value),
    binning: Number($("cap-bin").value) || 1,
  };
}
// 비닝 셀렉트 — 카메라 caps.max_bin까지만 노출 + setup.camera.default_binning 시드
function applyCameraSetup() {
  const sel = $("cap-bin");
  if (!sel) return;
  const dev = ((deviceConfig && deviceConfig.devices) || []).find((d) => d.key === "camera") || {};
  const maxBin = Math.max(1, Number((dev.caps && dev.caps.max_bin) || 4));
  const prev = Number(sel.value) || 0;   // 사용자가 고르던 값 (유지 우선)
  sel.innerHTML = Array.from({ length: maxBin },
    (_, i) => `<option value="${i + 1}">${i + 1}×${i + 1}</option>`).join("");
  let want = prev;
  if (!want) {   // 첫 로드 — setup.camera.default_binning 시드 ("2x2"·2 모두 허용)
    const m = String((dev.setup && dev.setup.default_binning) ?? "").match(/(\d+)/);
    if (m) want = Number(m[1]);
  }
  // max_bin이 줄어도 1×1로 소실되지 않게 클램프
  if (want) sel.value = String(Math.max(1, Math.min(maxBin, want)));
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
