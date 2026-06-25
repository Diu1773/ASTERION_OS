"""focus_model — 온도/고도→포커스 위치 선형 적합·예측(PWI3식 보정 코어).

순수 코어(fit/predict) + DB 기반 서비스(FocusModelService) + 촬영 중 적용(apply_focus_model).
"""

import asyncio
import json
import unittest
from types import SimpleNamespace

from asterion.core.focus_model import (
    FocusModelService, apply_focus_model, capture_focus_env,
    fit_focus_model, predict_position,
)


class FakeCfg:
    def __init__(self, d):
        self.d = d

    def get(self, dotted, default=None):
        return self.d.get(dotted, default)


class FakeDb:
    def __init__(self, runs):
        self._runs = runs            # 최신순 dict 리스트(recent 계약)

    def recent(self, model, limit=50):
        return self._runs[:limit]


class FakeFocuser:
    def __init__(self, position=10000, temp=None, max_position=60000, connected=True):
        self.position, self._temp = position, temp
        self.max_position, self._connected = max_position, connected
        self.moves = []

    def status(self):
        return SimpleNamespace(connected=self._connected, position=self.position,
                               temperature=self._temp, max_position=self.max_position)

    def move_to(self, target):
        self.moves.append(target)
        self.position = target


def _run(T, *, pos=None, filt="L", alt=60, key="focuser_temp_c"):
    return {"focuser_position": (10000 + 50 * T if pos is None else pos),
            "filter_name": filt,
            "environment_json": json.dumps({key: T, "altitude_deg": alt})}


class TestFitFocusModel(unittest.TestCase):
    def test_recovers_linear_temp_slope(self):
        # pos = 10000 + 50·T (steps/°C)
        pts = [(10000 + 50 * T, T, None) for T in (0, 2, 4, 6, 8, 10)]
        m = fit_focus_model(pts, use_altitude=False)
        self.assertTrue(m["ok"])
        self.assertAlmostEqual(m["c_temp"], 50.0, places=3)
        self.assertAlmostEqual(m["c0"], 10000.0, places=2)
        self.assertAlmostEqual(predict_position(m, 5.0), 10250.0, places=2)

    def test_includes_altitude(self):
        # pos = 10000 + 50·T − 3·alt
        pts = [(10000 + 50 * T - 3 * A, T, A)
               for T, A in [(0, 30), (5, 80), (10, 45), (2, 60), (8, 20), (4, 75)]]
        m = fit_focus_model(pts)
        self.assertTrue(m["ok"])
        self.assertTrue(m["use_altitude"])
        self.assertAlmostEqual(m["c_temp"], 50.0, places=2)
        self.assertAlmostEqual(m["c_alt"], -3.0, places=2)
        self.assertAlmostEqual(predict_position(m, 5.0, 50.0), 10000 + 250 - 150, places=1)

    def test_insufficient_points(self):
        m = fit_focus_model([(10000, 5, None), (10100, 7, None)], min_points=4)
        self.assertFalse(m["ok"])

    def test_temp_span_guard(self):
        # 온도 변화폭 0.5°C < 2 → 온도 계수 신뢰 불가 → 적합 거부(fail-safe)
        pts = [(10000 + i, 5.0 + 0.1 * i, None) for i in range(6)]
        m = fit_focus_model(pts, min_temp_span=2.0)
        self.assertFalse(m["ok"])

    def test_altitude_skipped_if_no_variation(self):
        # 고도 변화 <5° → 고도 항 제외(온도만)
        pts = [(10000 + 50 * T, T, 45.0) for T in (0, 2, 4, 6, 8, 10)]
        m = fit_focus_model(pts, use_altitude=True)
        self.assertTrue(m["ok"])
        self.assertFalse(m["use_altitude"])

    def test_none_temp_samples_excluded(self):
        pts = [(10000 + 50 * T, T, None) for T in (0, 2, 4, 6, 8, 10)]
        pts.append((99999, None, None))     # 온도 None → 제외
        m = fit_focus_model(pts, use_altitude=False)
        self.assertEqual(m["n"], 6)
        self.assertAlmostEqual(m["c_temp"], 50.0, places=3)

    def test_predict_none_guards(self):
        self.assertIsNone(predict_position({"ok": False}, 5.0))
        self.assertIsNone(predict_position(None, 5.0))
        self.assertIsNone(predict_position({"ok": True, "c0": 1, "c_temp": 1}, None))


class TestFocusModelService(unittest.TestCase):
    def _cfg(self, **over):
        base = {"focus_model.enabled": True, "focus_model.mode": "auto",
                "focus_model.temp_source": "focuser", "focus_model.use_altitude": True,
                "focus_model.min_points": 4, "focus_model.min_temp_span_c": 2.0}
        base.update(over)
        return FakeCfg(base)

    def test_fits_from_history(self):
        runs = [_run(T) for T in (10, 8, 6, 4, 2, 0)]      # pos=10000+50T, alt 일정
        svc = FocusModelService(self._cfg(), FakeDb(runs))
        m = svc.refit()
        self.assertTrue(m["ok"])
        self.assertAlmostEqual(m["c_temp"], 50.0, places=2)
        self.assertFalse(m["use_altitude"])               # 고도 변화 없음 → 온도만
        self.assertAlmostEqual(svc.predict(5.0, 60.0, "L"), 10250.0, places=1)
        self.assertEqual(svc.meta["n_runs"], 6)

    def test_filter_normalization(self):
        # Ha는 +300 오프셋. 정규화하면 동일 직선 → 깨끗한 적합. 예측 시 필터 오프셋 복원.
        filters = [{"name": "L", "focus_offset": 0}, {"name": "Ha", "focus_offset": 300}]
        runs = []
        for T, f in [(10, "L"), (8, "Ha"), (6, "L"), (4, "Ha"), (2, "L"), (0, "Ha")]:
            off = 0 if f == "L" else 300
            runs.append(_run(T, pos=10000 + 50 * T + off, filt=f))
        svc = FocusModelService(
            self._cfg(**{"setup.filterwheel.filters": filters}), FakeDb(runs))
        m = svc.refit()
        self.assertTrue(m["ok"])
        self.assertAlmostEqual(m["c_temp"], 50.0, places=1)
        self.assertAlmostEqual(svc.predict(5.0, 60.0, "L"), 10250.0, places=0)
        self.assertAlmostEqual(svc.predict(5.0, 60.0, "Ha"), 10550.0, places=0)  # +300

    def test_insufficient_data(self):
        svc = FocusModelService(self._cfg(), FakeDb([_run(10), _run(8)]))
        self.assertFalse(svc.refit()["ok"])

    def test_manual_mode_through_anchor(self):
        # 알려진 온도계수 -15 steps/°C, 앵커=최근 run(T=10,pos=20000).
        runs = [_run(10, pos=20000)]
        svc = FocusModelService(
            self._cfg(**{"focus_model.mode": "manual",
                         "focus_model.c_temp_steps_per_c": -15.0}), FakeDb(runs))
        m = svc.refit()
        self.assertTrue(m["ok"])
        self.assertTrue(m["manual"])
        self.assertAlmostEqual(svc.predict(10.0, None, "L"), 20000.0, places=1)  # 앵커 통과
        self.assertAlmostEqual(svc.predict(0.0, None, "L"), 20150.0, places=1)   # +150

    def test_auto_falls_back_to_manual(self):
        # auto 적합 실패(데이터 부족)인데 수동계수 있으면 폴백.
        svc = FocusModelService(
            self._cfg(**{"focus_model.c_temp_steps_per_c": -10.0}),
            FakeDb([_run(10, pos=30000)]))
        m = svc.refit()
        self.assertTrue(m["ok"])
        self.assertTrue(m.get("manual"))

    def test_temp_source_fallback(self):
        # source=focuser인데 focuser_temp_c 결측 → ambient로 폴백.
        runs = [_run(T, key="ambient_temp_c") for T in (10, 8, 6, 4, 2, 0)]
        svc = FocusModelService(self._cfg(), FakeDb(runs))
        self.assertTrue(svc.refit()["ok"])

    def test_disabled_flag(self):
        self.assertFalse(FocusModelService(
            self._cfg(**{"focus_model.enabled": False}), FakeDb([])).enabled)


class TestApplyFocusModel(unittest.TestCase):
    def _setup(self, *, foc_temp, position=10000, deadband=10, max_step=0,
               enabled=True, model_ok=True):
        cfg = FakeCfg({"focus_model.enabled": enabled,
                       "focus_model.temp_source": "focuser",
                       "focus_model.deadband_steps": deadband,
                       "focus_model.max_step_steps": max_step})
        svc = FocusModelService(cfg, FakeDb([]))
        svc.model = ({"ok": True, "c0": 10000, "c_temp": 50, "c_alt": 0,
                      "use_altitude": False} if model_ok else {"ok": False})
        foc = FakeFocuser(position=position, temp=foc_temp)
        drivers = {"focuser": foc, "mount": SimpleNamespace(
            status=lambda: SimpleNamespace(alt_degs=60.0))}
        calls = []

        async def run_action(name, params, fn):
            calls.append((name, params))
            await fn()
        return cfg, svc, foc, drivers, calls, run_action

    def test_moves_when_delta_exceeds_deadband(self):
        cfg, svc, foc, drivers, calls, ra = self._setup(foc_temp=6.0)  # 예측 10300
        res = asyncio.run(apply_focus_model(cfg, drivers, ra, svc))
        self.assertTrue(res["applied"])
        self.assertEqual(foc.moves, [10300])
        self.assertEqual(calls[0][0], "focus_model_apply")

    def test_deadband_noop(self):
        cfg, svc, foc, drivers, calls, ra = self._setup(foc_temp=0.0)  # 예측=현재 10000
        res = asyncio.run(apply_focus_model(cfg, drivers, ra, svc))
        self.assertFalse(res["applied"])
        self.assertEqual(foc.moves, [])

    def test_max_step_clamp(self):
        cfg, svc, foc, drivers, calls, ra = self._setup(foc_temp=100.0, max_step=100)
        res = asyncio.run(apply_focus_model(cfg, drivers, ra, svc))
        self.assertTrue(res["applied"])
        self.assertTrue(res["clamped"])
        self.assertEqual(foc.moves, [10100])           # 5000 요청이 +100으로 클램프

    def test_disabled_returns_none(self):
        cfg, svc, foc, drivers, calls, ra = self._setup(foc_temp=6.0, enabled=False)
        self.assertIsNone(asyncio.run(apply_focus_model(cfg, drivers, ra, svc)))

    def test_unfit_returns_none(self):
        cfg, svc, foc, drivers, calls, ra = self._setup(foc_temp=6.0, model_ok=False)
        self.assertIsNone(asyncio.run(apply_focus_model(cfg, drivers, ra, svc)))

    def test_no_temp_returns_none(self):
        cfg, svc, foc, drivers, calls, ra = self._setup(foc_temp=None)  # 포커서 온도 없음
        self.assertIsNone(asyncio.run(apply_focus_model(cfg, drivers, ra, svc)))


class TestCaptureFocusEnv(unittest.TestCase):
    def _drivers(self):
        return {
            "focuser": SimpleNamespace(status=lambda: SimpleNamespace(temperature=7.5)),
            "mount": SimpleNamespace(status=lambda: SimpleNamespace(alt_degs=42.0)),
            "weather": SimpleNamespace(read=lambda: SimpleNamespace(temp_c=3.0)),
        }

    def test_collects_all(self):
        env = capture_focus_env(self._drivers())
        self.assertEqual(env["focuser_temp_c"], 7.5)
        self.assertEqual(env["altitude_deg"], 42.0)
        self.assertEqual(env["ambient_temp_c"], 3.0)

    def test_skip_weather(self):
        env = capture_focus_env(self._drivers(), include_weather=False)
        self.assertNotIn("ambient_temp_c", env)
        self.assertIn("focuser_temp_c", env)

    def test_best_effort_on_error(self):
        def boom():
            raise RuntimeError("COM")
        env = capture_focus_env({"focuser": SimpleNamespace(status=boom),
                                 "mount": SimpleNamespace(
                                     status=lambda: SimpleNamespace(alt_degs=30.0))})
        self.assertEqual(env, {"altitude_deg": 30.0})   # 포커서 예외 삼키고 고도만


if __name__ == "__main__":
    unittest.main()
