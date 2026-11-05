"""防護員：防護紀錄表（付費功能）、紀錄查詢、方案與付費（模擬金流）。"""
from __future__ import annotations

import time as _time

import pandas as pd
import streamlit as st
from sqlalchemy import select

from core import auth, billing, config, db, records, ui
from core.constants import (
    BODY_PARTS, CONSUMABLES, DIVISIONS, INJURY_TYPES, LATERAL_PARTS, PAY_METHODS, PLANS, SERIOUS_TREATMENTS,
    SIDES, TREATMENTS, TRIAL_DAYS,
)
from core.db import InjuryRecord, Payment, Subscription, TrainerProfile, User, now
from views import nav

OTHER_EVENT = -1


def _me():
    u = auth.current_user()
    with db.session() as s:
        prof = s.get(TrainerProfile, u.id)
        sub = billing.active_subscription(s, u.id)
    return u, prof, sub


def _log_paywall(u: User, where: str):
    """每個 session 只記一次「看到付費牆」，用來算轉換率。"""
    key = f"_pw_logged_{where}"
    if not st.session_state.get(key):
        st.session_state[key] = True
        with db.session() as s:
            db.log(s, u.id, "paywall_view", where=where)


# --- 防護紀錄表 -------------------------------------------------------------------

def record_page():
    u, prof, sub = _me()
    if prof is None:
        st.title("防護紀錄表")
        ui.notice("請先填寫防護員履歷，再使用防護紀錄表。")
        if st.button("填寫我的履歷", type="primary"):
            nav.go("profile")
        return
    if sub is None:
        _paywall(u)
        return
    if prof.record_terms_at is None:
        _terms(u)
        return
    _record_form(u, prof, sub)


def _paywall(u: User):
    _log_paywall(u, "record")
    st.title("防護紀錄表")
    st.markdown(ui.tag("付費功能", "orange"), unsafe_allow_html=True)
    st.markdown("#### 30 秒記完一位選手，主辦方 LINE 立刻收到")
    st.markdown(
        "- 從 LINE 直接打開，不用登入，點選就能記錄部位、傷況、處置\n"
        "- 送出後主辦方 LINE 馬上收到紀錄，「建議轉診」「傷退」會特別標示\n"
        "- 自動統計肌貼、白貼、冰包用量，賽後請款、申請保險直接匯出 Excel\n"
        "- 所有紀錄雲端保存，隨時查詢")
    with db.session() as s:
        used = billing.trial_used(s, u.id)
    c1, c2 = st.columns(2)
    if not used and c1.button(f"免費試用 {TRIAL_DAYS} 天", type="primary", width="stretch"):
        with db.session() as s:
            billing.start_trial(s, s.get(User, u.id))
        ui.flash(f"已開通 {TRIAL_DAYS} 天免費試用！")
        st.rerun()
    if c2.button("查看方案與開通", width="stretch", type="secondary" if not used else "primary"):
        nav.go("plan")


def _terms(u: User):
    st.title("防護紀錄表")
    ui.notice("<b>開始使用前請確認</b><br>防護紀錄包含選手的傷病資訊，屬於《個人資料保護法》規範的健康相關資料。", warn=True)
    st.markdown(
        "1. 只記錄防護處置需要的資訊。選手可以只填**背號**，不一定要寫全名。\n"
        "2. 紀錄會傳送給**這場賽事的主辦方**，作為傷病追蹤、保險申請使用，不會提供給其他人。\n"
        "3. 請勿在備註填寫與處置無關的個人資料（身分證字號、住址等）。\n"
        "4. 選手或家長要求查詢、刪除紀錄時，請協助處理或聯繫平台。")
    if st.checkbox("我了解並會遵守以上規範"):
        if st.button("開始使用", type="primary"):
            with db.session() as s:
                s.get(TrainerProfile, u.id).record_terms_at = now()
                db.log(s, u.id, "record_terms_accepted")
            st.rerun()


def _record_form(u: User, prof: TrainerProfile, sub: Subscription):
    v = st.session_state.setdefault("rec_v", 0)
    k = lambda name: f"r{v}_{name}"  # noqa: E731  每送出一筆就換一組元件 → 表單自動清空
    st.session_state.setdefault(k("t0"), _time.time())

    with db.session() as s:
        cases = records.hired_cases(s, u.id)

    st.markdown("### 📋 防護紀錄表")
    last = st.session_state.get("rec_last")
    if last:
        st.success(f"✅ 已儲存：{last['who']}" + ("，已用 LINE 通知主辦方" if last["notified"] else ""))
        c1, c2, _ = st.columns([1.2, 1.2, 1.6])
        if last.get("team") and c1.button("同隊再記一筆", width="stretch"):
            st.session_state[k("team")] = last["team"]
            st.session_state[k("category")] = last.get("category")
            st.session_state.pop("rec_last")
            st.rerun()
        if c2.button("記下一位", width="stretch"):
            st.session_state.pop("rec_last")
            st.rerun()

    # --- Header：賽事、場地、防護員、案件編號 ---
    opts = [c.id for c in cases] + [OTHER_EVENT]
    titles = {c.id: f"No.{c.id:03d} {c.title}" for c in cases} | {OTHER_EVENT: "其他賽事（不在平台上的案件）"}
    want = st.session_state.pop("rec_case", None)
    if want in opts and k("case") not in st.session_state:
        st.session_state[k("case")] = want
    case_id = st.selectbox("🏆 賽事", opts, format_func=titles.get, key=k("case"))
    case = next((c for c in cases if c.id == case_id), None)
    c1, c2 = st.columns([3, 2])
    if case is None:
        event_name = c1.text_input("賽事名稱 *", key=k("event"), placeholder="例如：2026 台北市 3v3 籃球爭霸賽")
    else:
        event_name = case.title
    venue = (c2 if case is None else c1).text_input("場地 / 時段（選填）", key=k("venue"), placeholder="例如：場地 A")
    case_txt = f"｜案件 No.{case.id:03d}" if case else ""
    st.caption(f"防護員：{prof.name}{case_txt}")

    # --- STEP 1 ---
    st.markdown("##### STEP 1　選手基本資料")
    c1, c2 = st.columns(2)
    team = c1.text_input("隊伍 / 單位", key=k("team"), placeholder="狂風籃球隊")
    athlete = c2.text_input("選手姓名 / 背號 *", key=k("athlete"), placeholder="#12 陳大為")
    sport = case.sport_type if case else "其他"
    categories = [f"{sport} - {d}" for d in DIVISIONS]
    category = st.selectbox("項目 / 類別", categories, index=None, key=k("category"), placeholder="選擇組別（選填）")

    # --- STEP 2 ---
    st.markdown("##### STEP 2　受傷部位與狀況")
    parts = st.pills("人體部位（可複選）", BODY_PARTS, selection_mode="multi", key=k("parts")) or []
    body = []
    for part in [p for p in BODY_PARTS if p in parts]:
        if part in LATERAL_PARTS:
            side = st.segmented_control(f"{part}：哪一側？", SIDES, key=k(f"side_{part}"))
            body.append({"part": part, "side": side})
        else:
            body.append({"part": part, "side": None})
    types = st.pills("受傷類型（可複選）*", INJURY_TYPES, selection_mode="multi", key=k("types")) or []
    other = st.text_input("其他：請簡單說明", key=k("other")) if "其他" in types else ""

    # --- STEP 3 ---
    st.markdown("##### STEP 3　現場處置與耗材")
    treat = st.pills("處置項目（可複選）*", TREATMENTS, selection_mode="multi", key=k("treat")) or []
    if SERIOUS_TREATMENTS & set(treat):
        ui.notice("🚨 已選「" + "、".join(t for t in treat if t in SERIOUS_TREATMENTS)
                  + "」，送出時會在主辦方的通知中特別標示。", warn=True)
    cons = {}
    with st.container(key="rec_cons"):
        st.caption("耗材快速計數器")
        for row in (CONSUMABLES[:2], CONSUMABLES[2:]):  # 2×2，手機上也不換行
            for col, (key, label, unit) in zip(st.columns(2), row):
                cons[key] = int(col.number_input(f"{label}（{unit}）", min_value=0, max_value=99, step=1,
                                                 key=k(f"c_{key}")))
    note = st.text_input("備註（選填）", key=k("note"), placeholder="例如：建議賽後至醫院拍 X 光")

    notify = False
    if case:
        notify = st.toggle("同步用 LINE 通知主辦方", value=True, key=k("notify"))

    with st.container(key="rec_footer"):
        label = "💾 儲存並發送紀錄" if notify else "💾 儲存紀錄"
        submitted = st.button(label, type="primary", width="stretch", key=k("submit"))
    st.caption(f"方案有效至 {sub.ends_at:%Y/%m/%d}　·　紀錄可在「紀錄查詢」查看與匯出")

    if not submitted:
        return
    data = dict(case_id=case.id if case else None, event_name=event_name, venue=venue, team=team,
                athlete=athlete, category=category or "", body_parts=body, injury_types=types,
                injury_other=other, treatments=treat, consumables=cons, note=note,
                fill_seconds=int(_time.time() - st.session_state[k("t0")]))
    try:
        with db.session() as s:
            rec = records.create(s, s.get(User, u.id), data, notify=notify,
                                 source="liff" if st.session_state.get("via_liff") else "web")
            who = " ".join(x for x in (rec.team, rec.athlete) if x)
            st.session_state.rec_last = {"who": who, "team": rec.team, "category": category,
                                         "notified": rec.notified_requester}
    except records.RecordError as err:
        for m in str(err).split("；"):
            st.error(m)
        return
    st.session_state.rec_v = v + 1
    st.rerun()


# --- 紀錄查詢 ---------------------------------------------------------------------

def records_page():
    u = auth.current_user()
    st.title("紀錄查詢")
    with db.session() as s:
        recs = list(s.scalars(select(InjuryRecord).where(
            InjuryRecord.trainer_id == u.id, InjuryRecord.is_deleted.is_(False))
            .order_by(InjuryRecord.created_at.desc())))
        prof = s.get(TrainerProfile, u.id)
    if not recs:
        ui.empty("還沒有防護紀錄。")
        return
    events = sorted({r.event_name for r in recs})
    pick = st.selectbox("賽事", ["全部"] + events)
    shown = [r for r in recs if pick == "全部" or r.event_name == pick]

    c = st.columns(4)
    c[0].metric("紀錄筆數", len(shown))
    c[1].metric("轉診 / 傷退", sum(records.is_serious(r) for r in shown))
    tot = records.consumable_totals(shown)
    c[2].metric("肌貼 + 白貼（捲）", tot["kinesio"] + tot["white"])
    c[3].metric("冰罐 / 冰包（個）", tot["ice"])
    st.caption("耗材合計：" + "、".join(f"{label} {tot[key]} {unit}" for key, label, unit in CONSUMABLES))

    df = pd.DataFrame(records.to_rows(shown, {u.id: prof.name if prof else ""}))
    st.download_button("下載 CSV（Excel 可直接開啟）", df.to_csv(index=False).encode("utf-8-sig"),
                       file_name="防護紀錄.csv", mime="text/csv")
    st.dataframe(df, hide_index=True, width="stretch")

    st.markdown("#### 最近的紀錄")
    for r in shown[:20]:
        title = f"{r.created_at:%m/%d %H:%M}　{' '.join(x for x in (r.team, r.athlete) if x)}"
        with st.expander(("🚨 " if records.is_serious(r) else "") + title):
            st.text(records.summary_text(r, prof.name if prof else ""))
            if st.button("刪除這筆紀錄", key=f"del_rec_{r.id}", type="tertiary"):
                with db.session() as s:
                    records.delete(s, s.get(InjuryRecord, r.id), s.get(User, u.id))
                ui.flash("已刪除。", "info")
                st.rerun()


# --- 方案與付費 -------------------------------------------------------------------

def plan_page():
    u, prof, sub = _me()
    st.title("方案與付費")
    if config.billing_mode() == "mock":
        ui.notice("🧪 <b>功能試驗中</b>：目前使用模擬金流，所有付款都<b>不會真的扣款</b>。", warn=True)

    pending_id = st.session_state.get("checkout_payment")
    if pending_id:
        _mock_checkout(u, pending_id)
        return

    with db.session() as s:
        history = list(s.scalars(select(Payment).where(Payment.user_id == u.id).order_by(Payment.id.desc())))
        used = billing.trial_used(s, u.id)

    if sub:
        kind = {"trial": "免費試用", "admin": "平台開通"}.get(sub.source, PLANS.get(sub.plan, {}).get("name", sub.plan))
        days_left = max(0, -(-int((sub.ends_at - now()).total_seconds()) // 86400))  # 無條件進位
        with st.container(border=True):
            st.markdown(f"**目前方案**　{ui.tag(kind, 'ok')}", unsafe_allow_html=True)
            st.markdown(f"有效至 **{sub.ends_at:%Y/%m/%d %H:%M}**（剩 {days_left} 天）")
            if sub.auto_renew:
                st.caption("自動續訂：開啟（模擬，不會真的扣款）")
                if st.button("取消自動續訂"):
                    with db.session() as s:
                        billing.cancel_auto_renew(s, s.get(User, u.id))
                    ui.flash("已取消自動續訂，到期前仍可正常使用。", "info")
                    st.rerun()
        st.markdown("#### 續約或升級")
    else:
        _log_paywall(u, "plan")
        st.markdown("#### 開通「防護紀錄 Pro」")
        st.write("在 LINE 裡 30 秒完成一筆防護紀錄，主辦方即時收到，耗材自動統計。")
        if not used and prof:
            if st.button(f"先免費試用 {TRIAL_DAYS} 天", type="primary"):
                with db.session() as s:
                    billing.start_trial(s, s.get(User, u.id))
                ui.flash(f"已開通 {TRIAL_DAYS} 天免費試用！")
                nav.go("record")

    if prof is None:
        st.info("請先填寫防護員履歷，才能開通付費功能。")
        return
    cols = st.columns(len(PLANS))
    for col, (key, p) in zip(cols, PLANS.items()):
        with col, st.container(border=True):
            st.markdown(f"**{p['name']}**")
            st.markdown(f"<span style='font-size:1.6rem;font-weight:900;color:var(--orange)'>NT${p['price']:,}</span>",
                        unsafe_allow_html=True)
            st.caption(p["note"])
            if st.button("選擇此方案", key=f"plan_{key}", width="stretch",
                         type="primary" if key == "yearly" else "secondary"):
                st.session_state.checkout_plan = key
                st.rerun()

    plan = st.session_state.get("checkout_plan")
    if plan:
        with st.container(border=True):
            p = PLANS[plan]
            st.markdown(f"**結帳：{p['name']}**　NT${p['price']:,}")
            if sub:
                st.caption(f"會接在目前方案之後，從 {sub.ends_at:%Y/%m/%d} 起算 {p['days']} 天。")
            method = st.radio("付款方式", PAY_METHODS, horizontal=True)
            c1, c2, _ = st.columns([1, 1, 2])
            if c1.button("前往付款", type="primary", width="stretch"):
                with db.session() as s:
                    start = billing.create_checkout(s, s.get(User, u.id), plan, method)
                    st.session_state.checkout_payment = start.payment.id
                st.session_state.pop("checkout_plan", None)
                st.rerun()
            if c2.button("取消", width="stretch"):
                st.session_state.pop("checkout_plan", None)
                st.rerun()

    if history:
        st.markdown("#### 付款紀錄")
        label = {"paid": "成功", "failed": "失敗", "cancelled": "取消", "pending": "未完成"}
        st.dataframe(pd.DataFrame([{
            "時間": x.created_at, "訂單編號": x.trade_no, "方案": PLANS.get(x.plan, {}).get("name", x.plan),
            "金額": x.amount, "付款方式": x.method, "狀態": label.get(x.status, x.status)} for x in history]),
            hide_index=True, width="stretch")


def _mock_checkout(u: User, payment_id: int):
    """模擬金流的付款頁。真金流時這一頁會換成導向綠界 / 藍新 / LINE Pay。"""
    with db.session() as s:
        pay = s.get(Payment, payment_id)
    if pay is None or pay.user_id != u.id or pay.status != "pending":
        st.session_state.pop("checkout_payment", None)
        st.rerun()
    p = PLANS[pay.plan]
    with st.container(border=True):
        st.markdown("#### 🧪 模擬付款頁")
        st.caption("正式上線後，這一步會跳轉到金流公司的付款頁面。")
        st.markdown(f"訂單編號：`{pay.trade_no}`  \n方案：{p['name']}  \n付款方式：{pay.method}  \n"
                    f"金額：**NT${pay.amount:,}**")
        if pay.method == "信用卡":
            st.caption("測試卡號 4311-9522-2222-2222，有效期限任意未來日期，CVV 222（僅供展示）")
        c1, c2, c3 = st.columns(3)
        outcome = None
        if c1.button("✅ 模擬付款成功", type="primary", width="stretch"):
            outcome = "paid"
        if c2.button("❌ 模擬付款失敗", width="stretch"):
            outcome = "failed"
        if c3.button("取消付款", width="stretch"):
            outcome = "cancelled"
    if outcome:
        with db.session() as s:
            billing.provider().simulate(s, s.get(Payment, payment_id), outcome)
        st.session_state.pop("checkout_payment", None)
        if outcome == "paid":
            ui.flash(f"付款成功（模擬），已開通 {p['name']}！")
            nav.go("record")
        else:
            ui.flash("付款失敗（模擬），沒有扣款，可以再試一次。" if outcome == "failed" else "已取消付款。",
                     "warning" if outcome == "failed" else "info")
        st.rerun()
