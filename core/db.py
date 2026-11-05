"""資料庫模型。SQLite（本機）與 PostgreSQL（正式）共用同一份定義。"""
from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import date, datetime, time

from sqlalchemy import (
    JSON, Boolean, Date, DateTime, ForeignKey, Integer, String, Text, Time,
    UniqueConstraint, create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker
from sqlalchemy import select

from .config import TZ


def now() -> datetime:
    return datetime.now(TZ).replace(tzinfo=None)  # 一律存台灣時間（naive）


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    line_user_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(100), default="")
    picture_url: Mapped[str | None] = mapped_column(String(500))
    role: Mapped[str | None] = mapped_column(String(20))  # trainer / requester / None(尚未選擇)
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    blocked_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    last_login_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    trainer: Mapped["TrainerProfile | None"] = relationship(back_populates="user", uselist=False)
    requester: Mapped["RequesterProfile | None"] = relationship(back_populates="user", uselist=False)


class TrainerProfile(Base):
    __tablename__ = "trainer_profiles"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    name: Mapped[str] = mapped_column(String(50))
    gender: Mapped[str] = mapped_column(String(20))
    phone: Mapped[str] = mapped_column(String(30))
    email: Mapped[str] = mapped_column(String(120))
    line_contact_id: Mapped[str | None] = mapped_column(String(60))  # 對外 LINE ID（必填），錄用後才公開
    school: Mapped[str] = mapped_column(String(80))
    graduation_year: Mapped[int] = mapped_column(Integer)
    service_counties: Mapped[list] = mapped_column(JSON, default=list)
    has_at_license: Mapped[bool] = mapped_column(Boolean, default=False)
    has_pt_license: Mapped[bool] = mapped_column(Boolean, default=False)
    first_aid_licenses: Mapped[list] = mapped_column(JSON, default=list)
    # 證號：只給管理員審核用，永遠不會顯示給發案方
    at_license_no: Mapped[str | None] = mapped_column(String(40))    # 教育部體育署 運動防護員證號
    pt_license_no: Mapped[str | None] = mapped_column(String(40))    # 物理治療師證書字號
    emt1_license_no: Mapped[str | None] = mapped_column(String(40))  # EMT-1 證號
    emt2_license_no: Mapped[str | None] = mapped_column(String(40))  # EMT-2 證號
    cpr_license_no: Mapped[str | None] = mapped_column(String(40))   # CPR+AED 證號
    experience: Mapped[str] = mapped_column(Text, default="")
    # draft / pending / approved / rejected
    review_status: Mapped[str] = mapped_column(String(20), default="pending")
    review_note: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)
    consent_at: Mapped[datetime | None] = mapped_column(DateTime)
    record_terms_at: Mapped[datetime | None] = mapped_column(DateTime)  # 同意防護紀錄的個資處理規範
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)

    user: Mapped[User] = relationship(back_populates="trainer")


class RequesterProfile(Base):
    __tablename__ = "requester_profiles"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    org_name: Mapped[str] = mapped_column(String(100))
    org_type: Mapped[str] = mapped_column(String(30))
    contact_name: Mapped[str] = mapped_column(String(50))
    phone: Mapped[str] = mapped_column(String(30))
    email: Mapped[str] = mapped_column(String(120))
    line_contact_id: Mapped[str | None] = mapped_column(String(60))
    consent_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)

    user: Mapped[User] = relationship(back_populates="requester")


class Case(Base):
    __tablename__ = "cases"
    id: Mapped[int] = mapped_column(primary_key=True)
    requester_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(120))
    sport_type: Mapped[str] = mapped_column(String(40))
    # 實際時段存在 case_sessions；以下三個欄位是彙整值，方便排序與篩選
    event_date: Mapped[date] = mapped_column(Date)                   # 第一個時段的日期
    event_end_date: Mapped[date | None] = mapped_column(Date)        # 最後一個時段的日期
    time_slot: Mapped[str] = mapped_column(String(60))               # 時間摘要（全部相同時為 08:00-17:00）
    location_county: Mapped[str] = mapped_column(String(10), index=True)
    location_detail: Mapped[str] = mapped_column(String(200))
    salary_type: Mapped[str] = mapped_column(String(10))
    salary_amount: Mapped[int] = mapped_column(Integer)
    headcount: Mapped[int] = mapped_column(Integer, default=1)  # 每個時段需要的人數
    description: Mapped[str] = mapped_column(Text, default="")
    # 條件
    license_req: Mapped[str] = mapped_column(String(20), default="none")
    require_emt: Mapped[bool] = mapped_column(Boolean, default=False)
    exp_mode: Mapped[str] = mapped_column(String(10), default="none")
    exp_years: Mapped[int] = mapped_column(Integer, default=0)
    required_school: Mapped[str | None] = mapped_column(String(80))
    is_high_risk: Mapped[bool] = mapped_column(Boolean, default=False)
    # open / closed / cancelled
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)
    notified_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime)

    @property
    def end_date(self) -> date:
        return self.event_end_date or self.event_date

    @property
    def schedule(self) -> list[tuple[date, str, str]]:
        """所有時段 [(日期, "08:00", "17:00"), ...]。舊版沒有時段明細的案件，用日期區間 + 時段字串推回。"""
        if self.sessions:
            return [(x.date, f"{x.start_time:%H:%M}", f"{x.end_time:%H:%M}") for x in self.sessions]
        start, _, end = (self.time_slot or "-").partition("-")
        days = (self.end_date - self.event_date).days + 1
        from datetime import timedelta
        return [(self.event_date + timedelta(days=i), start, end) for i in range(days)]

    requester: Mapped[User] = relationship()
    sessions: Mapped[list["CaseSession"]] = relationship(
        back_populates="case", cascade="all, delete-orphan", lazy="selectin",
        order_by="(CaseSession.date, CaseSession.start_time)",
    )
    applications: Mapped[list["Application"]] = relationship(
        back_populates="case", cascade="all, delete-orphan"
    )


class CaseSession(Base):
    """賽會時段：一個案件可以有多個（例如連續兩週的週六、週日）。"""
    __tablename__ = "case_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)

    case: Mapped[Case] = relationship(back_populates="sessions")


class Application(Base):
    __tablename__ = "applications"
    __table_args__ = (UniqueConstraint("case_id", "trainer_id", name="uq_case_trainer"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    trainer_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    applied_at: Mapped[datetime] = mapped_column(DateTime, default=now)  # 應徵時間戳記（排序第三權重）
    # applied / hired / not_selected / withdrawn
    status: Mapped[str] = mapped_column(String(20), default="applied")
    hired_at: Mapped[datetime | None] = mapped_column(DateTime)

    case: Mapped[Case] = relationship(back_populates="applications")
    trainer_user: Mapped[User] = relationship()
    slots: Mapped[list["ApplicationSession"]] = relationship(
        back_populates="application", cascade="all, delete-orphan", lazy="selectin")

    @property
    def available_ids(self) -> set[int]:
        return {x.session_id for x in self.slots}

    @property
    def hired_ids(self) -> set[int]:
        return {x.session_id for x in self.slots if x.status == "hired"}


class ApplicationSession(Base):
    """應徵者「可以出席」的時段；被錄用的時段 status = hired。"""
    __tablename__ = "application_sessions"
    __table_args__ = (UniqueConstraint("application_id", "session_id", name="uq_app_session"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id"), index=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("case_sessions.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="available")  # available / hired
    hired_at: Mapped[datetime | None] = mapped_column(DateTime)

    application: Mapped[Application] = relationship(back_populates="slots")
    session: Mapped[CaseSession] = relationship(lazy="joined")


class CaseQuestion(Base):
    """防護員對案件的提問，發案方回覆。"""
    __tablename__ = "case_questions"
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    trainer_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str | None] = mapped_column(Text)
    is_public: Mapped[bool] = mapped_column(Boolean, default=False)  # 公開給所有看此案件的防護員（匿名）
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime)

    case: Mapped[Case] = relationship()


class Subscription(Base):
    """付費功能的使用權。可以有多筆（試用、付費、管理員贈送），以 ends_at 最晚的一筆為準。"""
    __tablename__ = "subscriptions"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    plan: Mapped[str] = mapped_column(String(20))           # trial / monthly / yearly / admin
    source: Mapped[str] = mapped_column(String(20))         # trial / paid / admin
    status: Mapped[str] = mapped_column(String(20), default="active")  # active / revoked
    auto_renew: Mapped[bool] = mapped_column(Boolean, default=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    ends_at: Mapped[datetime] = mapped_column(DateTime)
    payment_id: Mapped[int | None] = mapped_column(ForeignKey("payments.id"))
    note: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Payment(Base):
    """付款紀錄。目前 provider 都是 mock（模擬），之後接綠界 / 藍新 / LINE Pay 時沿用同一張表。"""
    __tablename__ = "payments"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    trade_no: Mapped[str] = mapped_column(String(40), unique=True)
    plan: Mapped[str] = mapped_column(String(20))
    amount: Mapped[int] = mapped_column(Integer)
    method: Mapped[str | None] = mapped_column(String(20))
    provider: Mapped[str] = mapped_column(String(20), default="mock")
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending / paid / failed / cancelled
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime)


class InjuryRecord(Base):
    """防護紀錄表：一位選手一筆。"""
    __tablename__ = "injury_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    trainer_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    case_id: Mapped[int | None] = mapped_column(ForeignKey("cases.id"), index=True)  # 平台上的案件
    event_name: Mapped[str] = mapped_column(String(120))     # 案件名稱，或平台外賽事的名稱
    venue: Mapped[str | None] = mapped_column(String(60))    # 場地 / 時段，例如「場地 A」
    # STEP 1
    team: Mapped[str] = mapped_column(String(60), default="")
    athlete: Mapped[str] = mapped_column(String(60))         # 姓名或背號
    category: Mapped[str] = mapped_column(String(60), default="")
    # STEP 2
    body_parts: Mapped[list] = mapped_column(JSON, default=list)   # [{"part": "踝關節", "side": "右"}]
    injury_types: Mapped[list] = mapped_column(JSON, default=list)
    injury_other: Mapped[str | None] = mapped_column(String(100))
    # STEP 3
    treatments: Mapped[list] = mapped_column(JSON, default=list)
    consumables: Mapped[dict] = mapped_column(JSON, default=dict)  # {"kinesio": 1, ...}
    note: Mapped[str] = mapped_column(Text, default="")
    notified_requester: Mapped[bool] = mapped_column(Boolean, default=False)
    fill_seconds: Mapped[int | None] = mapped_column(Integer)      # 填寫花費秒數（目標 30–45 秒）
    source: Mapped[str] = mapped_column(String(10), default="web")  # liff / web
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)

    case: Mapped["Case | None"] = relationship()


class Notification(Base):
    """每一則 LINE 推播（含模擬發送）的紀錄，用來驗收配對精準度。"""
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int | None] = mapped_column(ForeignKey("cases.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(30))  # case_invite / hired_trainer / hired_requester / review_result
    channel: Mapped[str] = mapped_column(String(20))  # line / simulated
    status: Mapped[str] = mapped_column(String(20))  # sent / failed / simulated
    summary: Mapped[str] = mapped_column(String(300), default="")
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    user: Mapped[User] = relationship()


class Rating(Base):
    __tablename__ = "ratings"
    __table_args__ = (UniqueConstraint("case_id", "trainer_id", name="uq_rating_case_trainer"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"))
    trainer_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    requester_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    score: Mapped[int] = mapped_column(Integer)
    comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Report(Base):
    """發案方檢舉（放鳥 / 遲到）。管理員查證後才計入紀錄。"""
    __tablename__ = "reports"
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"))
    trainer_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    reporter_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    reason: Mapped[str] = mapped_column(String(30))
    detail: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending / confirmed / dismissed
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    handled_at: Mapped[datetime | None] = mapped_column(DateTime)

    case: Mapped[Case] = relationship()
    trainer_user: Mapped[User] = relationship(foreign_keys=[trainer_id])


class Feedback(Base):
    __tablename__ = "feedback"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    role: Mapped[str | None] = mapped_column(String(20))
    score: Mapped[int | None] = mapped_column(Integer)
    wishes: Mapped[list] = mapped_column(JSON, default=list)
    message: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str | None] = mapped_column(String(60))  # 從哪裡知道平台
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class ActivityLog(Base):
    """行為紀錄：登入、註冊、發案、應徵、錄用等，用於後台漏斗分析。"""
    __tablename__ = "activity_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    action: Mapped[str] = mapped_column(String(40), index=True)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)


# ---------------------------------------------------------------------------

def make_engine(url: str):
    kwargs = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if url in ("sqlite://", "sqlite:///:memory:"):
            # 記憶體資料庫（測試用）要共用同一條連線
            from sqlalchemy.pool import StaticPool
            kwargs["poolclass"] = StaticPool
        else:
            path = url[len("sqlite:///"):]
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    engine = create_engine(url, **kwargs)
    Base.metadata.create_all(engine)
    _add_missing_columns(engine)
    _backfill_sessions(engine)
    return engine


def _backfill_sessions(engine):
    """舊版案件沒有時段明細 → 依日期區間補上時段，舊的應徵也補上「全部時段可出席」。
    只處理還沒有時段的案件，重複執行不會重複新增。"""
    from datetime import datetime as _dt

    with sessionmaker(bind=engine)() as s:
        ids_with = select(CaseSession.case_id)
        legacy = s.scalars(select(Case).where(Case.id.not_in(ids_with))).all()
        for c in legacy:
            for d, t1, t2 in c.schedule:
                try:
                    a = _dt.strptime(t1, "%H:%M").time()
                    b = _dt.strptime(t2, "%H:%M").time()
                except ValueError:
                    a, b = time(8), time(17)
                c.sessions.append(CaseSession(date=d, start_time=a, end_time=b))
            s.flush()
            for app in c.applications:
                if not app.slots:
                    for x in c.sessions:
                        app.slots.append(ApplicationSession(
                            session_id=x.id, status="hired" if app.status == "hired" else "available",
                            hired_at=app.hired_at if app.status == "hired" else None))
        s.commit()


def _add_missing_columns(engine):
    """輕量自動遷移：新版本加了欄位時，自動 ALTER TABLE 補上（只補可為空的欄位）。
    讓已上線的資料庫升級程式後不必重建。刪欄位、改型別仍需手動處理。"""
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            existing = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in existing:
                    continue
                coltype = col.type.compile(dialect=engine.dialect)
                if isinstance(col.type, Boolean):
                    conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN {col.name} {coltype} DEFAULT FALSE'))
                else:
                    conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN {col.name} {coltype}'))


_SessionFactory: sessionmaker | None = None


def use(engine):
    """把 session 綁到指定 engine。Streamlit 每次 rerun 都呼叫一次（很便宜），
    搭配 st.cache_resource 快取 engine，可避免開發時模組重新載入後失去連線設定。"""
    global _SessionFactory
    if _SessionFactory is None or _SessionFactory.kw.get("bind") is not engine:
        _SessionFactory = sessionmaker(bind=engine, expire_on_commit=False)


def init(url: str):
    engine = make_engine(url)
    use(engine)
    return engine


@contextmanager
def session():
    if _SessionFactory is None:
        raise RuntimeError("資料庫尚未初始化，請先呼叫 db.init()")
    s = _SessionFactory()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def log(s, actor_id: int | None, action: str, **meta):
    """actor_id = 做這個動作的人（管理員動作為 None）。meta 可以自由放 user_id 等欄位。"""
    s.add(ActivityLog(user_id=actor_id, action=action, meta=meta))
