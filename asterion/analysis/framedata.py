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
