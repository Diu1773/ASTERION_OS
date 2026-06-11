"""관측 온톨로지 v0.1 — 팔란티어식 객체 모델.

현실 세계(관측)의 객체·관계·행동·피드백을 테이블로 고정한다.
FITS 원본은 파일 저장소에 두고, 여기에는 의미(메타데이터·품질·
판단·실행 기록)만 저장한다. 로드맵 문서의 12개 객체를 그대로 구현;
일부는 스키마만 먼저 깔아두고(UserGoal, ObservationPlan, FocusRun,
Feedback) 이후 단계에서 채운다.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Base(DeclarativeBase):
    pass


class UserGoal(Base):
    __tablename__ = "user_goal"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    goal_type: Mapped[str] = mapped_column(String(64))
    required_filters: Mapped[str] = mapped_column(String(128), default="")
    quality_thresholds_json: Mapped[str] = mapped_column(Text, default="{}")
    priority: Mapped[int] = mapped_column(Integer, default=0)


class Target(Base):
    __tablename__ = "target"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    ra_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    dec_degs: Mapped[float | None] = mapped_column(Float, nullable=True)
    type: Mapped[str] = mapped_column(String(64), default="")
    magnitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")


class ObservationPlan(Base):
    __tablename__ = "observation_plan"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    target_id: Mapped[int | None] = mapped_column(ForeignKey("target.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(64))
    params_json: Mapped[str] = mapped_column(Text, default="{}")
    approval_status: Mapped[str] = mapped_column(String(32), default="draft")
    created_utc: Mapped[str] = mapped_column(String(40), default=utc_iso)


class ObservationSession(Base):
    __tablename__ = "observation_session"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(64))  # autoflat / science / focus ...
    started_utc: Mapped[str] = mapped_column(String(40), default=utc_iso)
    ended_utc: Mapped[str | None] = mapped_column(String(40), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="running")
    summary_json: Mapped[str] = mapped_column(Text, default="{}")


class TelescopeState(Base):
    __tablename__ = "telescope_state"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    utc: Mapped[str] = mapped_column(String(40), default=utc_iso)
    ra_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    dec_degs: Mapped[float | None] = mapped_column(Float, nullable=True)
    alt_degs: Mapped[float | None] = mapped_column(Float, nullable=True)
    az_degs: Mapped[float | None] = mapped_column(Float, nullable=True)
    tracking: Mapped[bool] = mapped_column(Boolean, default=False)
    slewing: Mapped[bool] = mapped_column(Boolean, default=False)


class Frame(Base):
    __tablename__ = "frame"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int | None] = mapped_column(ForeignKey("observation_session.id"), nullable=True)
    telescope_state_id: Mapped[int | None] = mapped_column(ForeignKey("telescope_state.id"), nullable=True)
    file_path: Mapped[str] = mapped_column(Text, default="")
    image_type: Mapped[str] = mapped_column(String(16))  # FLAT / LIGHT / DARK / BIAS / TEST
    filter_name: Mapped[str] = mapped_column(String(16), default="")
    exposure_s: Mapped[float] = mapped_column(Float, default=0.0)
    date_obs_utc: Mapped[str] = mapped_column(String(40), default=utc_iso)
    median_adu: Mapped[float | None] = mapped_column(Float, nullable=True)
    mean_adu: Mapped[float | None] = mapped_column(Float, nullable=True)
    std_adu: Mapped[float | None] = mapped_column(Float, nullable=True)
    flag: Mapped[str] = mapped_column(String(32), default="ok")  # ok / out_of_range / aborted


class WeatherRecord(Base):
    __tablename__ = "weather_record"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    utc: Mapped[str] = mapped_column(String(40), default=utc_iso)
    temp_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    humidity: Mapped[float | None] = mapped_column(Float, nullable=True)
    dew_point_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    wind_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    wind_dir_deg: Mapped[float | None] = mapped_column(Float, nullable=True)
    cloud_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    rain: Mapped[bool] = mapped_column(Boolean, default=False)
    safe: Mapped[bool] = mapped_column(Boolean, default=False)


class QualityMetric(Base):
    __tablename__ = "quality_metric"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    frame_id: Mapped[int] = mapped_column(ForeignKey("frame.id"))
    median_adu: Mapped[float | None] = mapped_column(Float, nullable=True)
    std_adu: Mapped[float | None] = mapped_column(Float, nullable=True)
    min_adu: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_adu: Mapped[float | None] = mapped_column(Float, nullable=True)
    saturation_frac: Mapped[float | None] = mapped_column(Float, nullable=True)
    verdict: Mapped[str] = mapped_column(String(32), default="")  # ok / out_of_range / ...
    reason: Mapped[str] = mapped_column(Text, default="")


class FocusRun(Base):
    __tablename__ = "focus_run"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    utc: Mapped[str] = mapped_column(String(40), default=utc_iso)
    filter_name: Mapped[str] = mapped_column(String(16), default="")
    focuser_position: Mapped[float | None] = mapped_column(Float, nullable=True)
    best_fwhm: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    environment_json: Mapped[str] = mapped_column(Text, default="{}")


class Decision(Base):
    __tablename__ = "decision"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    utc: Mapped[str] = mapped_column(String(40), default=utc_iso)
    source: Mapped[str] = mapped_column(String(64))  # autoflat / safety / human ...
    recommendation: Mapped[str] = mapped_column(Text, default="")
    evidence_json: Mapped[str] = mapped_column(Text, default="{}")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    approved_by: Mapped[str] = mapped_column(String(64), default="")
    outcome: Mapped[str] = mapped_column(String(64), default="")


class ActionLog(Base):
    __tablename__ = "action_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    utc: Mapped[str] = mapped_column(String(40), default=utc_iso)
    action_type: Mapped[str] = mapped_column(String(64))
    actor: Mapped[str] = mapped_column(String(64))
    params_json: Mapped[str] = mapped_column(Text, default="{}")
    input_state_json: Mapped[str] = mapped_column(Text, default="{}")
    output_state_json: Mapped[str] = mapped_column(Text, default="{}")
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    message: Mapped[str] = mapped_column(Text, default="")


class Feedback(Base):
    __tablename__ = "feedback"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    utc: Mapped[str] = mapped_column(String(40), default=utc_iso)
    original_ai_judgment: Mapped[str] = mapped_column(Text, default="")
    human_correction: Mapped[str] = mapped_column(Text, default="")
    reason: Mapped[str] = mapped_column(Text, default="")


def row_to_dict(obj: Any) -> dict[str, Any]:
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}


class Db:
    """SQLite 래퍼 — 쓰기 직렬화 락 포함 (이벤트 루프/스레드 혼용 안전)."""

    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            f"sqlite:///{db_path}", connect_args={"check_same_thread": False}
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(self.engine, expire_on_commit=False)
        self._lock = threading.Lock()

    def add(self, obj: Any) -> Any:
        with self._lock, self.Session() as s:
            s.add(obj)
            s.commit()
        return obj

    def update(self, fn) -> None:
        """fn(session)을 트랜잭션으로 실행."""
        with self._lock, self.Session() as s:
            fn(s)
            s.commit()

    def recent(self, model, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock, self.Session() as s:
            rows = s.query(model).order_by(model.id.desc()).limit(limit).all()
        return [row_to_dict(r) for r in rows]
