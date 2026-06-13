"""프레임 미리보기 — 16bit 이미지를 스트레치해 작은 PNG로 인코딩.

대시보드 이미지 패널이 캡처/플랫 직후의 프레임을 바로 띄울 수 있도록
robust 퍼센타일 + asinh 스트레치 후 그레이스케일 PNG 바이트를 만든다.
"""

from __future__ import annotations

import io

import numpy as np

try:
    from PIL import Image
except ImportError:  # Pillow 없으면 미리보기 비활성 (캡처/저장은 정상)
    Image = None


def have_pillow() -> bool:
    return Image is not None


def stretch_to_png(img: np.ndarray, max_dim: int = 560,
                   lo_pct: float = 1.0, hi_pct: float = 99.5,
                   asinh: float = 8.0) -> bytes | None:
    if Image is None:
        return None
    a = np.asarray(img)
    if a.ndim != 2:
        a = np.squeeze(a)
    h, w = a.shape
    step = max(1, int(max(h, w) / max_dim))
    if step > 1:
        a = a[::step, ::step]
    a = a.astype(np.float32)
    lo = float(np.percentile(a, lo_pct))
    hi = float(np.percentile(a, hi_pct))
    if hi <= lo:
        hi = lo + 1.0
    a = np.clip((a - lo) / (hi - lo), 0.0, 1.0)
    if asinh > 0:
        a = np.arcsinh(a * asinh) / np.arcsinh(asinh)
    u8 = (a * 255.0).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(u8, mode="L").save(buf, format="PNG")
    return buf.getvalue()
