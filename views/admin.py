"""管理後台：數據總覽、證照審核、檢舉與黑名單、案件與通知紀錄、資料匯出。"""
from __future__ import annotations

from collections import Counter

import pandas as pd
import streamlit as st
from sqlalchemy import delete, select

from core import auth, config, db, fmt, services, ui
from core import records as records_mod
from core.constants import CONSUMABLES, LICENSE_FIELDS, PLANS
from core.db import (
    ActivityLog, Application, Case, CaseQuestion, Feedback, InjuryRecord, Notification, Payment, Rating, Report,
    Subscription,
    RequesterProfile, TrainerProfile, User,
)
from core.matching import find_matches, years_since


def _guard() -> bool:
    if not auth.is_admin():
        st.error("需要管理員權限")
        return False
    return True


def _exclude_demo_toggle(key: str) -> bool:
    return st.toggle("排除測試帳號（demo_ 開頭）", value=True, key=key)


def _real_user_ids(s, exclude_demo: bool) -> set[int]:
    q = select(User.id)
    if exclude_demo:
        q = q.where(~User.line_user_id.like("demo_%"))
    return set(s.scalars(q))


# --- 數據總覽 ---------------------------------------------------------------

def dashboard():
    if not _guard():
        return
    st.title("數據總覽")
    ex = _exclude_demo_toggle("dash_ex")
    with db.session() as s:
        ids = _real_user_ids(s, ex)
        users = [u for u in s.scalars(select(User)) if u.id in ids]
        trainers = [p for p in s.scalars(select(TrainerProfile)) if p.user_id in ids]
        reqs = [p for p in s.scalars(select(RequesterProfile)) if p.user_id in ids]
        cases = [c for c in s.scalars(select(Case)) if c.requester_id in ids]
        apps = [a for a in s.scalars(select(Application)) if a.trainer_id in ids]
        notes = [n for n in s.scalars(select(Notification)) if n.user_id in ids]
        fbs = [f for f in s.scalars(select(Feedback)) if f.user_id is None or f.user_id in ids]
        logs = [lg for lg in s.scalars(select(ActivityLog)) if lg.user_id is None or lg.user_id in ids]
        qs = [q for q in s.scalars(select(CaseQuestion)) if q.trainer_id in ids]

    st.markdown("#### 用戶")
    c = st.columns(4)
    c[0].metric("註冊人數", len(users))
    c[1].metric("防護員（已填履歷）", len(trainers))
    c[2].metric("通過審核", sum(p.review_status == "approved" for p in trainers))
    c[3].metric("發案方（已填資料）", len(reqs))

    st.markdown("#### 媒合")
    hired_cases = {a.case_id for a in apps if a.status == "hired"}
    finished = [x for x in cases if x.status != "open"]
    c = st.columns(4)
    c[0].metric("累計案件", len(cases), f"招募中 {sum(x.status == 'open' for x in cases)}", delta_color="off")
    c[1].metric("應徵次數", len(apps))
    c[2].metric("成功錄用", sum(a.status == "hired" for a in apps))
    rate = f"{len(hired_cases & {x.id for x in finished}) / len(finished):.0%}" if finished else "—"
    c[3].metric("結案媒合率", rate, help="已結束的案件中，至少錄用 1 人的比例")
    c = st.columns(4)
    c[0].metric("防護員提問", len(qs))
    c[1].metric("已回覆", sum(q.answer is not None for q in qs))
    answered = [(q.answered_at - q.created_at).total_seconds() / 3600 for q in qs if q.answered_at]
    c[2].metric("平均回覆時間", f"{sum(answered) / len(answered):.1f} 小時" if answered else "—")
    c[3].metric("待回覆", sum(q.answer is None for q in qs))
    c = st.columns(4)
    c[0].metric("LINE 實際發送", sum(n.status == "sent" for n in notes))
    c[1].metric("模擬發送", sum(n.status == "simulated" for n in notes))
    c[2].metric("發送失敗", sum(n.status == "failed" for n in notes))
    avg = [x.notified_count for x in cases]
    c[3].metric("每案平均通知人數", f"{sum(avg) / len(avg):.1f}" if avg else "—")

    st.markdown("#### 轉換漏斗")
    acted = lambda action: len({lg.user_id for lg in logs if lg.action == action and lg.user_id})  # noqa: E731
    funnel = pd.DataFrame({
        "步驟": ["LINE 登入註冊", "選擇身分", "防護員填履歷", "防護員通過審核", "發案方刊登賽事", "防護員提問",
               "防護員應徵", "發案方錄用"],
        "人數": [len(users), sum(u.role is not None for u in users), len(trainers),
                sum(p.review_status == "approved" for p in trainers), acted("case_posted"),
                acted("ask"), acted("apply"), acted("hire")],
    })
    st.dataframe(funnel, hide_index=True, width="stretch")

    if users:
        st.markdown("#### 每日新註冊")
        daily = pd.Series(1, index=pd.to_datetime([u.created_at for u in users])).resample("D").sum()
        st.bar_chart(daily.rename("新註冊"))

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("#### 防護員接案縣市")
        cnt = Counter(x for p in trainers for x in (p.service_counties or []))
        if cnt:
            st.bar_chart(pd.Series(cnt).sort_values(ascending=False).rename("人數"), horizontal=True)
    with c2:
        st.markdown("#### 案件運動項目")
        cnt = Counter(x.sport_type for x in cases)
        if cnt:
            st.bar_chart(pd.Series(cnt).sort_values(ascending=False).rename("案件數"), horizontal=True)

    st.markdown("#### 用戶回饋")
    if not fbs:
        st.caption("還沒有回饋。")
        return
    scored = [f.score for f in fbs if f.score]
    c = st.columns(3)
    c[0].metric("回饋筆數", len(fbs))
    c[1].metric("平均滿意度", f"{sum(scored) / len(scored):.1f} / 5" if scored else "—")
    wish = Counter(w for f in fbs for w in (f.wishes or []))
    c[2].metric("最想要的功能", wish.most_common(1)[0][0] if wish else "—")
    st.dataframe(pd.DataFrame([{
        "時間": f.created_at, "身分": f.role, "星等": f.score, "想要的功能": "、".join(f.wishes or []),
        "來源": f.source, "內容": f.message} for f in sorted(fbs, key=lambda f: f.created_at, reverse=True)]),
        hide_index=True, width="stretch")


# --- 證照審核 ---------------------------------------------------------------

def review():
    if not _guard():
        return
    st.title("證照審核")
    st.caption("請將證號到官方系統核對：運動防護員證 → 教育部體育署；物理治療師 → 衛福部醫事人員查詢系統；"
               "EMT → 核發單位（各縣市消防局或訓練機構）。核對無誤再按「審核通過」。")
    status = st.segmented_control("狀態", ["pending", "rejected", "approved"], default="pending",
                                  format_func=lambda x: ui.REVIEW_STATUS[x][0])
    with db.session() as s:
        profs = s.scalars(select(TrainerProfile).where(TrainerProfile.review_status == (status or "pending"))
                          .order_by(TrainerProfile.updated_at)).all()
        for p in profs:
            _ = p.user
        # 同一個證號被多個帳號使用 → 標記可疑
        used: dict[tuple[str, str], list[int]] = {}
        for o in s.scalars(select(TrainerProfile)):
            for field, kind, _l, _h in LICENSE_FIELDS:
                if no := getattr(o, field):
                    used.setdefault((kind, no.strip().upper()), []).append(o.user_id)
    if not profs:
        ui.empty("這個狀態下沒有資料。")
        return
    st.caption(f"共 {len(profs)} 位")
    for p in profs:
        with st.container(border=True):
            lic = [x for x, ok in (("AT", p.has_at_license), ("PT", p.has_pt_license)) if ok] + list(p.first_aid_licenses or [])
            st.markdown(f"**{ui.e(p.name)}**　{ui.e(p.gender)}　{ui.e(p.school)}（{p.graduation_year} 畢業，"
                        f"{years_since(p.graduation_year)} 年）{' 🚫 已封鎖' if p.user.is_blocked else ''}<br>"
                        f"{''.join(ui.tag(x) for x in lic) or ui.tag('未填證照', 'grey')}"
                        f"{''.join(ui.tag(x, 'grey') for x in p.service_counties or [])}",
                        unsafe_allow_html=True)
            st.caption(f"電話 {p.phone}　Email {p.email}　LINE 顯示名稱 {p.user.display_name}　"
                       f"更新於 {p.updated_at:%Y/%m/%d %H:%M}")
            if p.experience:
                st.caption(p.experience)
            rows = [(label, kind, getattr(p, field)) for field, kind, label, _h in LICENSE_FIELDS]
            lines = []
            for label, kind, no in rows:
                if not no:
                    continue
                dup = [x for x in used.get((kind, no.strip().upper()), []) if x != p.user_id]
                warn = f"　{ui.tag(f'⚠ 與帳號 #{dup[0]} 證號重複', 'warn')}" if dup else ""
                lines.append(f"{label}：<b>{ui.e(no)}</b>{warn}")
            if lines:
                st.markdown("<br>".join(lines), unsafe_allow_html=True)
            else:
                st.warning("沒有填寫任何證號（舊版資料），請退件請對方補填")
            note = st.text_input("退件原因（退件時必填，會用 LINE 告訴對方）", key=f"note_{p.user_id}")
            c1, c2, _ = st.columns([1, 1, 3])
            if status != "approved" and c1.button("審核通過", key=f"ok_{p.user_id}", type="primary"):
                with db.session() as s:
                    services.review_trainer(s, s.get(TrainerProfile, p.user_id), True)
                ui.flash(f"{p.name} 已審核通過")
                st.rerun()
            if c2.button("退件", key=f"no_{p.user_id}", disabled=not note.strip()):
                with db.session() as s:
                    services.review_trainer(s, s.get(TrainerProfile, p.user_id), False, note.strip())
                ui.flash(f"已退件 {p.name}", "info")
                st.rerun()


# --- 檢舉與黑名單 -------------------------------------------------------------

def reports():
    if not _guard():
        return
    st.title("檢舉與黑名單")
    t1, t2, t3 = st.tabs(["待處理檢舉", "已處理", "黑名單管理"])
    with db.session() as s:
        all_reports = s.scalars(select(Report).order_by(Report.created_at.desc())).all()
        for r in all_reports:
            _ = r.case, r.trainer_user, r.trainer_user.trainer
        users = s.scalars(select(User).order_by(User.id)).all()
        for u in users:
            _ = u.trainer, u.requester

    with t1:
        pending = [r for r in all_reports if r.status == "pending"]
        if not pending:
            ui.empty("沒有待處理的檢舉。")
        for r in pending:
            prof = r.trainer_user.trainer
            with st.container(border=True):
                st.markdown(f"**{ui.e(r.reason)}**　被檢舉：{ui.e(prof.name if prof else r.trainer_user.display_name)}　"
                            f"案件 No.{r.case_id:03d}「{ui.e(r.case.title)}」", unsafe_allow_html=True)
                st.caption(f"{r.created_at:%Y/%m/%d %H:%M}　{r.detail or '（無說明）'}")
                block = st.checkbox("查證屬實後同時封鎖帳號（無法登入、不再收到通知）", key=f"blk_{r.id}")
                c1, c2, _ = st.columns([1, 1, 3])
                if c1.button("查證屬實", key=f"cf_{r.id}", type="primary"):
                    with db.session() as s:
                        services.handle_report(s, s.get(Report, r.id), True, block)
                    st.rerun()
                if c2.button("不成立", key=f"dm_{r.id}"):
                    with db.session() as s:
                        services.handle_report(s, s.get(Report, r.id), False)
                    st.rerun()
    with t2:
        done = [r for r in all_reports if r.status != "pending"]
        if done:
            st.dataframe(pd.DataFrame([{
                "時間": r.created_at, "案件": r.case_id, "防護員": r.trainer_user.display_name,
                "原因": r.reason, "結果": "屬實" if r.status == "confirmed" else "不成立",
                "處理時間": r.handled_at} for r in done]), hide_index=True, width="stretch")
        else:
            st.caption("沒有紀錄。")
    with t3:
        blocked = [u for u in users if u.is_blocked]
        st.markdown(f"**目前封鎖 {len(blocked)} 個帳號**")
        for u in blocked:
            c1, c2 = st.columns([4, 1])
            c1.markdown(f"{ui.e(u.trainer.name if u.trainer else u.display_name)}　"
                        f"<span class='locked'>{ui.e(u.blocked_reason or '')}</span>", unsafe_allow_html=True)
            if c2.button("解除封鎖", key=f"ub_{u.id}"):
                with db.session() as s:
                    services.set_blocked(s, s.get(User, u.id), False)
                st.rerun()
        st.divider()
        opts = {u.id: f"{u.trainer.name if u.trainer else (u.requester.org_name if u.requester else u.display_name)}"
                      f"（#{u.id}，{u.display_name}）" for u in users if not u.is_blocked}
        pick = st.selectbox("手動封鎖帳號", list(opts), format_func=opts.get, index=None, placeholder="搜尋姓名")
        reason = st.text_input("封鎖原因")
        if st.button("封鎖", disabled=pick is None or not reason.strip()):
            with db.session() as s:
                services.set_blocked(s, s.get(User, pick), True, reason.strip())
            st.rerun()


# --- 案件與通知紀錄 -----------------------------------------------------------

def cases():
    if not _guard():
        return
    st.title("案件與通知紀錄")
    with db.session() as s:
        all_cases = s.scalars(select(Case).order_by(Case.id.desc())).all()
        orgs = {o.user_id: o.org_name for o in s.scalars(select(RequesterProfile))}
    if not all_cases:
        ui.empty("還沒有案件。")
        return
    st.dataframe(pd.DataFrame([{
        "編號": c.id, "賽會": c.title, "項目": c.sport_type, "時間": fmt.schedule_summary(c), "縣市": c.location_county,
        "發案方": orgs.get(c.requester_id, ""), "狀態": ui.CASE_STATUS[c.status][0],
        "已通知": c.notified_count, "建立時間": c.created_at} for c in all_cases]),
        hide_index=True, width="stretch")

    st.markdown("#### 配對驗證")
    st.caption("選一個案件，比對「實際收到通知的人」與「現在重新跑配對的結果」。驗收配對精準度用。")
    opts = {c.id: f"No.{c.id:03d} {c.title}" for c in all_cases}
    cid = st.selectbox("案件", list(opts), format_func=opts.get)
    with db.session() as s:
        c = s.get(Case, cid)
        sent = s.execute(select(Notification, User).join(User, User.id == Notification.user_id)
                         .where(Notification.case_id == cid).order_by(Notification.id)).all()
        now_match = find_matches(s, c)
        names = {p.user_id: p.name for p in s.scalars(select(TrainerProfile))}
    ui.case_card(c)
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**發案當下收到邀請的人**")
        inv = [(n, u) for n, u in sent if n.kind == "case_invite"]
        st.dataframe(pd.DataFrame([{"防護員": names.get(u.id, u.display_name), "管道": n.channel,
                                    "狀態": n.status, "時間": n.created_at} for n, u in inv]),
                     hide_index=True, width="stretch")
    with c2:
        st.markdown("**以目前資料重新配對**")
        st.dataframe(pd.DataFrame([{"防護員": p.name, "學校": p.school, "AT": p.has_at_license,
                                    "縣市": "、".join(p.service_counties)} for _, p in now_match]),
                     hide_index=True, width="stretch")
    other = [(n, u) for n, u in sent if n.kind != "case_invite"]
    if other:
        st.markdown("**此案件的其他通知**")
        st.dataframe(pd.DataFrame([{"對象": names.get(u.id, u.display_name), "類型": n.kind, "狀態": n.status,
                                    "錯誤": n.error, "時間": n.created_at} for n, u in other]),
                     hide_index=True, width="stretch")

    st.markdown("#### 最近 200 則通知")
    with db.session() as s:
        rows = s.execute(select(Notification, User).join(User, User.id == Notification.user_id)
                         .order_by(Notification.id.desc()).limit(200)).all()
    st.dataframe(pd.DataFrame([{"時間": n.created_at, "對象": names.get(u.id, u.display_name), "類型": n.kind,
                                "管道": n.channel, "狀態": n.status, "內容": n.summary, "錯誤": n.error}
                               for n, u in rows]), hide_index=True, width="stretch")
    if not config.line_push_enabled():
        st.info("尚未設定 LINE_MESSAGING_ACCESS_TOKEN，所有通知都是「模擬發送」，只寫入紀錄。")


# --- 資料匯出 ---------------------------------------------------------------

def export():
    if not _guard():
        return
    st.title("資料匯出")
    st.caption("CSV 使用 UTF-8 BOM 編碼，Excel 直接開啟不會亂碼。")
    ex = _exclude_demo_toggle("exp_ex")
    with db.session() as s:
        ids = _real_user_ids(s, ex)
        tables = {
            "users": pd.DataFrame([{
                "id": u.id, "LINE 顯示名稱": u.display_name, "身分": u.role, "封鎖": u.is_blocked,
                "註冊時間": u.created_at, "最後登入": u.last_login_at}
                for u in s.scalars(select(User)) if u.id in ids]),
            "trainers": pd.DataFrame([{
                "user_id": p.user_id, "姓名": p.name, "性別": p.gender, "電話": p.phone, "Email": p.email,
                "LINE ID": p.line_contact_id, "學校": p.school, "畢業年": p.graduation_year,
                "接案縣市": "、".join(p.service_counties or []), "AT": p.has_at_license, "PT": p.has_pt_license,
                "急救證照": "、".join(p.first_aid_licenses or []),
                **{f"{code}證號": getattr(p, field) for field, code, _l, _h in LICENSE_FIELDS},
                "經歷": p.experience,
                "審核狀態": p.review_status, "建立時間": p.created_at}
                for p in s.scalars(select(TrainerProfile)) if p.user_id in ids]),
            "requesters": pd.DataFrame([{
                "user_id": o.user_id, "單位": o.org_name, "類型": o.org_type, "聯絡人": o.contact_name,
                "電話": o.phone, "Email": o.email, "LINE ID": o.line_contact_id, "建立時間": o.created_at}
                for o in s.scalars(select(RequesterProfile)) if o.user_id in ids]),
            "cases": pd.DataFrame([{
                "id": c.id, "requester_id": c.requester_id, "賽會": c.title, "項目": c.sport_type,
                "開始日期": c.event_date, "結束日期": c.end_date, "時段數": len(c.schedule),
                "時段明細": "；".join(fmt.schedule_lines(c)), "縣市": c.location_county, "地址": c.location_detail,
                "薪資方式": c.salary_type, "金額": c.salary_amount, "人數": c.headcount,
                "證照要求": c.license_req, "需EMT": c.require_emt, "資歷": c.exp_mode, "年數": c.exp_years,
                "指定學校": c.required_school, "高風險": c.is_high_risk, "狀態": c.status,
                "通知人數": c.notified_count, "建立時間": c.created_at}
                for c in s.scalars(select(Case)) if c.requester_id in ids]),
            "applications": pd.DataFrame([{
                "id": a.id, "case_id": a.case_id, "trainer_id": a.trainer_id, "應徵時間": a.applied_at,
                "狀態": a.status, "錄用時間": a.hired_at,
                "可出席時段": "；".join(fmt.slot_label(x.session) for x in a.slots),
                "錄用時段": "；".join(fmt.slot_label(x.session) for x in a.slots if x.status == "hired")}
                for a in s.scalars(select(Application)) if a.trainer_id in ids]),
            "questions": pd.DataFrame([{
                "id": q.id, "case_id": q.case_id, "trainer_id": q.trainer_id, "問題": q.question,
                "回覆": q.answer, "公開": q.is_public, "提問時間": q.created_at, "回覆時間": q.answered_at}
                for q in s.scalars(select(CaseQuestion)) if q.trainer_id in ids]),
            "injury_records": pd.DataFrame(records_mod.to_rows(
                [r for r in s.scalars(select(InjuryRecord)) if r.trainer_id in ids and not r.is_deleted])),
            "payments": pd.DataFrame([{
                "訂單編號": p.trade_no, "user_id": p.user_id, "方案": p.plan, "金額": p.amount, "付款方式": p.method,
                "金流": p.provider, "狀態": p.status, "建立時間": p.created_at, "付款時間": p.paid_at}
                for p in s.scalars(select(Payment)) if p.user_id in ids]),
            "ratings": pd.DataFrame([{
                "case_id": r.case_id, "trainer_id": r.trainer_id, "分數": r.score, "評語": r.comment,
                "時間": r.created_at} for r in s.scalars(select(Rating)) if r.trainer_id in ids]),
            "feedback": pd.DataFrame([{
                "時間": f.created_at, "user_id": f.user_id, "身分": f.role, "星等": f.score,
                "想要的功能": "、".join(f.wishes or []), "來源": f.source, "內容": f.message}
                for f in s.scalars(select(Feedback)) if f.user_id is None or f.user_id in ids]),
            "activity_log": pd.DataFrame([{
                "時間": lg.created_at, "user_id": lg.user_id, "動作": lg.action, "細節": lg.meta}
                for lg in s.scalars(select(ActivityLog)) if lg.user_id is None or lg.user_id in ids]),
        }
    labels = {"users": "帳號", "trainers": "防護員履歷", "requesters": "發案方", "cases": "案件",
              "applications": "應徵紀錄", "questions": "案件問答", "injury_records": "防護紀錄", "payments": "付款紀錄", "ratings": "評價", "feedback": "用戶回饋", "activity_log": "行為紀錄"}
    cols = st.columns(4)
    for i, (k, df) in enumerate(tables.items()):
        cols[i % 4].download_button(f"{labels[k]}（{len(df)}）", df.to_csv(index=False).encode("utf-8-sig"),
                                    file_name=f"{k}.csv", mime="text/csv", width="stretch")

    if config.demo_mode():
        st.divider()
        st.markdown("**清除測試資料**　刪除所有 demo_ 開頭的帳號與相關案件、應徵、通知。真實用戶不受影響。")
        if st.button("清除測試資料", type="secondary"):
            with db.session() as s:
                n = purge_demo(s)
            ui.flash(f"已刪除 {n} 個測試帳號與相關資料。")
            st.rerun()


def purge_demo(s) -> int:
    demo = list(s.scalars(select(User.id).where(User.line_user_id.like("demo_%"))))
    if not demo:
        return 0
    case_ids = list(s.scalars(select(Case.id).where(Case.requester_id.in_(demo))))
    for model, col in ((Notification, Notification.case_id), (Application, Application.case_id),
                       (CaseQuestion, CaseQuestion.case_id),
                       (Rating, Rating.case_id), (Report, Report.case_id)):
        if case_ids:
            s.execute(delete(model).where(col.in_(case_ids)))
    s.execute(delete(Notification).where(Notification.user_id.in_(demo)))
    s.execute(delete(Application).where(Application.trainer_id.in_(demo)))
    s.execute(delete(Rating).where(Rating.trainer_id.in_(demo) | Rating.requester_id.in_(demo)))
    s.execute(delete(Report).where(Report.trainer_id.in_(demo) | Report.reporter_id.in_(demo)))
    if case_ids:
        s.execute(delete(Case).where(Case.id.in_(case_ids)))
    s.execute(delete(CaseQuestion).where(CaseQuestion.trainer_id.in_(demo)))
    s.execute(delete(InjuryRecord).where(InjuryRecord.trainer_id.in_(demo)))
    if case_ids:
        s.execute(delete(InjuryRecord).where(InjuryRecord.case_id.in_(case_ids)))
    s.execute(delete(Subscription).where(Subscription.user_id.in_(demo)))
    s.execute(delete(Payment).where(Payment.user_id.in_(demo)))
    s.execute(delete(TrainerProfile).where(TrainerProfile.user_id.in_(demo)))
    s.execute(delete(RequesterProfile).where(RequesterProfile.user_id.in_(demo)))
    s.execute(delete(Feedback).where(Feedback.user_id.in_(demo)))
    s.execute(delete(ActivityLog).where(ActivityLog.user_id.in_(demo)))
    s.execute(delete(User).where(User.id.in_(demo)))
    return len(demo)


# --- 付費功能試驗 -------------------------------------------------------------

def billing_page():
    if not _guard():
        return
    from statistics import median

    from core import billing
    records = records_mod

    st.title("付費功能試驗")
    st.caption(f"金流模式：{config.billing_mode()}（mock = 模擬，不會扣款）")
    ex = _exclude_demo_toggle("bill_ex")
    with db.session() as s:
        ids = _real_user_ids(s, ex)
        logs = [lg for lg in s.scalars(select(ActivityLog).where(ActivityLog.action.in_([
            "paywall_view", "trial_start", "checkout_start", "payment_paid", "payment_failed",
            "payment_cancelled", "record_created", "auto_renew_off"]))) if lg.user_id in ids]
        pays = [p for p in s.scalars(select(Payment).order_by(Payment.id.desc())) if p.user_id in ids]
        recs = [r for r in s.scalars(select(InjuryRecord).where(InjuryRecord.is_deleted.is_(False)))
                if r.trainer_id in ids]
        trainers = {p.user_id: p.name for p in s.scalars(select(TrainerProfile)) if p.user_id in ids}
        active = {uid: billing.active_subscription(s, uid) for uid in trainers}

    who = lambda a: {lg.user_id for lg in logs if lg.action == a}  # noqa: E731
    t_funnel, t_subs, t_pay, t_use = st.tabs(["轉換漏斗", "訂閱管理", "付款紀錄", "紀錄使用情形"])

    with t_funnel:
        saw, trial, start, paid = who("paywall_view"), who("trial_start"), who("checkout_start"), who("payment_paid")
        used = {r.trainer_id for r in recs}
        funnel = pd.DataFrame({
            "步驟": ["看到付費牆", "開始免費試用", "試用後有實際記錄", "進入結帳", "付款成功（模擬）"],
            "人數": [len(saw), len(trial), len(trial & used), len(start), len(paid)],
        })
        funnel["相對上一步"] = [""] + [f"{b / a:.0%}" if a else "—" for a, b in
                                    zip(funnel["人數"][:-1], funnel["人數"][1:])]
        st.dataframe(funnel, hide_index=True, width="stretch")
        c = st.columns(4)
        c[0].metric("付費牆 → 付款", f"{len(paid & saw) / len(saw):.0%}" if saw else "—",
                    help="看過付費牆的人當中，最後付款成功的比例")
        c[1].metric("結帳放棄率", f"{len(start - paid) / len(start):.0%}" if start else "—",
                    help="進入結帳、但始終沒有付款成功的人數比例")
        c[2].metric("模擬營收", f"NT${sum(p.amount for p in pays if p.status == 'paid'):,}")
        c[3].metric("目前有效訂閱", sum(1 for x in active.values() if x))
        mix = pd.Series([p.plan for p in pays if p.status == "paid"]).map(lambda k: PLANS.get(k, {}).get("name", k))
        if len(mix):
            st.markdown("**付費方案分布**")
            st.bar_chart(mix.value_counts().rename("筆數"), horizontal=True)
        st.caption("提示：這是「願付意願」的實驗數據。真正上線收費前，可以調整 core/constants.py 的價格做比較。")

    with t_subs:
        rows = []
        for uid, name in trainers.items():
            sub = active[uid]
            rows.append({"user_id": uid, "防護員": name,
                         "狀態": ({"trial": "試用中", "paid": "付費中", "admin": "平台開通"}.get(sub.source, sub.source)
                                if sub else "未開通"),
                         "到期": sub.ends_at if sub else None, "自動續訂": bool(sub and sub.auto_renew),
                         "紀錄筆數": sum(r.trainer_id == uid for r in recs)})
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        st.markdown("**手動開通 / 停用**（例如給種子用戶、合作學校）")
        c1, c2, c3 = st.columns([2, 1, 1])
        pick = c1.selectbox("防護員", list(trainers), format_func=lambda i: f"{trainers[i]}（#{i}）",
                            index=None, placeholder="選擇防護員")
        days = c2.number_input("天數", min_value=1, max_value=730, value=30)
        note = c3.text_input("備註", placeholder="種子用戶")
        b1, b2, _ = st.columns([1, 1, 2])
        if b1.button("開通", type="primary", disabled=pick is None):
            with db.session() as s:
                billing.admin_grant(s, pick, int(days), note)
            ui.flash(f"已為 {trainers[pick]} 開通 {days} 天")
            st.rerun()
        if b2.button("立即停用", disabled=pick is None or not active.get(pick)):
            with db.session() as s:
                billing.admin_revoke(s, pick)
            ui.flash(f"已停用 {trainers[pick]} 的付費功能", "info")
            st.rerun()

    with t_pay:
        if pays:
            st.dataframe(pd.DataFrame([{
                "時間": p.created_at, "訂單編號": p.trade_no, "防護員": trainers.get(p.user_id, p.user_id),
                "方案": PLANS.get(p.plan, {}).get("name", p.plan), "金額": p.amount, "付款方式": p.method,
                "金流": p.provider, "狀態": p.status, "付款時間": p.paid_at} for p in pays]),
                hide_index=True, width="stretch")
        else:
            st.caption("還沒有付款紀錄。")

    with t_use:
        if not recs:
            st.caption("還沒有防護紀錄。")
            return
        secs = [r.fill_seconds for r in recs if r.fill_seconds]
        c = st.columns(4)
        c[0].metric("紀錄總數", len(recs))
        c[1].metric("填寫時間中位數", f"{median(secs):.0f} 秒" if secs else "—", help="目標：30–45 秒")
        c[2].metric("從 LINE（LIFF）開啟", f"{sum(r.source == 'liff' for r in recs) / len(recs):.0%}")
        c[3].metric("轉診 / 傷退", sum(records.is_serious(r) for r in recs))
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**受傷類型**")
            st.bar_chart(pd.Series([t for r in recs for t in r.injury_types]).value_counts().rename("次數"),
                         horizontal=True)
        with c2:
            st.markdown("**受傷部位**")
            st.bar_chart(pd.Series([b["part"] for r in recs for b in r.body_parts]).value_counts().rename("次數"),
                         horizontal=True)
        tot = records.consumable_totals(recs)
        st.caption("耗材總用量：" + "、".join(f"{label} {tot[k]} {unit}" for k, label, unit in CONSUMABLES))
