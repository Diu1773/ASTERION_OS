"""Analysis 계층 REST 라우터 (Sentinel 품질 평가 + 추후 픽셀 데이터 API).

app.py는 `app.include_router(build_analysis_router(sentinel))` 한 줄만 추가한다.
이미지/픽셀 뷰어 데이터 API(히스토그램·라인프로파일)는 다음 단계에서 여기에 붙는다.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel


class CalibrationRegisterReq(BaseModel):
    kind: str                         # bias | dark | flat
    file_path: str = ""
    filter_name: str = ""             # flat용
    temperature_c: float | None = None
    exposure_s: float | None = None
    gain: float | None = None
    binning: int = 1
    n_frames: int = 0
    quality_score: float | None = None
    notes: str = ""


class ForgeToggleReq(BaseModel):
    on: bool | None = None            # 실시간 보정 on/off
    save: bool | None = None          # 보정본 FITS 저장 on/off


class ForgeConfigReq(BaseModel):
    # 설정패널/분석 프로세스 설정에서 런타임 수정 (전부 선택).
    dark_pin: str | None = None
    flat_pin: str | None = None
    bias_pin: str | None = None
    dark_tol_s: float | None = None         # dark 노출 매칭 허용오차(초)
    flat_max_age_h: float | None = None      # '당일' 플랫 인정 시간
    stack_max: int | None = None
    pedestal: float | None = None

# 프레임 데이터 로드 상태 → HTTP 코드 매핑 (astropy/파일 결손을 정직하게 보고).
_STATUS_HTTP = {
    "no_frame": (404, "프레임 없음"),
    "no_file": (409, "저장된 FITS 파일 경로 없음 (통계만 기록된 프레임)"),
    "missing_file": (409, "FITS 파일이 경로에 없음"),
    "no_astropy": (503, "astropy 미설치 — 픽셀 데이터 비가용"),
    "read_error": (500, "FITS 읽기 실패"),
}


def _check(result: dict) -> dict:
    """framedata 결과의 status가 ok가 아니면 적절한 HTTPException을 던진다."""
    status = result.get("status")
    if status == "ok":
        return result
    code, msg = _STATUS_HTTP.get(status, (500, f"알 수 없는 오류: {status}"))
    raise HTTPException(code, msg)


def build_analysis_router(sentinel: Any, framedata: Any = None,
                          calibration: Any = None, forge: Any = None) -> APIRouter:
    router = APIRouter(tags=["analysis"])

    @router.get("/api/sentinel/frames/{frame_id}")
    async def sentinel_frame(frame_id: int):
        """프레임 1장 품질 판정 (accepted/warning/rejected + metrics + 권고)."""
        verdict = sentinel.evaluate(frame_id)
        if verdict is None:
            raise HTTPException(404, f"프레임 #{frame_id} 없음")
        return verdict

    @router.get("/api/sentinel/recent")
    async def sentinel_recent(limit: int = 20):
        """최근 프레임들의 품질 판정 목록."""
        return sentinel.evaluate_recent(limit)

    # ---------- 이미지/픽셀 데이터 (뷰어 백엔드) ----------

    @router.get("/api/analysis/frames/{frame_id}/stats")
    async def frame_stats(frame_id: int):
        if framedata is None:
            raise HTTPException(503, "FrameData 미가용")
        return _check(framedata.stats(frame_id))

    @router.get("/api/analysis/frames/{frame_id}/histogram")
    async def frame_histogram(frame_id: int, bins: int = 256):
        if framedata is None:
            raise HTTPException(503, "FrameData 미가용")
        return _check(framedata.histogram(frame_id, bins))

    @router.get("/api/analysis/frames/{frame_id}/profile")
    async def frame_profile(frame_id: int, axis: str = "row",
                            index: int | None = None):
        if framedata is None:
            raise HTTPException(503, "FrameData 미가용")
        return _check(framedata.profile(frame_id, axis=axis, index=index))

    @router.get("/api/timeseries")
    async def timeseries(target: str = "", session_id: int | None = None,
                         night: str = "", filter: str = "", show_raw: bool = False):
        """PP된 품질 시계열(background/fwhm/별수) — 시계열 뷰어용. 기본 calibrated=true만,
        show_raw=true면 raw 포함. target/session_id/night/filter 필터."""
        if framedata is None:
            raise HTTPException(503, "FrameData 미가용")
        from ..core import skygraph
        return {"points": skygraph.quality_timeseries(
            framedata.db, target=target, session_id=session_id, night=night,
            filt=filter, show_raw=show_raw),
            "facets": skygraph.quality_facets(framedata.db)}   # 촬영된 대상·필터만

    # ---------- Calibration Library (§10.5) ----------

    @router.get("/api/calibration/products")
    async def calibration_list(kind: str | None = None, limit: int = 100):
        if calibration is None:
            raise HTTPException(503, "CalibrationLibrary 미가용")
        return calibration.list_products(kind=kind, limit=limit)

    @router.post("/api/calibration/products")
    async def calibration_register(req: CalibrationRegisterReq):
        if calibration is None:
            raise HTTPException(503, "CalibrationLibrary 미가용")
        try:
            return calibration.register(**req.model_dump())
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    @router.get("/api/calibration/match")
    async def calibration_match(kind: str, filter_name: str | None = None,
                                temperature_c: float | None = None,
                                exposure_s: float | None = None,
                                binning: int | None = None):
        if calibration is None:
            raise HTTPException(503, "CalibrationLibrary 미가용")
        try:
            m = calibration.find_match(kind=kind, filter_name=filter_name,
                                       temperature_c=temperature_c,
                                       exposure_s=exposure_s, binning=binning)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        if m is None:
            raise HTTPException(404, "조건에 맞는 보정 프레임 없음")
        return m

    # ---------- Forge 실시간 보정 토글 (§10.2 경량) ----------

    @router.get("/api/forge/status")
    async def forge_status():
        if forge is None:
            raise HTTPException(503, "Forge 미가용")
        return forge.status_dict()

    @router.post("/api/forge/toggle")
    async def forge_toggle(req: ForgeToggleReq):
        if forge is None:
            raise HTTPException(503, "Forge 미가용")
        forge.set_enabled(on=req.on, save=req.save)
        forge.clear_cache()   # 토글 시 마스터 캐시 무효화(새로 등록된 마스터 반영)
        return forge.status_dict()

    @router.post("/api/forge/config")
    async def forge_config(req: ForgeConfigReq):
        """설정패널/분석 프로세스 설정에서 핀·노출오차 등을 런타임 수정."""
        if forge is None:
            raise HTTPException(503, "Forge 미가용")
        return forge.update_config(**req.model_dump())

    return router
