"""캡처 end-to-end (sim) — 패널이 호출하는 경로를 실제 부품으로 검증.

대시보드 패널의 캡처 버튼은 /api/actions/camera/capture → CaptureService.start 를 부른다.
여기선 그 CaptureService를 실제 sim 드라이버(ConnectionManager)·실제 ActionBus·실제
fitsio 저장과 함께 돌려, '연결 → 노출 → Frame 적재 + FITS 파일 저장 + 프리뷰 콜백'까지
한 번에 확인한다. 사용자 config.local.json 오버레이의 영향을 받지 않게 기본 config.toml만
로드(Config(data, path), 오버레이 미적용)하고 데이터는 임시폴더에 격리한다.

실행: 프로젝트 루트에서  python -m unittest tests.test_capture_e2e
"""

from __future__ import annotations

import asyncio
import tempfile
import tomllib
import unittest
from pathlib import Path

from asterion.camera.capture import CaptureService
from asterion.config import DEFAULT_PATH, Config
from asterion.core.actions import ActionBus
from asterion.core.events import EventHub
from asterion.core.ontology import Db, Frame
from asterion.drivers import ConnectionManager
from asterion.drivers.sim import TwilightSim


def _clean_cfg() -> Config:
    """오버레이(config.local.json) 없이 패키지 기본 config.toml만 — sim 모드 보장."""
    with open(DEFAULT_PATH, "rb") as f:
        return Config(tomllib.load(f), DEFAULT_PATH)


class TestCaptureE2E(unittest.TestCase):
    def test_capture_persists_frame_and_file(self):
        cfg = _clean_cfg()
        self.assertEqual(str(cfg.get("drivers.mode")), "sim")
        tmp = Path(tempfile.mkdtemp())
        frames_dir = tmp / "frames"
        db = Db(tmp / "asterion.db")
        events = EventHub()
        twilight = TwilightSim()
        conn = ConnectionManager(cfg, twilight,
                                 sun_alt_fn=lambda: -30.0,   # 밤(태양 지평 아래)
                                 lst_fn=lambda: 0.0, events=events)
        bus = ActionBus(db, events, lambda: {})
        previews: list = []

        async def _preview(img, meta):
            previews.append(meta)

        capture = CaptureService(cfg, conn.drivers, bus, db, events, frames_dir,
                                 preview_cb=_preview)

        async def _run():
            await conn.connect_all()
            cam = await asyncio.to_thread(conn.drivers["camera"].status)
            self.assertTrue(cam.connected, "sim 카메라가 연결돼야 함")
            await capture.start(exposure_s=0.05, frame_type="LIGHT",
                                count=1, interval_s=0.1, binning=1)
            await capture._task          # 단발 캡처 완료까지 대기
            await conn.close_all()

        asyncio.run(_run())

        # Frame 한 건이 온톨로지에 적재됐다
        frames = db.recent(Frame, 10)
        self.assertEqual(len(frames), 1, "캡처 1건이 Frame으로 적재돼야 함")
        fr = frames[0]
        self.assertEqual(fr["image_type"], "LIGHT")
        self.assertIsNotNone(fr["median_adu"])
        self.assertEqual(fr["flag"], "ok")

        # 자동저장된 FITS 파일이 sim frames_dir 하위에 실제로 존재
        self.assertTrue(fr["file_path"], "autosave면 file_path가 채워져야 함")
        saved = Path(fr["file_path"])
        self.assertTrue(saved.exists(), f"FITS 저장 파일 없음: {saved}")
        self.assertEqual(saved.suffix, ".fits")
        self.assertIn(str(frames_dir), str(saved), "프레임은 격리 frames_dir 안에")

        # 프리뷰 콜백이 한 번 호출됐다(패널 프리뷰 갱신 경로)
        self.assertEqual(len(previews), 1)
        self.assertEqual(previews[0]["type"], "LIGHT")


if __name__ == "__main__":
    unittest.main()
