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

from sqlalchemy import (
    Boolean, Float, ForeignKey, Integer, String, Text, create_engine, event,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


# ── 사이트 차원 프리베이크 (멀티사이트/멀티기기 대비 — 차원만 미리 박고 기능은 미래) ──
# 한 ASTERION 인스턴스 = 한 사이트. 시작 시 set_current_site()로 식별자를 정하면 이후
# 모든 물리/이벤트 INSERT의 site 컬럼 기본값에 자동 반영된다(쓰기 호출부 수정 0).
# 멀티사이트면 코디네이터가 site로 집계, 한 사이트 여러 기기면 "사이트:기기"로 넣는다.
_CURRENT_SITE = "default"


def set_current_site(name: str) -> None:
    global _CURRENT_SITE
    _CURRENT_SITE = name or "default"


def _current_site() -> str:
    return _CURRENT_SITE


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
    site: Mapped[str] = mapped_column(String(40), default=_current_site)


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
    site: Mapped[str] = mapped_column(String(40), default=_current_site)


class Frame(Base):
    __tablename__ = "frame"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int | None] = mapped_column(ForeignKey("observation_session.id"), nullable=True)
    telescope_state_id: Mapped[int | None] = mapped_column(ForeignKey("telescope_state.id"), nullable=True)
    file_path: Mapped[str] = mapped_column(Text, default="")
    image_type: Mapped[str] = mapped_column(String(16))  # FLAT / LIGHT / DARK / BIAS / TEST
    filter_name: Mapped[str] = mapped_column(String(16), default="")
    exposure_s: Mapped[float] = mapped_column(Float, default=0.0)
    binning: Mapped[int] = mapped_column(Integer, default=1)   # NxN 하드웨어 비닝
    date_obs_utc: Mapped[str] = mapped_column(String(40), default=utc_iso)
    median_adu: Mapped[float | None] = mapped_column(Float, nullable=True)
    mean_adu: Mapped[float | None] = mapped_column(Float, nullable=True)
    std_adu: Mapped[float | None] = mapped_column(Float, nullable=True)
    flag: Mapped[str] = mapped_column(String(32), default="ok")  # ok / out_of_range / aborted
    checksum: Mapped[str] = mapped_column(String(64), default="")  # Archive Recovery 무결성(sha256)
    site: Mapped[str] = mapped_column(String(40), default=_current_site)


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
    site: Mapped[str] = mapped_column(String(40), default=_current_site)


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
    site: Mapped[str] = mapped_column(String(40), default=_current_site)


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
    site: Mapped[str] = mapped_column(String(40), default=_current_site)


class Feedback(Base):
    __tablename__ = "feedback"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    utc: Mapped[str] = mapped_column(String(40), default=utc_iso)
    original_ai_judgment: Mapped[str] = mapped_column(Text, default="")
    human_correction: Mapped[str] = mapped_column(Text, default="")
    reason: Mapped[str] = mapped_column(Text, default="")


class CalibrationProduct(Base):
    """보정 마스터 프레임 (bias/dark/flat). 전처리(Forge)가 프레임에 적용할 보정을
    고를 때 kind+filter+temp+exposure+binning으로 매칭한다 (로드맵 §10.5)."""
    __tablename__ = "calibration_product"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))  # bias / dark / flat
    filter_name: Mapped[str] = mapped_column(String(16), default="")  # flat용
    temperature_c: Mapped[float | None] = mapped_column(Float, nullable=True)  # dark용
    exposure_s: Mapped[float | None] = mapped_column(Float, nullable=True)     # dark용
    gain: Mapped[float | None] = mapped_column(Float, nullable=True)
    binning: Mapped[int] = mapped_column(Integer, default=1)
    n_frames: Mapped[int] = mapped_column(Integer, default=0)   # 스택 장수
    file_path: Mapped[str] = mapped_column(Text, default="")
    quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_utc: Mapped[str] = mapped_column(String(40), default=utc_iso)
    notes: Mapped[str] = mapped_column(Text, default="")
    site: Mapped[str] = mapped_column(String(40), default=_current_site)


class TelemetrySample(Base):
    """1분 다운샘플 텔레메트리(채널별 min/mean/max). 1Hz 라이브는 인메모리 링이 들고,
    이건 재시작 후에도 남는 추세 저장(보존기간 prune). InfluxDB는 선택적 외부 싱크."""
    __tablename__ = "telemetry_sample"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    utc: Mapped[str] = mapped_column(String(40), default=utc_iso)
    site: Mapped[str] = mapped_column(String(40), default=_current_site)
    channel: Mapped[str] = mapped_column(String(64))
    vmin: Mapped[float | None] = mapped_column(Float, nullable=True)
    vmean: Mapped[float | None] = mapped_column(Float, nullable=True)
    vmax: Mapped[float | None] = mapped_column(Float, nullable=True)
    n: Mapped[int] = mapped_column(Integer, default=0)


def row_to_dict(obj: Any) -> dict[str, Any]:
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}


class Db:
    """SQLite 래퍼. WAL로 동시 읽기 허용(샘플러가 쓰는 중에도 AI/대시보드가 히스토리
    읽음) + 쓰기만 직렬화 락. create_all로 새 테이블, _sync_columns로 기존 테이블에
    누락 컬럼 자동 추가(additive 마이그레이션 — 'create_all은 컬럼 추가 안 함' 한계 보완)."""

    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            f"sqlite:///{db_path}", connect_args={"check_same_thread": False}
        )

        @event.listens_for(self.engine, "connect")
        def _pragmas(dbapi_conn, _rec):   # 연결마다: WAL(동시읽기)·NORMAL·락 대기
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA busy_timeout=5000")
            cur.close()

        Base.metadata.create_all(self.engine)
        self._sync_columns()
        self.Session = sessionmaker(self.engine, expire_on_commit=False)
        self._lock = threading.Lock()   # 쓰기 직렬화 (읽기는 WAL로 락 없이 동시)

    def _sync_columns(self) -> None:
        """모델에 있는데 기존 테이블에 없는 컬럼을 ALTER TABLE ADD COLUMN으로 채운다.
        SQLite ADD COLUMN은 비파괴적 — 기존 행은 기본값을 받는다(추가형 변경만 지원)."""
        def affinity(col) -> str:
            t = col.type.__class__.__name__.lower()
            if "int" in t or "bool" in t:
                return "INTEGER"
            if "float" in t or "real" in t or "numeric" in t:
                return "REAL"
            return "TEXT"
        with self.engine.begin() as conn:
            for table in Base.metadata.sorted_tables:
                have = {r[1] for r in conn.exec_driver_sql(
                    f'PRAGMA table_info("{table.name}")')}
                if not have:
                    continue   # 테이블 자체가 없으면 create_all이 이미 만듦
                for col in table.columns:
                    if col.name in have:
                        continue
                    aff = affinity(col)
                    ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {aff}'
                    if not col.nullable:   # NOT NULL 컬럼은 기존 행용 기본값 필요
                        ddl += " DEFAULT ''" if aff == "TEXT" else " DEFAULT 0"
                    conn.exec_driver_sql(ddl)

    def add(self, obj: Any) -> Any:
        with self._lock, self.Session() as s:
            s.add(obj)
            s.commit()
        return obj

    def update(self, fn) -> None:
        """fn(session)을 쓰기 트랜잭션으로 실행 (직렬화)."""
        with self._lock, self.Session() as s:
            fn(s)
            s.commit()

    # 읽기는 WAL 덕에 락 없이 동시 실행 (쓰기를 막지 않음)
    def recent(self, model, limit: int = 50) -> list[dict[str, Any]]:
        with self.Session() as s:
            rows = s.query(model).order_by(model.id.desc()).limit(limit).all()
        return [row_to_dict(r) for r in rows]

    def get(self, model, id_: int) -> dict[str, Any] | None:
        with self.Session() as s:
            row = s.get(model, id_)
            return row_to_dict(row) if row else None

    def query(self, fn):
        """fn(session)을 읽기 트랜잭션으로 실행하고 반환값을 돌려준다(락 없음).
        커밋하지 않으므로 ORM 객체를 그대로 내보내지 말고 fn 안에서 dict로 변환할 것."""
        with self.Session() as s:
            return fn(s)

    # ---------- 텔레메트리 영속 ----------

    def add_telemetry(self, samples: list[Any],
                      prune_before_utc: str | None = None) -> None:
        """1분 다운샘플 배치 적재 + 보존기간 지난 행 prune (있으면)."""
        with self._lock, self.Session() as s:
            if samples:
                s.add_all(samples)
            if prune_before_utc:
                s.query(TelemetrySample).filter(
                    TelemetrySample.utc < prune_before_utc).delete(
                    synchronize_session=False)
            s.commit()

    def telemetry_persisted(self, channel: str | None = None,
                            since_utc: str | None = None,
                            limit: int = 5000) -> list[dict[str, Any]]:
        def _q(s):
            q = s.query(TelemetrySample)
            if channel:
                q = q.filter(TelemetrySample.channel == channel)
            if since_utc:
                q = q.filter(TelemetrySample.utc >= since_utc)
            rows = q.order_by(TelemetrySample.id.desc()).limit(limit).all()
            return [row_to_dict(r) for r in rows]
        return self.query(_q)
