"""防護紀錄表（付費功能）、模擬金流、LIFF 登入。"""
from datetime import timedelta
from unittest import mock

import pytest

from core import billing, db, records, seed
from core.db import Case, InjuryRecord, Notification, Subscription, User, now


@pytest.fixture()
def s():
    db.init("sqlite://")
    with db.session() as sess:
        seed.seed(sess)
        sess.flush()
        yield sess


def _u(s, lid):
    return s.query(User).filter_by(line_user_id=lid).one()


def _rec(**over):
    d = dict(athlete="#12 陳大為", team="狂風籃球隊", category="籃球 - 男子組",
             body_parts=[{"part": "踝關節", "side": "右"}], injury_types=["扭傷"],
             treatments=["冰敷", "貼紮 / 包紮"], consumables={"kinesio": 1, "white": 2, "elastic": 0, "ice": 1},
             note="建議賽後至醫院拍照X光", event_name="平台外的比賽", fill_seconds=36)
    d.update(over)
    return d


# --- 付費開通 ------------------------------------------------------------------

def test_paywall_blocks_without_subscription(s):
    t = _u(s, "demo_t01")
    assert not billing.has_access(s, t.id)
    with pytest.raises(records.RecordError, match="付費"):
        records.create(s, t, _rec())


def test_trial_once(s):
    t = _u(s, "demo_t01")
    sub = billing.start_trial(s, t)
    assert billing.has_access(s, t.id) and (sub.ends_at - sub.starts_at).days == 7
    with pytest.raises(billing.BillingError):
        billing.start_trial(s, t)


def test_mock_checkout_paid_failed_cancelled(s):
    t = _u(s, "demo_t01")
    pay = billing.create_checkout(s, t, "monthly", "信用卡").payment
    assert pay.status == "pending" and pay.amount == 199 and pay.provider == "mock"
    assert not billing.has_access(s, t.id)  # 還沒付款
    billing.provider().simulate(s, pay, "failed")
    assert pay.status == "failed" and not billing.has_access(s, t.id)
    p2 = billing.create_checkout(s, t, "monthly", "信用卡").payment
    billing.provider().simulate(s, p2, "cancelled")
    assert not billing.has_access(s, t.id)
    p3 = billing.create_checkout(s, t, "yearly", "LINE Pay").payment
    billing.provider().simulate(s, p3, "paid")
    sub = billing.active_subscription(s, t.id)
    assert sub.source == "paid" and sub.plan == "yearly" and sub.auto_renew
    # 同一筆付款通知重複送達，不會重複延長
    billing.settle(s, p3, "paid")
    assert s.query(Subscription).filter_by(payment_id=p3.id).count() == 1


def test_renewal_extends_from_current_end(s):
    t = _u(s, "demo_t01")
    billing.start_trial(s, t)
    first_end = billing.active_subscription(s, t.id).ends_at
    p = billing.create_checkout(s, t, "monthly", "ATM 轉帳").payment
    billing.settle(s, p, "paid")
    new = s.query(Subscription).filter_by(payment_id=p.id).one()
    assert new.starts_at == first_end and new.ends_at == first_end + timedelta(days=30)


def test_expired_subscription_loses_access(s):
    t = _u(s, "demo_t01")
    sub = billing.admin_grant(s, t.id, 1)
    sub.ends_at = now() - timedelta(minutes=1)
    assert not billing.has_access(s, t.id)


def test_admin_revoke_and_cancel_auto_renew(s):
    t = _u(s, "demo_t09")  # 種子資料已付費
    billing.cancel_auto_renew(s, t)
    assert billing.has_access(s, t.id) and not billing.active_subscription(s, t.id).auto_renew
    billing.admin_revoke(s, t.id)
    assert not billing.has_access(s, t.id)


def test_unknown_billing_mode_is_rejected(monkeypatch):
    monkeypatch.setenv("BILLING_MODE", "ecpay")
    with pytest.raises(billing.BillingError):
        billing.provider()


# --- 防護紀錄表 ----------------------------------------------------------------

def test_create_record_outside_platform(s):
    t = _u(s, "demo_t01")
    billing.start_trial(s, t)
    r = records.create(s, t, _rec())
    assert r.case_id is None and r.consumables == {"kinesio": 1, "white": 2, "ice": 1}  # 0 不存
    assert records.parts_text(r) == "右踝關節"
    assert not r.notified_requester


def test_record_for_hired_case_notifies_organizer(s):
    t = _u(s, "demo_t09")
    past = s.query(Case).filter_by(title="河濱親子路跑（測試・已結束）").one()
    r = records.create(s, t, _rec(case_id=past.id, treatments=["冰敷", "傷退"]))
    n = s.query(Notification).filter_by(kind="injury_record", case_id=past.id).order_by(Notification.id.desc()).first()
    assert r.notified_requester and n.user_id == past.requester_id
    text = records.summary_text(r, "蔡佩珊")
    assert "🚨 需留意：傷退" in text and "#12 陳大為" in text and "肌貼 1捲" in text


def test_cannot_record_for_case_not_hired(s):
    t = _u(s, "demo_t01")
    billing.start_trial(s, t)
    other = s.query(Case).filter_by(title="河濱親子路跑（測試・已結束）").one()
    with pytest.raises(records.RecordError, match="被錄用"):
        records.create(s, t, _rec(case_id=other.id))


@pytest.mark.parametrize("over,msg", [
    (dict(athlete=" "), "選手"),
    (dict(body_parts=[{"part": "膝關節", "side": None}]), "左、右或雙側"),
    (dict(injury_types=[]), "受傷類型"),
    (dict(injury_types=["其他"], injury_other=""), "其他"),
    (dict(treatments=[]), "處置"),
    (dict(consumables={"ice": 120}), "0–99"),
])
def test_record_validation(s, over, msg):
    t = _u(s, "demo_t01")
    billing.start_trial(s, t)
    with pytest.raises(records.RecordError, match=msg):
        records.create(s, t, _rec(**over))


def test_head_and_back_need_no_side(s):
    t = _u(s, "demo_t01")
    billing.start_trial(s, t)
    r = records.create(s, t, _rec(body_parts=[{"part": "頭 / 頸", "side": None}, {"part": "腰背", "side": None}],
                                  injury_types=["熱衰竭"]))
    assert records.parts_text(r) == "頭 / 頸、腰背"


def test_expired_user_keeps_old_records(s):
    t = _u(s, "demo_t09")
    before = s.query(InjuryRecord).filter_by(trainer_id=t.id).count()
    billing.admin_revoke(s, t.id)
    assert s.query(InjuryRecord).filter_by(trainer_id=t.id).count() == before == 3
    with pytest.raises(records.RecordError):
        records.create(s, t, _rec())


def test_consumable_totals_and_delete(s):
    t = _u(s, "demo_t09")
    recs = s.query(InjuryRecord).filter_by(trainer_id=t.id).all()
    assert records.consumable_totals(recs) == {"kinesio": 1, "white": 1, "elastic": 0, "ice": 4}
    records.delete(s, recs[0], t)
    assert recs[0].is_deleted
    with pytest.raises(records.RecordError):
        records.delete(s, recs[1], _u(s, "demo_t01"))


def test_record_url_prefers_liff(monkeypatch):
    monkeypatch.delenv("LIFF_ID", raising=False)
    monkeypatch.setenv("APP_BASE_URL", "https://x.streamlit.app")
    assert records.record_url(5) == "https://x.streamlit.app/?page=record&rec_case=5"
    monkeypatch.setenv("LIFF_ID", "2001-abc")
    assert records.record_url(5) == "https://liff.line.me/2001-abc?page=record&rec_case=5"


# --- LIFF 免登入 ---------------------------------------------------------------

def test_verify_liff_token(monkeypatch):
    from core import auth
    monkeypatch.setenv("LINE_LOGIN_CHANNEL_ID", "1234")

    class R:
        status_code = 200

        @staticmethod
        def json():
            return {"sub": "U999", "name": "小明", "aud": "1234"}
    with mock.patch("core.auth.requests.post", return_value=R()) as m:
        assert auth.verify_liff_token("tok")["sub"] == "U999"
        assert m.call_args.kwargs["data"] == {"id_token": "tok", "client_id": "1234"}

    class Bad:
        status_code = 400
    with mock.patch("core.auth.requests.post", return_value=Bad()):
        with pytest.raises(auth.LoginError):
            auth.verify_liff_token("expired")


def test_login_state_carries_record_page():
    from core import auth
    st_ = auth.read_state(auth.make_state(None, "record", 7))
    assert st_["p"] == "record" and st_["rc"] == 7
