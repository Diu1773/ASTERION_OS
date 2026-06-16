"""돔 슬레이빙 기하 — 망원경 광축이 돔 구(球)를 뚫는 방위 계산.

순수 함수(하드웨어 0). 망원경 광축은 보통 돔 회전중심에서 벗어나 있어(가대가
돔 중심에 없거나, 독일식 적도의의 경통이 축에서 옆으로 빠짐) **돔 방위 ≠ 망원경
방위**다. 그래서 광축을 '돔 중심에서 변위된 점 P에서 출발하는 반직선'으로 보고
반경 R 구와의 교점을 구해 그 방위를 돔 목표로 삼는다.

좌표계: 돔 중심 원점, ENU(동/북/상). 방위는 북에서 동으로 증가(천문 관례).
입력 오프셋·반경 단위는 미터(상대값만 의미 있으므로 단위 일관성만 지키면 됨).

한계: GEM 경통 측면 오프셋은 피어사이드 부호로 단순 적용한다(동/서). 엄밀한 GEM
모델은 HA/Dec·경통 자세까지 필요 — 추후 정밀화 여지(인터페이스는 그대로).
"""

from __future__ import annotations

import math


def target_dome_azimuth(mount_alt_deg: float, mount_az_deg: float, *,
                        dome_radius_m: float,
                        mount_offset_e_m: float = 0.0,
                        mount_offset_n_m: float = 0.0,
                        mount_offset_up_m: float = 0.0,
                        gem_dec_offset_m: float = 0.0,
                        pier_side: str = "east") -> tuple[float, float]:
    """광축이 돔 구를 뚫는 (돔 방위°, 출구 고도°)를 돌려준다.

    mount_alt/az_deg: 망원경이 향하는 고도/방위.
    dome_radius_m: 돔 반경.
    mount_offset_*: 돔 중심 대비 가대 교차점(RA·Dec 축 교차)의 E/N/Up 변위.
    gem_dec_offset_m: GEM 경통이 Dec 축에서 옆으로 빠진 거리(동서로 근사 적용).
    pier_side: "east"|"west" — gem 오프셋 부호.
    """
    R = float(dome_radius_m)
    if R <= 0:
        return mount_az_deg % 360.0, mount_alt_deg

    alt = math.radians(mount_alt_deg)
    az = math.radians(mount_az_deg)
    ca = math.cos(alt)
    # 광축 방향 단위벡터 (ENU)
    d_e = ca * math.sin(az)
    d_n = ca * math.cos(az)
    d_u = math.sin(alt)

    # 광축 출발점 P (돔 중심 기준). GEM 경통 오프셋은 동서로 근사(피어사이드 부호).
    sign = -1.0 if str(pier_side).lower().startswith("w") else 1.0
    p_e = float(mount_offset_e_m) + sign * float(gem_dec_offset_m)
    p_n = float(mount_offset_n_m)
    p_u = float(mount_offset_up_m)

    # |P + t·d|² = R²  →  t² + 2(P·d)t + (P·P - R²) = 0  (d는 단위벡터)
    b = p_e * d_e + p_n * d_n + p_u * d_u
    c = p_e * p_e + p_n * p_n + p_u * p_u - R * R
    disc = b * b - c
    if disc < 0:
        # 교점 없음(수치적 경계) → 변위 무시하고 망원경 방위로 폴백
        return mount_az_deg % 360.0, mount_alt_deg
    t = -b + math.sqrt(disc)   # 양의 방향 교점

    q_e = p_e + t * d_e
    q_n = p_n + t * d_n
    q_u = p_u + t * d_u

    dome_az = math.degrees(math.atan2(q_e, q_n)) % 360.0
    exit_alt = math.degrees(math.asin(max(-1.0, min(1.0, q_u / R))))
    return dome_az, exit_alt


def azimuth_error(current_az: float, target_az: float) -> float:
    """target - current 의 최단 부호각 (-180, 180]. +면 CW(동쪽)로 가야 함."""
    return ((target_az - current_az + 540.0) % 360.0) - 180.0
