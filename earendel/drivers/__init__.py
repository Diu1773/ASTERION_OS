"""드라이버 레지스트리 — config 기준으로 장비별 드라이버를 조립한다.

drivers.mode = "sim"  → 전부 시뮬 (마스터 스위치, 기본값)
drivers.mode = "real" → 장비별 설정을 따름. 장비별로 sim 혼용 가능
                        (예: mount만 pwi4 실물, 카메라는 sim).
real 전용 모듈(pwi4/ascom)은 선택됐을 때만 import한다.
"""

from __future__ import annotations

from typing import Any, Callable

from ..config import Config
from .sim import SimCamera, SimFilterWheel, SimMount, SimWeather, TwilightSim


def build_drivers(cfg: Config, twilight: TwilightSim,
                  sun_alt_fn: Callable[[], float],
                  lst_fn: Callable[[], float],
                  mode: str | None = None) -> dict[str, Any]:
    # mode 인자가 오면 config의 drivers.mode를 덮어쓴다 (런타임 전환용).
    mode = mode or cfg.get("drivers.mode", "sim")
    names = list(cfg.get("filters.names", ["B", "V", "R", "I"]))
    lat = float(cfg.get("site.latitude", 36.6))

    def kind(device: str) -> str:
        if mode == "sim":
            return "sim"
        return str(cfg.get(f"drivers.{device}", "sim"))

    # 필터휠
    if kind("filterwheel") == "ascom":
        from .ascom import AscomFilterWheel
        fw = AscomFilterWheel(str(cfg.get("drivers.ascom.filterwheel_progid", "")),
                              fallback_names=names)
    else:
        fw = SimFilterWheel(names)

    # 마운트
    if kind("mount") == "pwi4":
        from .pwi4 import Pwi4Mount
        mount = Pwi4Mount(str(cfg.get("drivers.pwi4.base_url", "http://127.0.0.1:8220")))
    else:
        mount = SimMount(lat, lst_fn)

    # 카메라
    if kind("camera") == "ascom":
        from .ascom import AscomCamera
        camera = AscomCamera(str(cfg.get("drivers.ascom.camera_progid", "")),
                             saturation=int(cfg.get("camera.saturation_adu", 65535)))
    else:
        camera = SimCamera(
            width=int(cfg.get("sim.image_width", 958)),
            height=int(cfg.get("sim.image_height", 639)),
            twilight=twilight,
            sun_alt_fn=sun_alt_fn,
            filter_name_fn=lambda: fw.status().name,
            exposure_sleep_cap_s=float(cfg.get("sim.exposure_sleep_cap_s", 2.0)),
            saturation=int(cfg.get("camera.saturation_adu", 65535)),
        )

    # 기상
    weather = SimWeather()  # 실물 기상 장비 어댑터는 도입 시 추가

    drivers = {"mount": mount, "camera": camera, "filterwheel": fw, "weather": weather}
    drivers["mode"] = "sim" if all(getattr(d, "is_sim", False) for d in
                                   (mount, camera, fw, weather)) else "mixed"
    if drivers["mode"] == "mixed" and not any(getattr(d, "is_sim", False) for d in
                                              (mount, camera, fw, weather)):
        drivers["mode"] = "real"
    return drivers
