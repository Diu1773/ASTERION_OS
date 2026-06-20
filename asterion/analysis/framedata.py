"""프레임 픽셀 데이터 — 이미지/픽셀값 뷰어의 백엔드 (로드맵 §10, 사용자 요청 뷰어).

저장된 FITS를 읽어 히스토그램·라인프로파일·통계를 JSON으로 낸다. 프론트 패널은
별도(플래그됨) — 여기는 데이터만. astropy가 없거나 파일이 없으면 status 코드로
정직하게 보고한다(라우터가 적절한 HTTP로 매핑).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from ..core import fitsio
from ..core.ontology import Db, Frame


class FrameData:
    def __init__(self, db: Db):
        self.db = db

    def _load(self, frame_id: int):
        """(data|None, frame_dict|None, status). status: ok/no_frame/no_file/
        no_astropy/missing_file/read_error."""
        frame = self.db.get(Frame, frame_id)
        if frame is None:
            return None, None, "no_frame"
        path = frame.get("file_path") or ""
        if not path:
            return None, frame, "no_file"
        if not fitsio.have_fits():
            return None, frame, "no_astropy"
        if not Path(path).exists():
            return None, frame, "missing_file"
        data = fitsio.load_frame(path)
        if data is None:
            return None, frame, "read_error"
        return np.asarray(data), frame, "ok"

    def stats(self, frame_id: int) -> dict[str, Any]:
        data, frame, status = self._load(frame_id)
        if status != "ok":
            return {"status": status, "frame_id": frame_id}
        flat = data.reshape(-1)
        p1, p50, p99 = (float(x) for x in np.percentile(flat, [1, 50, 99]))
        return {
            "status": "ok", "frame_id": frame_id,
            "width": int(data.shape[1]), "height": int(data.shape[0]),
            "min": float(flat.min()), "max": float(flat.max()),
            "mean": float(flat.mean()), "median": float(np.median(flat)),
            "std": float(flat.std()), "p1": p1, "p50": p50, "p99": p99,
            "image_type": frame.get("image_type"), "filter": frame.get("filter_name"),
        }

    def histogram(self, frame_id: int, bins: int = 256) -> dict[str, Any]:
        data, frame, status = self._load(frame_id)
        if status != "ok":
            return {"status": status, "frame_id": frame_id}
        bins = max(8, min(1024, int(bins)))
        counts, edges = np.histogram(data, bins=bins)
        return {
            "status": "ok", "frame_id": frame_id, "bins": bins,
            "counts": counts.astype(int).tolist(),
            "edges": [float(e) for e in edges],
            "min": float(data.min()), "max": float(data.max()),
        }

    def photometry(self, frame_id: int, r_ap: float = 6.0, r_in: float = 10.0,
                   r_out: float = 16.0, zp: float = 25.0) -> dict[str, Any]:
        """경량 조리개 측광(점광원용). 중앙영역 최대픽셀→강도가중 centroid→조리개합
        − 배경(annulus 중앙값)×면적 = flux → instrumental mag = −2.5·log10(flux)+zp, SNR.
        대상이 goto/plate-solve로 중앙에 온다고 가정. 확장천체면 조리개 내 총플럭스 의미."""
        data, frame, status = self._load(frame_id)
        if status != "ok":
            return {"status": status, "frame_id": frame_id}
        # 전체 이미지를 float64로 변환하지 않는다(대형 센서면 수십 ms 낭비) — 측광에 쓰는
        # 작은 슬라이스(box·window)만 캐스팅. argmax/슬라이싱은 원 dtype 그대로 가능.
        d = data
        h, w = d.shape
        # 대상 위치: 중앙 1/3 영역의 최대 픽셀
        cy0, cy1, cx0, cx1 = h // 3, 2 * h // 3, w // 3, 2 * w // 3
        sub = d[cy0:cy1, cx0:cx1]
        if sub.size == 0:
            return {"status": "too_small", "frame_id": frame_id}
        py, px = np.unravel_index(int(np.argmax(sub)), sub.shape)
        py, px = py + cy0, px + cx0
        # 강도가중 centroid (피크 주변 창)
        win = int(max(3, round(r_ap)))
        y0, y1, x0, x1 = max(0, py - win), min(h, py + win + 1), max(0, px - win), min(w, px + win + 1)
        box = d[y0:y1, x0:x1].astype(np.float64)
        ys, xs = np.mgrid[y0:y1, x0:x1]
        bsub = box - box.min()
        tot = float(bsub.sum())
        cy, cx = ((float((ys * bsub).sum() / tot), float((xs * bsub).sum() / tot))
                  if tot > 0 else (float(py), float(px)))
        # 조리개·배경 마스크 — centroid 주변 (r_out+1) 박스에서만 거리 계산.
        # 전체 이미지 거리맵은 대형 센서에서 수백 ms(라이트커브 N프레임에 수십 초)라 윈도우화.
        rad = int(np.ceil(r_out)) + 1
        wy0, wy1 = max(0, int(cy) - rad), min(h, int(cy) + rad + 1)
        wx0, wx1 = max(0, int(cx) - rad), min(w, int(cx) + rad + 1)
        wd = d[wy0:wy1, wx0:wx1].astype(np.float64)
        wyy, wxx = np.ogrid[wy0:wy1, wx0:wx1]
        rr = np.sqrt((wyy - cy) ** 2 + (wxx - cx) ** 2)
        ap = rr <= r_ap
        ann = (rr >= r_in) & (rr <= r_out)
        if not ap.any():
            return {"status": "no_aperture", "frame_id": frame_id}
        bg = float(np.median(wd[ann])) if ann.any() else 0.0
        ap_pix = int(ap.sum())
        ap_sum = float(wd[ap].sum())
        flux = ap_sum - bg * ap_pix
        peak = float(wd[ap].max())
        noise = float(np.sqrt(max(1.0, abs(ap_sum))))   # 포아송 근사(read/gain 무시)
        snr = flux / noise if noise > 0 else 0.0
        mag = (-2.5 * np.log10(flux) + zp) if flux > 0 else None
        return {
            "status": "ok", "frame_id": frame_id, "x": round(cx, 2), "y": round(cy, 2),
            "flux": round(flux, 2), "bg": round(bg, 2), "peak": round(peak, 1),
            "ap_pixels": ap_pix, "snr": round(float(snr), 2),
            "mag": (round(float(mag), 3) if mag is not None else None),
            "saturated": peak >= 60000,
            "filter": frame.get("filter_name"), "date_obs_utc": frame.get("date_obs_utc"),
        }

    def light_curve(self, frames: list[dict], r_ap: float = 6.0, r_in: float = 10.0,
                    r_out: float = 16.0, zp: float = 25.0) -> list[dict[str, Any]]:
        """프레임 목록(skygraph.target_light_frames)에 측광을 적용해 시간순 시계열.
        실패 프레임은 status로 표시하고 건너뛰지 않는다(빠짐 없이 보고)."""
        pts = []
        for f in frames:
            ph = self.photometry(f["id"], r_ap=r_ap, r_in=r_in, r_out=r_out, zp=zp)
            pts.append({"frame_id": f["id"],
                        "date_obs_utc": f.get("date_obs_utc") or ph.get("date_obs_utc"),
                        "filter": f.get("filter") or ph.get("filter"),
                        "status": ph.get("status"), "mag": ph.get("mag"),
                        "flux": ph.get("flux"), "snr": ph.get("snr"),
                        "saturated": ph.get("saturated")})
        pts.sort(key=lambda p: p.get("date_obs_utc") or "")
        return pts

    def profile(self, frame_id: int, axis: str = "row",
                index: int | None = None, max_len: int = 2048) -> dict[str, Any]:
        data, frame, status = self._load(frame_id)
        if status != "ok":
            return {"status": status, "frame_id": frame_id}
        h, w = int(data.shape[0]), int(data.shape[1])
        if axis == "col":
            idx = w // 2 if index is None else max(0, min(w - 1, int(index)))
            line = data[:, idx]
        else:
            axis = "row"
            idx = h // 2 if index is None else max(0, min(h - 1, int(index)))
            line = data[idx, :]
        # JSON 크기 제한 — 큰 센서(수천 px)는 스트라이드 다운샘플
        step = max(1, len(line) // max_len)
        vals = line[::step]
        return {
            "status": "ok", "frame_id": frame_id, "axis": axis, "index": idx,
            "length": int(len(line)), "step": int(step),
            "values": [float(v) for v in vals],
        }
