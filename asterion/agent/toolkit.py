"""에이전트 도구 — ASTERION 서비스를 인프로세스로 부른다(앱 커넥터/사이드카 불필요).

각 도구는 OpenAI function 스키마(SPECS)로 LLM에 노출되고, call()이 이름→실제 호출로
디스패치한다. 실행계(goto/run/dome)는 ActionBus를 통과 → 안전게이트가 그대로 적용.
행성 위치는 astropy로 계산(지평선 아래면 사유와 함께 거부).
"""

from __future__ import annotations

import asyncio
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from ..core.dso_catalog import DSO, TYPE_KO, match_type
from ..core.ontology import (
    ActionLog, CalibrationProduct, Decision, Frame, FocusRun,
    ObservationSession, TelemetrySample, WeatherRecord,
)

MIN_ALT_DEG = 5.0
_BODIES = {"mercury", "venus", "mars", "jupiter", "saturn", "uranus",
           "neptune", "moon", "sun"}
_KO = {"수성": "mercury", "금성": "venus", "화성": "mars", "목성": "jupiter",
       "토성": "saturn", "천왕성": "uranus", "해왕성": "neptune",
       "달": "moon", "태양": "sun"}


class ToolKit:
    def __init__(self, *, cfg, snapshot_fn: Callable[[], dict], meridian,
                 orchestrator, bus, drivers, db=None, sentinel=None):
        self.cfg = cfg
        self.snapshot = snapshot_fn
        self.meridian = meridian
        self.orch = orchestrator
        self.bus = bus
        self.drivers = drivers
        self.db = db
        self.sentinel = sentinel
        self.lat = float(cfg.get("site.latitude", 36.64))
        self.lon = float(cfg.get("site.longitude", 127.49))

    # ---------- LLM에 노출할 스키마 ----------

    @property
    def specs(self) -> list[dict]:
        def fn(name, desc, props=None, required=None):
            return {"type": "function", "function": {
                "name": name, "description": desc,
                "parameters": {"type": "object", "properties": props or {},
                               "required": required or []}}}
        return [
            fn("get_status", "관측소 현재 상태(모드·안전상태+사유·마운트·돔·기상·시각). 무엇이든 하기 전에 확인."),
            fn("planet_position", "행성/달/해의 현재 고도·방위·관측가능 여부. 지평선 아래면 observable=false.",
               {"name": {"type": "string", "description": "예: '화성','mars','목성'"}}, ["name"]),
            fn("visible_planets", "지금 지평선 위(관측 가능)인 행성 목록 — 대안 제시용."),
            fn("goto_planet", "망원경을 행성으로 슬루('보여줘'). 지평선/한계 아래면 시도 없이 사유 반환.",
               {"name": {"type": "string"}}, ["name"]),
            fn("list_plans", "관측 계획(ObservationPlan) 목록.",
               {"status": {"type": "string", "description": "draft/approved/done 등(선택)"}}),
            fn("create_plan", "관측 계획 생성(draft). 행성은 ra/dec 비워도 됨.",
               {"target_name": {"type": "string"},
                "filters": {"type": "array", "items": {"type": "string"}},
                "exposure_s": {"type": "number"}, "count_per_filter": {"type": "integer"}},
               ["target_name", "filters"]),
            fn("run_plan", "승인된 계획을 Orchestrator로 실행. 사전조건 실패 시 사유 반환.",
               {"plan_id": {"type": "integer"}}, ["plan_id"]),
            fn("dome_shutter", "돔 셔터 열기/닫기. 수동 셔터면 거부됨.",
               {"open": {"type": "boolean"}}, ["open"]),
            fn("night_report",
               "지난 관측 요약(프레임 수·타입/필터별·품질 합격/경고/불합격·날씨로 닫힌 시각"
               "·세션 완료/중단·캘리브레이션 마스터·초점). '어젯밤 어땠어' 류 질문에.",
               {"date": {"type": "string", "description": "YYYY-MM-DD(UTC). 비우면 최근 hours"},
                "hours": {"type": "number", "description": "되돌아볼 시간(기본 24)"}}),
            fn("diagnose",
               "문제 진단 — 현재 상태 + 최근 실패한 액션(감사로그) + 안전판단 이력. "
               "'마운트 왜 이래' 류 질문에.",
               {"subsystem": {"type": "string",
                              "description": "mount/dome/camera/focuser/weather(선택)"}}),
            fn("telemetry_summary",
               "텔레메트리 채널 추세(min/mean/max·최신·표본수). 채널 비우면 사용 가능 채널 목록.",
               {"channel": {"type": "string", "description": "예: temperature, humidity(선택)"},
                "hours": {"type": "number", "description": "되돌아볼 시간(기본 12)"}}),
            fn("plan_night",
               "오늘 밤 관측 시간표를 짜준다 — 관측 가능 대상을 고도·관측창·달이격으로 점수내 "
               "고르고, 자오선 근처에 비겹침 시간 슬롯으로 배분해 ObservationPlan(초안) 생성. "
               "각 계획에 슬롯 시각(KST) 포함. 예: '오늘 밤 관측 계획 짜줘', '은하로 일정 잡아줘'. "
               "생성만 하고 실행은 사용자 승인 필요.",
               {"hours": {"type": "number", "description": "앞으로 볼 시간(기본 8)"},
                "type": {"type": "string", "description": "은하/성운/성단/galaxy/nebula 등(선택, 비우면 전체)"},
                "count": {"type": "integer", "description": "생성할 계획 수(기본 5)"},
                "exposure_s": {"type": "number", "description": "노출초(선택)"},
                "count_per_filter": {"type": "integer", "description": "필터당 장수(선택)"},
                "filters": {"type": "array", "items": {"type": "string"},
                            "description": "필터 목록(선택, 기본 L)"}}),
            fn("set_goal",
               "관측 목표 설정 — 이후 plan_night이 이 목표를 따른다. goal_type=campaign이면 "
               "set(messier/all/galaxies/nebulae)의 '아직 안 찍은 것'만 + 긴급도(곧 지는 것 먼저)로 "
               "짠다. 예: '메시에 채우기를 목표로 해', '은하 캠페인 시작'.",
               {"goal_type": {"type": "string",
                              "description": "campaign / imaging_broadband / imaging_narrowband"},
                "set": {"type": "string", "description": "campaign 대상군: messier/all/galaxies/nebulae(선택)"},
                "filters": {"type": "array", "items": {"type": "string"}},
                "exposure_s": {"type": "number"}, "count_per_filter": {"type": "integer"}}),
        ]

    # ---------- 디스패치 ----------

    async def call(self, name: str, args: dict) -> dict:
        try:
            handler = getattr(self, f"_t_{name}", None)
            if handler is None:
                return {"error": f"알 수 없는 도구: {name}"}
            return await handler(args)
        except Exception as exc:  # noqa: BLE001 — 도구 오류는 LLM에 전달해 설명하게
            return {"error": f"{name} 실행 오류: {exc}"}

    # ---------- 행성 계산 (astropy) ----------

    def _planet_altaz(self, name: str) -> dict:
        key = _KO.get((name or "").strip(), (name or "").strip().lower())
        if key not in _BODIES:
            return {"error": f"'{name}' 미지원. 지원: {', '.join(sorted(_BODIES))}"}
        from astropy.coordinates import EarthLocation, AltAz, get_body
        from astropy.time import Time
        import astropy.units as u
        loc = EarthLocation(lat=self.lat * u.deg, lon=self.lon * u.deg, height=0 * u.m)
        t = Time.now()
        body = get_body(key, t, loc)
        aa = body.transform_to(AltAz(obstime=t, location=loc))
        alt = float(aa.alt.deg)
        return {"body": key, "alt_deg": round(alt, 2), "az_deg": round(float(aa.az.deg), 2),
                "ra_hours": round(float(body.ra.hour), 5),
                "dec_degs": round(float(body.dec.deg), 5),
                "above_horizon": alt > 0, "observable": alt >= MIN_ALT_DEG,
                "note": ("관측 가능" if alt >= MIN_ALT_DEG else
                         f"고도 {alt:.1f}° — 한계({MIN_ALT_DEG}°) 아래라 goto 불가")}

    # ---------- 도구 구현 ----------

    async def _t_get_status(self, _: dict) -> dict:
        s = self.snapshot() or {}
        return {k: s.get(k) for k in ("mode", "time", "sun", "safety", "mount",
                                      "dome", "camera", "weather", "orchestrator")}

    async def _t_planet_position(self, a: dict) -> dict:
        return await asyncio.to_thread(self._planet_altaz, a.get("name", ""))

    async def _t_visible_planets(self, _: dict) -> dict:
        out = []
        for key in ("mercury", "venus", "mars", "jupiter", "saturn", "moon"):
            r = await asyncio.to_thread(self._planet_altaz, key)
            if r.get("observable"):
                out.append({"body": key, "alt_deg": r["alt_deg"], "az_deg": r["az_deg"]})
        return {"observable": out, "count": len(out)}

    async def _t_goto_planet(self, a: dict) -> dict:
        pos = await asyncio.to_thread(self._planet_altaz, a.get("name", ""))
        if "error" in pos:
            return pos
        if not pos["observable"]:
            return {"ok": False, "reason": pos["note"], "alt_deg": pos["alt_deg"]}
        mount = self.drivers["mount"]
        ra, dec = pos["ra_hours"], pos["dec_degs"]
        try:
            await self.bus.run("mount_goto_radec", actor="agent",
                               params={"ra_hours": ra, "dec_degs": dec,
                                       "target": pos["body"]},
                               func=lambda: asyncio.to_thread(mount.goto_radec, ra, dec))
        except Exception as exc:
            return {"ok": False, "reason": f"goto 거부/실패: {exc}", "target": pos["body"]}
        return {"ok": True, "target": pos["body"], "alt_deg": pos["alt_deg"],
                "az_deg": pos["az_deg"]}

    async def _t_list_plans(self, a: dict) -> dict:
        return {"plans": self.meridian.list_plans(status=a.get("status"))}

    async def _t_create_plan(self, a: dict) -> dict:
        return self.meridian.create_plan(
            target_name=a["target_name"], strategy={
                "filters": a.get("filters") or ["L"],
                "exposure_s": float(a.get("exposure_s", 60.0)),
                "count_per_filter": int(a.get("count_per_filter", 10)),
                "binning": 1, "dither_arcsec": 0.0, "priority": 0})

    async def _t_run_plan(self, a: dict) -> dict:
        try:
            await self.orch.start_plan(int(a["plan_id"]))
        except Exception as exc:
            return {"ok": False, "reason": f"실행 거부/실패: {exc}"}
        return {"ok": True, "plan_id": int(a["plan_id"])}

    async def _t_dome_shutter(self, a: dict) -> dict:
        dome = self.drivers["dome"]
        st = await asyncio.to_thread(dome.status)
        if not st.can_command_shutter:
            return {"ok": False, "reason": "수동 셔터 — SW로 개폐 불가"}
        fn = dome.open_shutter if a.get("open") else dome.close_shutter
        try:
            await self.bus.run("dome_shutter", actor="agent",
                               params={"open": bool(a.get("open"))},
                               func=lambda: asyncio.to_thread(fn))
        except Exception as exc:
            return {"ok": False, "reason": f"{exc}"}
        return {"ok": True, "open": bool(a.get("open"))}

    # ---------- 5단: 리포트·진단 (읽기 전용 — ActionBus 불필요) ----------

    def _since_iso(self, hours: float) -> str:
        return (datetime.now(timezone.utc)
                - timedelta(hours=hours)).isoformat(timespec="milliseconds")

    @staticmethod
    def _in_window(rows: list[dict], field: str, date: str, since: str) -> list[dict]:
        if date:
            return [r for r in rows if str(r.get(field, "")).startswith(date)]
        return [r for r in rows if str(r.get(field, "")) >= since]

    async def _t_night_report(self, a: dict) -> dict:
        if self.db is None:
            return {"error": "DB 미연결 — 리포트 불가"}
        date = (a.get("date") or "").strip()
        hours = float(a.get("hours") or 24)
        since = self._since_iso(hours)
        win = lambda rows, f: self._in_window(rows, f, date, since)

        frames = win(self.db.recent(Frame, 4000), "date_obs_utc")
        by_type: dict = {}
        by_filter: dict = {}
        flagged: dict = {}
        for f in frames:
            t = f.get("image_type", "?")
            by_type[t] = by_type.get(t, 0) + 1
            if t == "LIGHT" and f.get("filter_name"):
                by_filter[f["filter_name"]] = by_filter.get(f["filter_name"], 0) + 1
            fl = f.get("flag", "ok")
            if fl != "ok":
                flagged[fl] = flagged.get(fl, 0) + 1

        quality = {"accepted": 0, "warning": 0, "rejected": 0}
        examples: list = []
        if self.sentinel is not None:
            for f in frames[:200]:   # 상한 — 200장까지 판정(나머지는 표본)
                v = self.sentinel.evaluate(f["id"])
                if not v:
                    continue
                quality[v["verdict"]] = quality.get(v["verdict"], 0) + 1
                if v["verdict"] == "rejected" and len(examples) < 5:
                    examples.append({"frame_id": f["id"], "reason": v["reason"]})

        wx = list(reversed(win(self.db.recent(WeatherRecord, 3000), "utc")))  # 시간순
        unsafe_from = None
        hum_max = cloud_max = None
        prev_safe = None
        for w in wx:
            h, c = w.get("humidity"), w.get("cloud_score")
            if h is not None:
                hum_max = h if hum_max is None else max(hum_max, h)
            if c is not None:
                cloud_max = c if cloud_max is None else max(cloud_max, c)
            if prev_safe is True and w.get("safe") is False and unsafe_from is None:
                unsafe_from = w.get("utc")
            prev_safe = w.get("safe")

        sessions = [{"kind": s.get("kind"), "status": s.get("status"),
                     "started": s.get("started_utc"), "ended": s.get("ended_utc")}
                    for s in win(self.db.recent(ObservationSession, 200), "started_utc")]
        masters = [{"kind": m.get("kind"), "filter": m.get("filter_name"),
                    "n_frames": m.get("n_frames"), "created": m.get("created_utc")}
                   for m in win(self.db.recent(CalibrationProduct, 200), "created_utc")]
        focus = [{"filter": fr.get("filter_name"), "best_fwhm": fr.get("best_fwhm"),
                  "utc": fr.get("utc")}
                 for fr in win(self.db.recent(FocusRun, 100), "utc")]

        return {
            "window": {"date": date or None, "hours": None if date else hours},
            "frames": {"total": len(frames), "by_type": by_type,
                       "lights_by_filter": by_filter, "flagged": flagged},
            "quality": quality, "rejected_examples": examples,
            "weather": {"records": len(wx), "first_unsafe_utc": unsafe_from,
                        "humidity_max": hum_max, "cloud_max": cloud_max},
            "sessions": sessions, "calibration_masters": masters, "focus_runs": focus,
        }

    async def _t_diagnose(self, a: dict) -> dict:
        sub = (a.get("subsystem") or "").strip().lower()
        snap = self.snapshot() or {}
        state = {k: snap.get(k) for k in
                 ("mode", "safety", "mount", "dome", "camera", "weather", "orchestrator")}
        if sub:
            state = {k: v for k, v in state.items() if sub in k} or state

        failures: list = []
        decisions: list = []
        if self.db is not None:
            for r in self.db.recent(ActionLog, 60):
                if r.get("success"):
                    continue
                if sub and sub not in str(r.get("action_type", "")).lower():
                    continue
                failures.append({"action": r.get("action_type"), "actor": r.get("actor"),
                                 "message": r.get("message"), "utc": r.get("utc")})
                if len(failures) >= 10:
                    break
            decisions = [{"source": d.get("source"), "rec": d.get("recommendation"),
                          "outcome": d.get("outcome"), "utc": d.get("utc")}
                         for d in self.db.recent(Decision, 8)]
        return {"subsystem": sub or "전체", "current_state": state,
                "recent_failures": failures, "recent_decisions": decisions}

    async def _t_telemetry_summary(self, a: dict) -> dict:
        if self.db is None:
            return {"error": "DB 미연결"}
        channel = (a.get("channel") or "").strip()
        hours = float(a.get("hours") or 12)
        since = self._since_iso(hours)
        if not channel:
            def _ch(s):
                rows = s.query(TelemetrySample.channel).distinct().all()
                return sorted({r[0] for r in rows})
            return {"available_channels": self.db.query(_ch),
                    "hint": "channel을 지정하면 추세를 줍니다."}
        rows = self.db.telemetry_persisted(channel=channel, since_utc=since, limit=5000)
        if not rows:
            return {"channel": channel, "samples": 0, "note": f"최근 {hours}h 데이터 없음"}
        vmins = [r["vmin"] for r in rows if r.get("vmin") is not None]
        vmeans = [r["vmean"] for r in rows if r.get("vmean") is not None]
        vmaxs = [r["vmax"] for r in rows if r.get("vmax") is not None]
        latest = rows[0]   # desc 정렬 → 첫 행이 최신
        return {"channel": channel, "hours": hours, "samples": len(rows),
                "min": min(vmins) if vmins else None,
                "mean": round(sum(vmeans) / len(vmeans), 3) if vmeans else None,
                "max": max(vmaxs) if vmaxs else None,
                "latest": {"utc": latest.get("utc"), "mean": latest.get("vmean")}}

    # ---------- 2단: 자동 야간계획 (plan_night) ----------

    def _radec_alt(self, ra_h, dec_d, lst_h):
        ha = (lst_h - ra_h) * 15.0
        ha = ((ha + 180) % 360 + 360) % 360 - 180
        hr = math.radians(ha); d = math.radians(dec_d); p = math.radians(self.lat)
        s = math.sin(d) * math.sin(p) + math.cos(d) * math.cos(p) * math.cos(hr)
        return math.degrees(math.asin(max(-1.0, min(1.0, s))))

    def _night_plan(self, hours, types, count, strategy, campaign=False, exclude=None,
                    idpred=None, profile=None):
        """야간 스케줄러(to_thread). 하드제약(고도≥30·다크) → merit(고도+관측창+달) →
        비겹침 시간배분(transit순 greedy) → ObservationPlan(draft). 각 계획 strategy에
        slot_start/slot_end(KST) 기록. Phase3: 달은 위상(조도)·고도까지 반영하고,
        merit의 달 가중은 프로파일별(광대역=민감 / 협대역=관대)."""
        from astropy.coordinates import AltAz, EarthLocation, SkyCoord, get_body
        from astropy.time import Time
        import astropy.units as u
        loc = EarthLocation(lat=self.lat * u.deg, lon=self.lon * u.deg, height=0 * u.m)
        now = Time.now()
        N = 16
        samp = []   # (시간오프셋h, LST, 태양고도)
        for i in range(N + 1):
            t = now + (i * hours / N) * u.hour
            lst = float(t.sidereal_time("mean", longitude=self.lon * u.deg).hour)
            sun = float(get_body("sun", t, loc)
                        .transform_to(AltAz(obstime=t, location=loc)).alt.deg)
            samp.append((i * hours / N, lst, sun))
        dark = [s for s in samp if s[2] < -10]
        use = dark or samp
        # 달: 위상(조도분율)·고도·하늘밝힘계수 + 프로파일별 달 가중
        moon, m_illum, m_alt, m_bright = self._moon_metrics(now + (hours / 2) * u.hour, loc)
        nb = (profile == "imaging_narrowband") or self._is_narrowband(strategy.get("filters"))
        w_moon = 0.02 if nb else 0.25   # 협대역(Ha/OIII/SII)은 달빛 대부분 차단 → 달이격 거의 무시
        cand = []
        for o in DSO:
            if types and o["t"] not in types:
                continue
            if idpred and not idpred(o):
                continue   # 캠페인 세트(메시에 등) 밖 제외
            label = (o["name"] or o["id"]) + (f" ({o['id']})" if o["name"] else "")
            if exclude and label in exclude:
                continue   # 이미 관측 완료 — 캠페인 '안 찍은 것'만
            alts = [(off, self._radec_alt(o["ra"], o["dec"], lst)) for off, lst, _ in use]
            peak_off, peak = max(alts, key=lambda x: x[1])
            obs = [off for off, a in alts if a >= 30]
            if peak < 30 or not obs:
                continue   # 하드 제약: 고도 30° 못 넘으면 제외(위도/저고도 대상)
            sep = float(SkyCoord(o["ra"] * 15 * u.deg, o["dec"] * u.deg).separation(moon).deg)
            cand.append({"o": o, "label": label, "peak": peak, "tr": peak_off,
                         "win": (min(obs), max(obs)), "moon": sep})
        for c in cand:   # merit: 고도 + 관측창길이 + (달이격 × 프로파일가중 × 달밝힘)
            c["score"] = (c["peak"] + (c["win"][1] - c["win"][0]) * 6
                          + c["moon"] * w_moon * m_bright)
        if campaign:   # 긴급도 — 관측창 빨리 끝나는(곧 지는) 것 먼저
            cand.sort(key=lambda c: (c["win"][1], -c["peak"]))
        else:
            cand.sort(key=lambda c: -c["score"])
        sel = cand[:max(1, count)]
        sel.sort(key=lambda c: c["tr"])   # 배치는 시간순
        dur = (len(strategy["filters"]) * strategy["exposure_s"]
               * strategy["count_per_filter"]) / 3600 + 1 / 6   # +10분 오버헤드

        def hm(off):
            d = datetime.fromtimestamp(now.unix + off * 3600 + 9 * 3600, tz=timezone.utc)
            return d.strftime("%H:%M")   # KST

        cursor, out = 0.0, []
        for c in sel:
            s = max(cursor, c["win"][0])
            if s >= c["win"][1]:
                continue   # 남은 창에 슬롯 못 맞춤
            e = min(s + dur, c["win"][1] + 0.15)
            cursor = e
            o, label = c["o"], c["label"]
            st2 = dict(strategy, slot_start=hm(s), slot_end=hm(e),
                       slot_peak_alt=round(c["peak"], 1), slot_moon_sep=round(c["moon"]),
                       slot_moon_illum=round(m_illum * 100), slot_moon_alt=round(m_alt))
            plan = self.meridian.create_plan(
                target_name=label, ra_hours=o["ra"], dec_degs=o["dec"], strategy=st2)
            out.append({"plan_id": plan.get("id"), "target": label,
                        "type": TYPE_KO.get(o["t"], o["t"]),
                        "slot": hm(s) + "–" + hm(e), "max_alt": round(c["peak"], 1),
                        "moon_sep": round(c["moon"]), "mag": o["mag"]})
        moon_sum = {"illum_pct": round(m_illum * 100), "alt": round(m_alt, 1),
                    "up": m_alt > 0, "profile": "협대역" if nb else "광대역",
                    "weighted": round(m_bright, 2)}
        return out, len(dark) > 0, moon_sum

    @staticmethod
    def _is_narrowband(filters):
        """필터 목록이 협대역(Ha/OIII/SII·n nm)인지 — merit의 달 가중 선택."""
        nb = {"HA", "HALPHA", "OIII", "O3", "SII", "S2", "NB", "NARROWBAND"}
        for f in (filters or []):
            k = str(f).upper().replace(" ", "").replace("-", "")
            if k in nb or "NM" in k:
                return True
        return False

    def _moon_metrics(self, t, loc):
        """달 위상(조도분율 0삭~1망)·고도(°)·하늘밝힘계수. astroplan 없이 태양-달
        이각으로 위상 근사: illum=(1-cos(elong))/2. 밝힘=조도×고도램프(달이 떠
        있고 높을수록↑, 지평 아래면 0) — 달빛은 달이 떠 있을 때만 하늘을 밝힌다."""
        import math

        from astropy.coordinates import AltAz, get_body
        import astropy.units as u
        moon = get_body("moon", t, loc)
        sun = get_body("sun", t, loc)
        elong = float(moon.separation(sun).deg)            # 태양-달 이각
        illum = (1 - math.cos(math.radians(elong))) / 2    # 0(삭)~1(망)
        alt = float(moon.transform_to(AltAz(obstime=t, location=loc)).alt.deg)
        bright = illum * max(0.0, min(1.0, (alt + 2) / 18)) if alt > -2 else 0.0
        return moon, illum, alt, bright

    @staticmethod
    def _dso_label(o):
        return (o["name"] or o["id"]) + (f" ({o['id']})" if o["name"] else "")

    async def _t_set_goal(self, a: dict) -> dict:
        if self.meridian is None:
            return {"error": "계획 모듈 미연결"}
        gt = (a.get("goal_type") or "campaign").strip()
        params = {k: a[k] for k in ("set", "filters", "exposure_s", "count_per_filter")
                  if a.get(k) not in (None, "")}
        self.meridian.set_goal(gt, params)
        return {"ok": True, "goal_type": gt, "params": params,
                "note": "목표 저장됨 — 이제 '계획 짜줘' 하면 이 목표대로 시간표를 짭니다."}

    async def _t_plan_night(self, a: dict) -> dict:
        if self.meridian is None:
            return {"error": "계획 모듈 미연결"}
        hours = float(a.get("hours") or 8)
        count = int(a.get("count") or 5)
        goal = self.meridian.active_goal()
        gtype = (goal.get("goal_type") if goal else "") or ""
        campaign = gtype == "campaign"
        # 프로파일: 목표가 imaging_* 면 그것, 아니면 필터로 추론(merit의 달 가중 선택)
        profile = gtype if gtype.startswith("imaging_") else (a.get("profile") or None)
        gp = goal.get("params", {}) if goal else {}
        setname = (gp.get("set") or "") if campaign else ""
        types = match_type(a.get("type")) or (match_type(setname) if campaign else None)
        idpred = (lambda o: o["id"].startswith("M")) if setname.lower() in ("messier", "메시에") else None
        exclude = self.meridian.done_target_names() if campaign else None
        src = gp if gp else a   # 목표에 저장된 필터/노출(캠페인·imaging)을 우선, 없으면 도구 인자
        strategy = {
            "filters": src.get("filters") or a.get("filters") or ["L"],
            "exposure_s": float(src.get("exposure_s", a.get("exposure_s", 120.0))),
            "count_per_filter": int(src.get("count_per_filter", a.get("count_per_filter", 10))),
            "binning": 1, "dither_arcsec": 0.0, "priority": 0,
        }
        created, had_dark, moon_sum = await asyncio.to_thread(
            self._night_plan, hours, types, count, strategy, campaign, exclude, idpred, profile)
        out = {"created": created, "count": len(created), "window_hours": hours,
               "dark_window": had_dark, "moon": moon_sum,
               "note": ("승인 대기(draft) — 실행하려면 사용자 승인 필요" if created
                        else "조건에 맞는 관측 가능 대상이 없어요(고도 30°↑·밤시간 기준)")}
        if campaign:
            done = exclude or set()
            inset = [o for o in DSO if (not idpred or idpred(o)) and (not types or o["t"] in types)]
            out["campaign"] = {"set": setname or "all",
                               "total": len(inset),
                               "done": sum(1 for o in inset if self._dso_label(o) in done),
                               "ordered_by": "긴급도(곧 지는 것 먼저)"}
        else:
            out["type"] = a.get("type") or "전체"
        return out
