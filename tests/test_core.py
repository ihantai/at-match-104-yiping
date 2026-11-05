"""驗收測試（對應開發書第 6 點）。執行：pytest -q"""
from datetime import date, time, timedelta

import pytest

from core import db, seed, services
from core.db import Application, Case, Notification, TrainerProfile, User
from core.matching import find_matches, rank_applicants
from core.privacy import mask_name, public_trainer_view, scrub_contact


@pytest.fixture()
def s():
    db.init("sqlite://")
    with db.session() as sess:
        seed.seed(sess)
        sess.flush()
        yield sess


def _user(s, lid):
    return s.query(User).filter_by(line_user_id=lid).one()


def _case_kwargs(**over):
    d = date.today() + timedelta(days=7)
    kw = dict(title="中部盃柔道錦標賽", sport_type="籃球", sessions=[(d, time(8), time(17))],
              location_county="台中市", location_detail="台中某體育館",
              salary_type="日薪", salary_amount=2800, headcount=1, description="",
              license_req="at", require_emt=False, exp_mode="none", exp_years=0,
              required_school=seed.NTSU)
    kw.update(over)
    return kw


def test_acceptance_precision_ntsu_at_taichung(s):
    """限台體大 + AT + 台中 → 只通知陳冠宇、林雅婷。"""
    req = _user(s, "demo_r01")
    c, n = services.publish_case(s, req, **_case_kwargs())
    s.flush()
    notified = {x.user.display_name for x in s.query(Notification).filter_by(case_id=c.id, kind="case_invite")}
    assert notified == {"陳冠宇", "林雅婷"}
    assert n == 2


def test_pending_rejected_blocked_never_notified(s):
    req = _user(s, "demo_r01")
    c, _ = services.publish_case(s, req, **_case_kwargs(required_school=None, license_req="none"))
    names = {p.name for _, p in find_matches(s, c)}
    assert not names & {"王小明", "謝宛臻", "劉怡君"}
    assert {"陳冠宇", "林雅婷", "張家豪", "黃詩涵"} <= names


def test_experience_filters(s):
    req = _user(s, "demo_r01")
    c, _ = services.publish_case(s, req, **_case_kwargs(exp_mode="min", exp_years=5))
    assert {p.name for _, p in find_matches(s, c)} == {"林雅婷"}
    c2, _ = services.publish_case(s, req, **_case_kwargs(exp_mode="max", exp_years=3))
    assert {p.name for _, p in find_matches(s, c2)} == {"陳冠宇"}


def test_high_risk_and_emt(s):
    req = _user(s, "demo_r01")
    c, _ = services.publish_case(s, req, **_case_kwargs(sport_type="柔道", license_req="none",
                                                          required_school=None, require_emt=True))
    assert c.is_high_risk
    assert {p.name for _, p in find_matches(s, c)} == {"林雅婷", "黃詩涵"}


def test_ranking_no_show_goes_last(s):
    c = s.query(Case).filter_by(title="河濱半程馬拉松（測試）").one()
    ranked = [r["profile"].name for r in rank_applicants(s, c)]
    # 三人都 100% 吻合；許志明有放鳥紀錄 → 最後；蔡佩珊有 5 星 → 排在無評價的李宗翰前
    assert ranked == ["蔡佩珊", "李宗翰", "許志明"]


def test_privacy_before_hire(s):
    prof = s.get(TrainerProfile, _user(s, "demo_t01").id)
    prof.experience = "請打 0912-345-678 或 email a@b.com，LINE ID: hello123"
    view = public_trainer_view(prof, 3)
    blob = str(view)
    assert prof.phone not in blob and prof.email not in blob and prof.name not in blob
    assert "0912-345-678" not in blob and "a@b.com" not in blob and "hello123" not in blob
    assert view["masked_name"] == "陳O宇"


def test_hire_unlocks_closes_and_notifies(s):
    c = s.query(Case).filter_by(title="河濱半程馬拉松（測試）").one()
    req = s.get(User, c.requester_id)
    apps = sorted(c.applications, key=lambda a: a.id)
    assert services.hire(s, c, apps[0], req) is False  # 需 2 人，還沒滿
    assert services.hire(s, c, apps[1], req) is True   # 滿了 → 自動結案
    assert c.status == "closed"
    assert apps[2].status == "not_selected"
    kinds = [n.kind for n in s.query(Notification).filter_by(case_id=c.id)]
    assert kinds.count("hired_trainer") == 2 and kinds.count("hired_requester") == 2
    with pytest.raises(services.ActionError, match="額滿"):
        services.apply(s, c, _user(s, "demo_t12"))


def test_mask_name():
    assert mask_name("王小明") == "王O明"
    assert mask_name("王明") == "王O"
    assert mask_name("歐陽小明") == "歐OO明"
    assert mask_name("John Smith") == "J***"
    assert "［電話已隱藏］" in scrub_contact("手機 0912345678")


def test_purge_demo_keeps_real_users(s):
    from views.admin import purge_demo
    real = User(line_user_id="U_real_123", display_name="真實用戶", role="trainer")
    s.add(real)
    s.flush()
    n = purge_demo(s)
    assert n == len(seed.TRAINERS) + len(seed.REQUESTERS)
    assert s.query(User).count() == 1
    assert s.query(Case).count() == 0 and s.query(Notification).count() == 0


# --- 第二版修改 -------------------------------------------------------------

def test_weekly_sessions_user_example():
    """1. 每週固定：「每個星期六日，連續兩週」→ 4 個時段。"""
    from core.validators import weekly_sessions
    sat = date(2026, 11, 14)  # 週六
    out = weekly_sessions(sat, 2, [5, 6], time(8), time(17))
    assert [d for d, _, _ in out] == [date(2026, 11, 14), date(2026, 11, 15), date(2026, 11, 21), date(2026, 11, 22)]
    # 從週三開始也一樣抓得到該週與下週的六日
    assert len(weekly_sessions(date(2026, 11, 11), 2, [5, 6], time(8), time(17))) == 4


def test_session_validation():
    from core.validators import sessions_errors
    d = date.today() + timedelta(days=3)
    assert sessions_errors([]) == ["請至少設定一個時段"]
    assert any("結束時間" in e for e in sessions_errors([(d, time(17), time(8))]))
    assert any("重疊" in e for e in sessions_errors([(d, time(8), time(12)), (d, time(11), time(15))]))
    assert sessions_errors([(d, time(8), time(12)), (d, time(13), time(17))]) == []  # 同一天上下午分開 OK
    assert any("過去" in e for e in sessions_errors([(date.today() - timedelta(days=1), time(8), time(9))]))


def test_multi_session_case_and_summary(s):
    from core import fmt
    from core.matching import is_event_past
    req = _user(s, "demo_r01")
    sat = date.today() + timedelta(days=(5 - date.today().weekday()) % 7 + 7)
    sess = [(sat, time(9), time(17)), (sat + timedelta(days=1), time(9), time(12)),
            (sat + timedelta(days=7), time(9), time(17)), (sat + timedelta(days=8), time(9), time(12))]
    c, _ = services.publish_case(s, req, **_case_kwargs(sessions=list(reversed(sess))))
    assert [x.date for x in c.sessions] == [d for d, _, _ in sess]  # 自動排序
    assert c.event_date == sat and c.end_date == sat + timedelta(days=8) and c.time_slot == "多時段"
    summ = fmt.schedule_summary(c)
    assert "共 4 個時段" in summ and "週六、日" in summ and "各時段時間不同" in summ
    assert not is_event_past(c)
    # 連續多天、同時段
    d = date.today() + timedelta(days=5)
    c2, _ = services.publish_case(s, req, **_case_kwargs(
        sessions=[(d + timedelta(days=i), time(8), time(17)) for i in range(3)]))
    assert "連續 3 天，每日 08:00-17:00" in fmt.schedule_summary(c2)
    # 單一時段
    c3, _ = services.publish_case(s, req, **_case_kwargs())
    assert fmt.schedule_summary(c3).endswith("08:00-17:00") and "共" not in fmt.schedule_summary(c3)
    # LINE 卡片列出各時段
    from core import line_api
    assert sum(fmt.session_line(x, a.strftime("%H:%M"), b.strftime("%H:%M")) in str(line_api.case_flex(c))
               for x, a, b in sess) == 4
    with pytest.raises(services.ActionError):
        services.publish_case(s, req, **_case_kwargs(sessions=[]))


def test_legacy_case_without_sessions_still_displays():
    """舊版只有日期區間 + 時段字串的案件，仍可正常顯示。"""
    from core import fmt
    c = Case(event_date=date(2026, 11, 14), event_end_date=date(2026, 11, 15), time_slot="08:00-17:00")
    assert len(c.schedule) == 2
    assert "連續 2 天" in fmt.schedule_summary(c)


def test_license_numbers_drive_matching(s):
    """2. 所有證照都有證號：AT、PT、EMT-1、EMT-2、CPR+AED。"""
    p = s.get(TrainerProfile, _user(s, "demo_t02").id)
    assert p.at_license_no and p.emt1_license_no and "EMT-1" in p.first_aid_licenses
    p3 = s.get(TrainerProfile, _user(s, "demo_t03").id)
    assert p3.at_license_no is None and not p3.has_at_license
    assert p3.cpr_license_no and "CPR+AED" in p3.first_aid_licenses
    p8 = s.get(TrainerProfile, _user(s, "demo_t08").id)
    assert p8.emt2_license_no and "EMT-2" in p8.first_aid_licenses
    # 每個種子防護員具備的證照都有證號
    from core.constants import LICENSE_FIELDS
    for p in s.query(TrainerProfile):
        held = {"AT"} if p.has_at_license else set()
        held |= {"PT"} if p.has_pt_license else set()
        held |= set(p.first_aid_licenses or [])
        for field, code, _l, _h in LICENSE_FIELDS:
            assert bool(getattr(p, field)) == (code in held), (p.name, code)
    # 證號永遠不會出現在發案方看到的資料裡
    assert p.at_license_no not in str(public_trainer_view(p, 6))


def test_questions_flow(s):
    """3. 我想發問：提問、通知發案方、回覆、公開/私下、遮蔽聯絡方式、上限、結案後不能問。"""
    req = _user(s, "demo_r01")
    t1, t2 = _user(s, "demo_t01"), _user(s, "demo_t02")
    c, _ = services.publish_case(s, req, **_case_kwargs())
    q = services.ask_question(s, c, t1, "可以先打給我嗎 0912-345-678")
    assert "0912-345-678" not in q.question
    assert s.query(Notification).filter_by(case_id=c.id, kind="question", user_id=req.id).count() == 1
    # 還沒回覆：只有發問者看得到
    assert q in services.visible_questions(s, c, t1.id)
    assert q not in services.visible_questions(s, c, t2.id)
    services.answer_question(s, q, req, "有提供冰塊", public=True)
    assert q in services.visible_questions(s, c, t2.id)
    assert s.query(Notification).filter_by(case_id=c.id, kind="answer", user_id=t1.id).count() == 1
    q2 = services.ask_question(s, c, t2, "私下問")
    services.answer_question(s, q2, req, "好", public=False)
    assert q2 not in services.visible_questions(s, c, t1.id)
    with pytest.raises(services.ActionError):
        services.answer_question(s, q2, _user(s, "demo_r02"), "不是我的案件")
    for _ in range(services.MAX_PENDING_QUESTIONS):
        services.ask_question(s, c, t1, "問題")
    with pytest.raises(services.ActionError, match="等待回覆"):
        services.ask_question(s, c, t1, "再一題")
    c.status = "closed"
    with pytest.raises(services.ActionError, match="結束招募"):
        services.ask_question(s, c, t2, "還能問嗎")


def test_county_select_all():
    """4. 接案地區「全選」。"""
    from core.constants import COUNTIES, COUNTY_ALL
    from core.validators import collapse_counties, expand_counties
    assert expand_counties([COUNTY_ALL]) == COUNTIES
    assert expand_counties([COUNTY_ALL, "台北市"]) == COUNTIES
    assert expand_counties(["台南市", "台北市"]) == ["台北市", "台南市"]  # 依官方順序
    assert collapse_counties(COUNTIES) == [COUNTY_ALL]
    assert collapse_counties(["台北市"]) == ["台北市"]


def test_line_id_required():
    """5. LINE ID 必填與格式。"""
    from core.validators import line_id_error
    assert line_id_error("") is not None
    assert line_id_error("ab") is not None
    assert line_id_error("my id") is not None
    assert line_id_error("trainer_01") is None
    assert line_id_error("@river.run") is None


def test_auto_migration_adds_new_columns(tmp_path):
    """舊版資料庫升級後自動補上新欄位，不用重建。"""
    import sqlite3
    from sqlalchemy import inspect
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE cases (id INTEGER PRIMARY KEY, title VARCHAR(120))")
    con.commit()
    con.close()
    engine = db.make_engine(f"sqlite:///{path}")
    cols = {c["name"] for c in inspect(engine).get_columns("cases")}
    assert {"event_end_date", "is_high_risk", "status"} <= cols
    assert inspect(engine).has_table("case_sessions")


# --- 第四版：依時段應徵與錄用 -----------------------------------------------------

@pytest.fixture()
def weekly(s):
    """週六日連續兩週、每時段 1 人。回傳 (case, requester, {名稱: user}, [sat1, sun1, sat2, sun2])。"""
    from core.validators import weekly_sessions
    req = _user(s, "demo_r01")
    sat = date.today() + timedelta(days=(5 - date.today().weekday()) % 7 + 7)
    c, _ = services.publish_case(s, req, **_case_kwargs(
        title="週末聯賽", license_req="none", required_school=None,
        sessions=weekly_sessions(sat, 2, [5, 6], time(9), time(17))))
    ids = [x.id for x in c.sessions]
    who = {k: _user(s, lid) for k, lid in (("陳", "demo_t01"), ("林", "demo_t02"), ("黃", "demo_t04"))}
    return c, req, who, ids


def _app(s, c, u):
    return s.query(Application).filter_by(case_id=c.id, trainer_id=u.id).one()


def test_apply_with_selected_sessions(s, weekly):
    c, req, who, (sat1, sun1, sat2, sun2) = weekly
    a = services.apply(s, c, who["陳"], [sat1, sat2])
    assert a.available_ids == {sat1, sat2}
    assert services.apply(s, c, who["林"]).available_ids == {sat1, sun1, sat2, sun2}  # 不給 = 全部
    with pytest.raises(services.ActionError, match="至少勾選"):
        services.apply(s, c, who["黃"], [])
    other, _ = services.publish_case(s, req, **_case_kwargs())
    with pytest.raises(services.ActionError, match="不屬於"):
        services.apply(s, c, who["黃"], [other.sessions[0].id])


def test_hire_by_session_until_all_full(s, weekly):
    c, req, who, (sat1, sun1, sat2, sun2) = weekly
    services.apply(s, c, who["陳"], [sat1, sat2])
    services.apply(s, c, who["林"])
    services.apply(s, c, who["黃"], [sat2, sun2])
    assert services.hire(s, c, _app(s, c, who["陳"]), req, [sat1, sat2]) is False
    assert services.session_fill(s, c) == {sat1: 1, sun1: 0, sat2: 1, sun2: 0}
    assert _app(s, c, who["黃"]).status == "applied"  # 週日第二週還有機會
    with pytest.raises(services.ActionError, match="錄滿"):
        services.hire(s, c, _app(s, c, who["林"]), req, [sat1])
    with pytest.raises(services.ActionError, match="勾選可以出席"):
        services.hire(s, c, _app(s, c, who["陳"]), req, [sun1])
    assert services.hire(s, c, _app(s, c, who["黃"]), req, [sun2]) is False
    # 林：不指定時段 = 他可出席且還沒滿的（只剩 sun1）→ 全部錄滿、自動結案
    assert services.hire(s, c, _app(s, c, who["林"]), req) is True
    assert c.status == "closed"
    assert _app(s, c, who["林"]).hired_ids == {sun1}
    # 錄用通知列出時段
    msgs = [n.summary for n in s.query(Notification).filter_by(case_id=c.id, kind="hired_trainer")]
    assert len(msgs) == 3


def test_not_selected_when_their_sessions_fill_up(s, weekly):
    c, req, who, (sat1, sun1, sat2, sun2) = weekly
    services.apply(s, c, who["黃"], [sat2, sun2])
    services.apply(s, c, who["林"])
    services.hire(s, c, _app(s, c, who["林"]), req, [sat2, sun2])
    assert c.status == "open"  # 第一週還沒人
    assert _app(s, c, who["黃"]).status == "not_selected"  # 他能來的時段都被補滿了
    with pytest.raises(services.ActionError):
        services.hire(s, c, _app(s, c, who["黃"]), req, [sat2])


def test_add_more_sessions_to_hired_trainer(s, weekly):
    c, req, who, (sat1, sun1, sat2, sun2) = weekly
    services.apply(s, c, who["林"])
    services.hire(s, c, _app(s, c, who["林"]), req, [sat1])
    services.hire(s, c, _app(s, c, who["林"]), req, [sun1])  # 加錄
    assert _app(s, c, who["林"]).hired_ids == {sat1, sun1}
    kinds = [n for n in s.query(Notification).filter_by(case_id=c.id, kind="hired_trainer")]
    assert len(kinds) == 2


def test_update_availability(s, weekly):
    c, req, who, (sat1, sun1, sat2, sun2) = weekly
    services.apply(s, c, who["陳"], [sat1])
    services.update_availability(s, _app(s, c, who["陳"]), who["陳"], [sat1, sun2])
    assert _app(s, c, who["陳"]).available_ids == {sat1, sun2}
    services.hire(s, c, _app(s, c, who["陳"]), req, [sat1])
    with pytest.raises(services.ActionError, match="已錄用的時段不能取消"):
        services.update_availability(s, _app(s, c, who["陳"]), who["陳"], [sun2])
    services.apply(s, c, who["林"], [sun1])
    services.hire(s, c, _app(s, c, who["林"]), req, [sun1])
    with pytest.raises(services.ActionError, match="錄滿"):
        services.update_availability(s, _app(s, c, who["陳"]), who["陳"], [sat1, sun2, sun1])


def test_withdraw_and_reapply_resets_sessions(s, weekly):
    c, req, who, (sat1, sun1, sat2, sun2) = weekly
    a = services.apply(s, c, who["陳"], [sat1, sun1])
    services.withdraw(s, a, who["陳"])
    a2 = services.apply(s, c, who["陳"], [sat2])
    assert a2.id == a.id and a2.available_ids == {sat2} and a2.status == "applied"


def test_backfill_legacy_cases_and_applications(tmp_path):
    """舊版案件（沒有時段）升級後自動補上時段；已錄用的應徵也補上「已錄用」時段。"""
    path = tmp_path / "legacy.db"
    eng = db.make_engine(f"sqlite:///{path}")
    from sqlalchemy.orm import Session
    with Session(eng) as x:
        u1, u2 = User(line_user_id="a", display_name="a"), User(line_user_id="b", display_name="b")
        x.add_all([u1, u2])
        x.flush()
        c = Case(requester_id=u1.id, title="舊案", sport_type="籃球", event_date=date(2026, 12, 5),
                 event_end_date=date(2026, 12, 6), time_slot="08:00-17:00", location_county="台北市",
                 location_detail="x", salary_type="日薪", salary_amount=1)
        x.add(c)
        x.flush()
        x.add(Application(case_id=c.id, trainer_id=u2.id, status="hired"))
        x.commit()
    db.init(f"sqlite:///{path}")
    with db.session() as x:
        c = x.query(Case).one()
        assert [str(v.date) for v in c.sessions] == ["2026-12-05", "2026-12-06"]
        assert len(c.applications[0].hired_ids) == 2


def test_database_url_password_with_special_chars(monkeypatch):
    """密碼含 @ / # ? : % 時自動網址編碼，主機名稱能正確解析。"""
    from sqlalchemy.engine import make_url
    from core import config
    host = "aws-0-ap-northeast-1.pooler.supabase.com"
    for pw in ["abc@1234", "a@b@c", "p/w#x?y:z", "100%sure", "plainPass123"]:
        monkeypatch.setenv("DATABASE_URL", f"postgresql://postgres.myref:{pw}@{host}:5432/postgres")
        u = make_url(config.database_url())
        assert u.host == host and u.password == pw and u.username == "postgres.myref", pw
        assert u.drivername == "postgresql+psycopg2"
    # 已經編碼過的不會被重複編碼
    monkeypatch.setenv("DATABASE_URL", f"postgres://postgres.myref:abc%401234@{host}:5432/postgres")
    assert make_url(config.database_url()).password == "abc@1234"
    # SQLite 不受影響
    monkeypatch.setenv("DATABASE_URL", "sqlite:///data/app.db")
    assert config.database_url() == "sqlite:///data/app.db"


def test_admin_block_and_unblock(s):
    """管理員封鎖 / 解除封鎖（之前這裡會因為紀錄參數衝突而出錯）。"""
    from core.db import ActivityLog
    u = _user(s, "demo_t03")
    services.set_blocked(s, u, True, "測試")
    assert u.is_blocked and u.blocked_reason == "測試"
    services.set_blocked(s, u, False)
    assert not u.is_blocked
    s.flush()
    assert s.query(ActivityLog).filter_by(action="block").one().meta["user_id"] == u.id
