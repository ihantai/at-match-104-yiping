"""種子資料：給測試模式與驗收測試使用。

驗收情境（開發書第 6 點）：發布「限台體大畢業 + 有 AT 證 + 台中」案件時，
只有 陳冠宇、林雅婷 兩位應收到通知；其餘都是刻意設計的干擾組。
"""
from __future__ import annotations

from datetime import date, time, timedelta

from . import services
from .db import Application, Case, CaseSession, Rating, Report, RequesterProfile, TrainerProfile, User, now
from .matching import current_year

NTSU = "國立臺灣體育運動大學"

# (line_id, 姓名, 性別, 學校, 畢業幾年, 縣市, AT, PT, 急救, 審核狀態, 封鎖, 備註)
TRAINERS = [
    ("demo_t01", "陳冠宇", "男", NTSU, 3, ["台中市", "彰化縣"], True, False, ["CPR+AED"], "approved", False, "✅ 驗收應命中"),
    ("demo_t02", "林雅婷", "女", NTSU, 6, ["台中市", "南投縣"], True, True, ["EMT-1"], "approved", False, "✅ 驗收應命中"),
    ("demo_t03", "張家豪", "男", NTSU, 4, ["台中市"], False, True, ["CPR+AED"], "approved", False, "✗ 沒有 AT"),
    ("demo_t04", "黃詩涵", "女", "國立體育大學", 5, ["台中市", "苗栗縣"], True, False, ["EMT-1"], "approved", False, "✗ 學校不同"),
    ("demo_t05", "吳承翰", "男", NTSU, 2, ["台北市", "新北市"], True, False, ["CPR+AED"], "approved", False, "✗ 不接台中"),
    ("demo_t06", "王小明", "男", NTSU, 1, ["台中市"], True, False, ["CPR+AED"], "pending", False, "✗ 尚未審核"),
    ("demo_t07", "劉怡君", "女", NTSU, 7, ["台中市"], True, False, ["EMT-2"], "approved", True, "✗ 黑名單"),
    ("demo_t08", "李宗翰", "男", "長庚大學", 8, ["台北市", "桃園市"], False, True, ["EMT-2"], "approved", False, ""),
    ("demo_t09", "蔡佩珊", "女", "國立臺灣師範大學", 1, ["台北市", "新北市"], True, False, ["CPR+AED"], "approved", False, ""),
    ("demo_t10", "許志明", "男", "輔仁大學", 0, ["台北市", "新北市"], False, False, ["CPR+AED"], "approved", False, ""),
    ("demo_t11", "鄭雅文", "女", "高雄醫學大學", 4, ["高雄市", "台南市", "屏東縣"], True, False, ["EMT-1"], "approved", False, ""),
    ("demo_t12", "周柏諺", "男", "國立體育大學", 10, ["桃園市", "新竹市", "新竹縣", "台北市"], True, True, ["EMT-2"], "approved", False, ""),
    ("demo_t13", "謝宛臻", "女", NTSU, 2, ["台中市"], True, False, [], "rejected", False, "✗ 審核退件"),
]

REQUESTERS = [
    ("demo_r01", "台中市某體育會（測試）", "單項運動協會", "游志強", "0912-000-101", "req01@example.com", "tc_sports"),
    ("demo_r02", "河濱路跑協會（測試）", "賽事主辦單位", "簡美玲", "0912-000-102", "req02@example.com", "river_run"),
]


def has_demo_data(s) -> bool:
    return s.query(User).filter(User.line_user_id.like("demo_%")).count() > 0


def seed(s) -> str:
    if has_demo_data(s):
        return "已經有測試資料，略過。"
    cy = current_year()
    users = {}
    for i, (lid, name, gender, school, yrs, counties, at, pt, fa, review, blocked, _note) in enumerate(TRAINERS, 1):
        u = User(line_user_id=lid, display_name=name, role="trainer", is_blocked=blocked,
                 blocked_reason="測試：放鳥紀錄" if blocked else None)
        s.add(u)
        s.flush()
        s.add(TrainerProfile(
            user_id=u.id, name=name, gender=gender, phone=f"0911-000-{i:03d}",
            email=f"trainer{i:02d}@example.com", line_contact_id=f"trainer_{i:02d}",
            school=school, graduation_year=cy - yrs, service_counties=counties,
            has_at_license=at, has_pt_license=pt, first_aid_licenses=fa,
            at_license_no=f"AT{cy - yrs}{i:04d}" if at else None,
            pt_license_no=f"PT{cy - yrs}{i:04d}" if pt else None,
            emt1_license_no=f"EMT1-{i:05d}" if "EMT-1" in fa else None,
            emt2_license_no=f"EMT2-{i:05d}" if "EMT-2" in fa else None,
            cpr_license_no=f"CPR{cy - 1}{i:04d}" if "CPR+AED" in fa else None,
            experience=f"曾支援 {cy - 1} 年多場地區賽事的場邊防護與傷害評估。",
            review_status=review, reviewed_at=now() if review != "pending" else None,
            review_note="AT 證號查無資料，請確認是否填錯" if review == "rejected" else None,
            consent_at=now(),
        ))
        users[lid] = u
    for lid, org, otype, contact, phone, email, line_id in REQUESTERS:
        u = User(line_user_id=lid, display_name=contact, role="requester")
        s.add(u)
        s.flush()
        s.add(RequesterProfile(user_id=u.id, org_name=org, org_type=otype, contact_name=contact,
                               phone=phone, email=email, line_contact_id=line_id, consent_at=now()))
        users[lid] = u
    s.flush()

    # 一筆已結束的歷史案件：蔡佩珊拿到好評，許志明被查證放鳥 → 用來展示排序權重
    past = Case(requester_id=users["demo_r02"].id, title="河濱親子路跑（測試・已結束）",
                sport_type="路跑 / 馬拉松", event_date=date.today() - timedelta(days=20),
                event_end_date=date.today() - timedelta(days=20),
                time_slot="06:00-11:00", location_county="台北市", location_detail="大佳河濱公園",
                sessions=[CaseSession(date=date.today() - timedelta(days=20), start_time=time(6), end_time=time(11))],
                salary_type="日薪", salary_amount=2500, headcount=2, status="closed", closed_at=now())
    s.add(past)
    s.flush()
    for lid in ("demo_t09", "demo_t10"):
        s.add(Application(case_id=past.id, trainer_id=users[lid].id, status="hired", hired_at=now()))
    s.add(Rating(case_id=past.id, trainer_id=users["demo_t09"].id, requester_id=users["demo_r02"].id,
                 score=5, comment="準時、處置專業"))
    s.add(Report(case_id=past.id, trainer_id=users["demo_t10"].id, reporter_id=users["demo_r02"].id,
                 reason="無故缺席（放鳥）", detail="當天未到場也未聯繫", status="confirmed", handled_at=now()))

    # 一筆招募中的兩天賽事，已有三人應徵、兩則提問 → 發案方可直接體驗挑選與回覆流程
    d14 = date.today() + timedelta(days=14)
    c, _ = services.publish_case(
        s, users["demo_r02"], title="河濱半程馬拉松（測試）", sport_type="路跑 / 馬拉松",
        sessions=[(d14, time(5, 30), time(12)), (d14 + timedelta(days=1), time(5, 30), time(12))],
        location_county="台北市", location_detail="台北市中正區某河濱公園",
        salary_type="日薪", salary_amount=3000, headcount=2,
        description="需駐點終點醫護站，負責抽筋、熱傷害初步處置。",
        license_req="first_aid", require_emt=False, exp_mode="none", exp_years=0, required_school=None,
    )
    for lid in ("demo_t10", "demo_t08", "demo_t09"):
        services.apply(s, c, users[lid])
    q = services.ask_question(s, c, users["demo_t08"], "終點醫護站有提供帳篷和冰塊嗎？需要自備 AED 嗎？")
    services.answer_question(s, q, users["demo_r02"], "帳篷、冰塊和 AED 主辦方都會準備，請自備個人防護包即可。",
                             public=True)
    services.ask_question(s, c, users["demo_t09"], "兩天都需要到場嗎？可以只接其中一天嗎？")

    # 每週固定的案件：從下週六起，連續兩週的週六、週日（週日只有上午）
    from .validators import weekly_sessions
    sat = date.today() + timedelta(days=(5 - date.today().weekday()) % 7 + 7)
    weekly = weekly_sessions(sat, 2, [5, 6], time(9), time(17))
    weekly = [(d, a, time(12) if d.weekday() == 6 else b) for d, a, b in weekly]
    wc, _ = services.publish_case(
        s, users["demo_r01"], title="青少年週末籃球聯賽（測試）", sport_type="籃球", sessions=weekly,
        location_county="台中市", location_detail="台中市某國中體育館", salary_type="時薪", salary_amount=300,
        headcount=1, description="聯賽期間駐點場邊，週日只有上午場。",
        license_req="at_or_pt", require_emt=False, exp_mode="none", exp_years=0, required_school=None,
    )
    # 陳冠宇只能週六、林雅婷全部都可以、黃詩涵只能第二週 → 發案方可體驗依時段錄用
    sats = [x.id for x in wc.sessions if x.date.weekday() == 5]
    week2 = [x.id for x in wc.sessions if x.date >= sat + timedelta(days=7)]
    services.apply(s, wc, users["demo_t01"], sats)
    services.apply(s, wc, users["demo_t02"])
    services.apply(s, wc, users["demo_t04"], week2)
    # 防護紀錄表（付費功能）：蔡佩珊已付費（模擬），在已結束的路跑賽留了 3 筆紀錄；其他人未開通
    from . import billing, records
    t09 = users["demo_t09"]
    pay = billing.create_checkout(s, t09, "monthly", "LINE Pay").payment
    billing.settle(s, pay, "paid")
    s.get(TrainerProfile, t09.id).record_terms_at = now()
    samples = [
        dict(team="河濱跑團", athlete="#1024", category="路跑 / 馬拉松 - 公開組",
             body_parts=[{"part": "膝關節", "side": "右"}], injury_types=["拉傷"], treatments=["冰敷", "伸展拉筋"],
             consumables={"ice": 1}, note="", fill_seconds=34),
        dict(team="", athlete="#2088", category="路跑 / 馬拉松 - 公開組", body_parts=[],
             injury_types=["熱衰竭"], treatments=["冰敷", "建議轉診"], consumables={"ice": 2},
             note="意識清楚，已由救護車送醫", fill_seconds=41),
        dict(team="親子組", athlete="#3301", category="路跑 / 馬拉松 - 青少年組",
             body_parts=[{"part": "踝關節", "side": "左"}], injury_types=["扭傷"],
             treatments=["冰敷", "貼紮 / 包紮"], consumables={"white": 1, "kinesio": 1, "ice": 1},
             note="建議賽後至醫院照 X 光", fill_seconds=38),
    ]
    for d in samples:
        records.create(s, t09, dict(case_id=past.id, venue="終點醫護站", **d))
    return f"已建立 {len(TRAINERS)} 位防護員、{len(REQUESTERS)} 個發案方、3 筆案件、2 則提問與 3 筆防護紀錄。"
