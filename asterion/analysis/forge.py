"""Forge — 실시간 단일 프레임 보정 (퀵룩). 로드맵 §10.2 경량판.

'찍자마자 보정된 그림'을 위한 가벼운 경로: 캡처된 LIGHT 한 장에 마스터
bias/dark/flat을 즉시 적용한다(다크·바이어스 빼고, 플랫으로 비네팅/감도 보정).
순수 numpy 배열연산이라 대형 센서도 수십 ms — scipy·서브프로세스·신규 의존성
없음. 토글이 켜져 있고 Calibration Library에 맞는 마스터가 있을 때만 동작한다.

**무거운 정밀 처리(정렬·리젝션·적분 스택)는 여기 없다.** 그건 AstralImage
서브프로세스(`core.aippi_subprocess`)로 온디맨드 '전체 전처리'에서 처리한다 —
실시간 경로를 가볍게 유지하기 위한 의도적 분리.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from ..config import Config
from ..core import fitsio
from ..core.events import EventHub
from ..core.ontology import Db, Frame


class Forge:
    def __init__(self, cfg: Config, db: Db, calibration: Any,
                 frames_dir: Path | None = None,
                 events: EventHub | None = None):
        self.cfg = cfg
        self.db = db
        self.calibration = calibration   # CalibrationLibrary (마스터 매칭/경로)
        self.frames_dir = frames_dir
        self.events = events
        self.enabled = bool(cfg.get("forge.live_calibration", False))
        # 보정본 FITS도 frames/cal/에 저장할지 (기본 꺼짐 — 프리뷰 퀵룩만)
        self.save_calibrated = bool(cfg.get("forge.save_calibrated", False))
        self.pedestal = float(cfg.get("forge.pedestal_adu", 0.0))
        # 마스터 핀 — 캡처본이 없을 때 폴백. 파일이면 그대로, 폴더면 안의 FITS를 스택.
        # ("마스터 경로" 또는 "찍은 dark들의 경로"를 한 키로 — 외부에서 찍은 것 연결).
        self.dark_pin = str(cfg.get("forge.dark_pin", "") or "")
        self.flat_pin = str(cfg.get("forge.flat_pin", "") or "")
        self.bias_pin = str(cfg.get("forge.bias_pin", "") or "")
        self.dark_tol_s = float(cfg.get("forge.dark_exposure_tol_s", 1.0))
        self.flat_max_age_h = float(cfg.get("forge.flat_max_age_hours", 24.0))
        self.stack_max = int(cfg.get("forge.stack_max_frames", 20))
        # key=(filter,exposure) → {"masters","sources","warnings"}
        self._master_cache: dict[tuple, dict[str, Any]] = {}
        self._sources: dict[str, str | None] = {}
        self._warnings: list[str] = []
        self._last: dict[str, Any] = {}

    # 런타임에 고칠 수 있는 설정(설정패널/분석 프로세스 설정에서 POST /api/forge/config).
    _CONFIG_KEYS = ("dark_pin", "flat_pin", "bias_pin", "dark_tol_s",
                    "flat_max_age_h", "stack_max", "pedestal")

    def update_config(self, **kw) -> dict[str, Any]:
        for k, v in kw.items():
            if v is None or k not in self._CONFIG_KEYS:
                continue
            cur = getattr(self, k)
            setattr(self, k, type(cur)(v) if isinstance(cur, (int, float)) else v)
        self.clear_cache()   # 설정 바뀜 → 마스터 재해석
        if self.events:
            self.events.log("forge", "보정 설정 변경됨")
        return self.status_dict()

    # ---------- 토글/상태 ----------

    def set_enabled(self, on: bool | None = None,
                    save: bool | None = None) -> None:
        if on is not None:
            self.enabled = bool(on)
        if save is not None:
            self.save_calibrated = bool(save)
        if self.events:
            self.events.log(
                "forge",
                f"실시간 보정 {'켜짐' if self.enabled else '꺼짐'}"
                f" (보정본 저장 {'켜짐' if self.save_calibrated else '꺼짐'})")

    def status_dict(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "save_calibrated": self.save_calibrated,
                "pedestal": self.pedestal, "sources": self._sources,
                "warnings": self._warnings,
                "pins": {"dark": self.dark_pin, "flat": self.flat_pin,
                         "bias": self.bias_pin},
                "dark_exposure_tol_s": self.dark_tol_s,
                "flat_max_age_hours": self.flat_max_age_h,
                "masters_cached": len(self._master_cache), "last": self._last}

    def clear_cache(self) -> None:
        self._master_cache.clear()

    # ---------- 순수 보정 (테스트 가능, 부작용 없음) ----------

    @staticmethod
    def calibrate_array(light: np.ndarray, *, bias: np.ndarray | None = None,
                        dark: np.ndarray | None = None,
                        flat: np.ndarray | None = None,
                        pedestal: float = 0.0) -> tuple[np.ndarray, list[str]]:
        """단일 프레임 보정. 사용 가능한 마스터만 적용(없으면 건너뜀). dark가 있으면
        bias 대신 dark를 뺀다(dark가 바이어스 포함이라 가정). flat은 평균 정규화 후 나눔.
        shape가 안 맞는 마스터는 무시(graceful)."""
        out = light.astype(np.float32, copy=True)
        applied: list[str] = []
        if dark is not None and dark.shape == out.shape:
            out -= dark.astype(np.float32)
            applied.append("dark")
        elif bias is not None and bias.shape == out.shape:
            out -= bias.astype(np.float32)
            applied.append("bias")
        if flat is not None and flat.shape == out.shape:
            f = flat.astype(np.float32)
            m = float(np.mean(f))
            if m > 0:
                fn = f / m
                # 0 근처 플랫값으로 나눠 폭주하는 것 방지
                fn = np.where(fn < 1e-3, 1.0, fn)
                out /= fn
                applied.append("flat")
        if pedestal:
            out += float(pedestal)
        np.clip(out, 0, None, out=out)
        return out, applied

    # ---------- 마스터 해석 (자율형: OS가 자기 캡처를 우선 안다) ----------
    #
    # kind별 우선순위:
    #   flat : 최근(flat_max_age_h내) 캡처 FLAT(필터별) 스택 = "오토플랫 당일 자동"
    #          > flat_pin = "수동지정 계속" > 오래된 캡처
    #   dark : 노출 맞는 캡처 DARK 스택 = "찍은 dark 자동" > dark_pin > 등록 라이브러리
    #   bias : 캡처 BIAS 스택 > bias_pin > 등록 라이브러리
    # 핀은 파일이면 그대로, 폴더면 안의 FITS를 스택(외부에서 찍은 것 연결).

    def _pin_paths(self, pin: str) -> list[str]:
        if not pin:
            return []
        p = Path(pin)
        if p.is_dir():   # 폴더면 하위(날짜 폴더 포함)까지 FITS를 모아 스택
            return sorted(str(x) for x in
                          list(p.rglob("*.fit")) + list(p.rglob("*.fits")))
        return [str(p)] if p.is_file() else []

    def _stack(self, paths: list[str]) -> np.ndarray | None:
        """경로들의 FITS를 median 합성(같은 shape만). 빈/실패면 None."""
        arrs: list[np.ndarray] = []
        for p in paths[:self.stack_max]:
            a = fitsio.load_frame(p)
            if a is None or (arrs and a.shape != arrs[0].shape):
                continue
            arrs.append(a.astype(np.float32))
        if not arrs:
            return None
        if len(arrs) == 1:
            return arrs[0]
        return np.median(np.stack(arrs, axis=0), axis=0)

    @staticmethod
    def _age_h(date_iso: str | None) -> float:
        try:
            dt = datetime.fromisoformat(date_iso)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0
        except Exception:
            return float("inf")

    def _captured(self, image_type: str, *, filter_name: str | None = None,
                  exposure_s: float | None = None,
                  tol_s: float = 0.0) -> list[tuple[str, str]]:
        """OS가 캡처한 보정 프레임 (Frame 테이블). [(file_path, date_obs_utc)] 최신순."""
        def _q(s):
            q = (s.query(Frame.file_path, Frame.date_obs_utc, Frame.exposure_s)
                 .filter(Frame.image_type == image_type, Frame.file_path != "",
                         Frame.flag == "ok"))
            if filter_name is not None:
                q = q.filter(Frame.filter_name == filter_name)
            rows = q.order_by(Frame.id.desc()).limit(300).all()
            return [(path, date) for path, date, exp in rows
                    if exposure_s is None or abs((exp or 0.0) - exposure_s) <= tol_s]
        return self.db.query(_q)

    def _registered(self, kind: str, *, exposure_s=None,
                    filter_name=None) -> tuple[np.ndarray | None, str | None]:
        if self.calibration is None:
            return None, None
        try:
            mm = self.calibration.find_match(kind=kind, exposure_s=exposure_s,
                                             filter_name=filter_name)
            if mm:
                a = fitsio.load_frame(mm["file_path"])
                if a is not None:
                    return a, "registered"
        except Exception:
            pass
        return None, None

    # 각 _resolve_*는 (배열|None, 출처|None, 경고|None)를 돌려준다 — 폴백/부재를 경고로.

    def _resolve_flat(self, filter_name):
        caps = self._captured("FLAT", filter_name=filter_name)
        if caps and self._age_h(caps[0][1]) <= self.flat_max_age_h:
            recent = [p for p, d in caps if self._age_h(d) <= self.flat_max_age_h]
            m = self._stack(recent)
            if m is not None:
                return m, f"captured({len(recent)})", None
        m = self._stack(self._pin_paths(self.flat_pin))
        if m is not None:
            w = f"[{filter_name}] 당일 플랫 없음 → 지정 핀 플랫 사용" if caps else None
            return m, "pin", w
        if caps:
            age = self._age_h(caps[0][1])
            m = self._stack([p for p, _ in caps])
            if m is not None:
                return m, "captured-old", \
                    f"[{filter_name}] 당일 플랫 없음 → {age:.0f}h 지난 플랫 사용"
        return None, None, f"[{filter_name}] 플랫 없음 — 비네팅/감도 미보정"

    def _resolve_dark(self, exposure_s):
        caps = self._captured("DARK", exposure_s=exposure_s, tol_s=self.dark_tol_s)
        m = self._stack([p for p, _ in caps])
        if m is not None:
            return m, f"captured({len(caps)})", None
        mismatch = bool(self._captured("DARK"))   # 다크는 있는데 노출만 안 맞음?
        m = self._stack(self._pin_paths(self.dark_pin))
        if m is not None:
            w = (f"다크 노출 {exposure_s:g}s(±{self.dark_tol_s:g}) 매칭 실패 → 지정 핀 다크 사용"
                 if mismatch else "노출 맞는 캡처 다크 없음 → 지정 핀 다크 사용")
            return m, "pin", w
        arr, src = self._registered("dark", exposure_s=exposure_s)
        if arr is not None:
            return arr, src, f"다크 노출 {exposure_s:g}s 매칭 실패 → 등록 다크 사용"
        w = (f"다크 노출 {exposure_s:g}s 매칭 실패, 다른 다크 없음 — 다크 미보정"
             if mismatch else f"다크 없음 (노출 {exposure_s:g}s)")
        return None, None, w

    def _resolve_bias(self):
        m = self._stack([p for p, _ in self._captured("BIAS")])
        if m is not None:
            return m, "captured", None
        m = self._stack(self._pin_paths(self.bias_pin))
        if m is not None:
            return m, "pin", None
        arr, src = self._registered("bias")
        if arr is not None:
            return arr, src, None
        return None, None, "바이어스 없음"

    def masters_for(self, filter_name: str | None, temperature_c: float | None,
                    exposure_s: float | None,
                    binning: int = 1) -> dict[str, np.ndarray | None]:
        """캡처(OS가 앎) > 핀(외부 경로) > 등록 순으로 bias/dark/flat을 해석(캐시).
        출처는 self._sources, 폴백·부재 경고는 self._warnings에 기록하고 emit한다
        (캐시 키당 1회만 — 매 프레임 스팸 방지)."""
        key = (filter_name or "", round(exposure_s or 0.0, 1))
        cached = self._master_cache.get(key)
        if cached is not None:
            self._sources, self._warnings = cached["sources"], cached["warnings"]
            return cached["masters"]
        try:
            flat, fs, fw = self._resolve_flat(filter_name)
            dark, ds, dw = self._resolve_dark(exposure_s)
            bias, bs, bw = self._resolve_bias()
        except Exception as exc:
            if self.events:
                self.events.log("forge", f"마스터 해석 실패: {exc}", "warn")
            flat = dark = bias = None
            fs = ds = bs = fw = dw = bw = None
        masters = {"bias": bias, "dark": dark, "flat": flat}
        sources = {"flat": fs, "dark": ds, "bias": bs}
        warnings = [w for w in (fw, dw) if w]
        # dark가 있으면 bias 부재는 정상(dark가 바이어스 포함) → 경고 생략.
        if dark is None and bias is None:
            warnings.append("다크·바이어스 모두 없음 — 오프셋 미보정")
        elif dark is None and bw:
            warnings.append(bw)
        self._sources, self._warnings = sources, warnings
        self._master_cache[key] = {"masters": masters, "sources": sources,
                                   "warnings": warnings}
        if warnings and self.events:
            for w in warnings:
                self.events.log("forge", f"보정 경고: {w}", "warn")
            self.events.emit({"type": "forge_warning", "warnings": warnings,
                              "filter": filter_name, "exposure_s": exposure_s})
        return masters

    # ---------- 실시간 처리 훅 ----------

    def process(self, img: np.ndarray,
                meta: dict[str, Any]) -> tuple[np.ndarray, dict[str, Any]]:
        """캡처 직후 호출. 비활성이거나 LIGHT가 아니면 원본 그대로 반환
        (플랫/다크/바이어스는 보정 안 함). 반환: (이미지, info)."""
        if not self.enabled or (meta.get("type") or "").upper() != "LIGHT":
            return img, {"enabled": self.enabled, "applied": [], "skipped": True}
        masters = self.masters_for(meta.get("filter"), meta.get("ccd_temp"),
                                   meta.get("exposure_s"), meta.get("binning", 1))
        cal, applied = self.calibrate_array(img, pedestal=self.pedestal, **masters)
        info = {"enabled": True, "applied": applied, "filter": meta.get("filter"),
                "sources": dict(self._sources), "warnings": list(self._warnings),
                "median_before": round(float(np.median(img)), 1),
                "median_after": round(float(np.median(cal)), 1)}
        self._last = info
        if self.events and applied:
            self.events.log("forge",
                            f"실시간 보정 [{meta.get('filter')}] {'+'.join(applied)} "
                            f"→ 중앙값 {info['median_before']:.0f}→{info['median_after']:.0f}")
        return cal, info

    def save_calibrated_fits(self, cal: np.ndarray,
                             meta: dict[str, Any]) -> str | None:
        """save_calibrated가 켜져 있으면 보정본을 frames/cal/에 FITS로 저장하고
        경로를 돌려준다(블로킹 I/O — 호출부가 thread로 감싸 호출). 아니면 None."""
        if not self.save_calibrated or self.frames_dir is None:
            return None
        try:
            path = fitsio.save_frame(
                self.frames_dir / "cal", self.cfg, cal,
                image_type="LIGHT_CAL", filter_name=str(meta.get("filter") or ""),
                exposure_s=float(meta.get("exposure_s") or 0.0),
                seq=int(meta.get("seq") or 0),
                object_name=str(meta.get("target") or ""))
            return str(path) if path else None
        except Exception as exc:
            if self.events:
                self.events.log("forge", f"보정본 저장 실패: {exc}", "warn")
            return None
