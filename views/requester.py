"""發案方：機構資料、刊登賽事、我的案件（挑選應徵者、錄用、評價、檢舉）。"""
from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, time, timedelta
from types import SimpleNamespace

import pandas as pd
import streamlit as st
from sqlalchemy import func, select

from core import auth, db, fmt, services, ui
from core.constants import (
    COUNTIES, EXP_MODES, HIGH_RISK_SPORTS, LICENSE_REQ, MAX_SESSIONS, ORG_TYPES, REPORT_REASONS,
    SALARY_TYPES, SCHOOLS, SPORTS, WEEKDAY_NAMES,
)
from core.db import Application, Case, CaseQuestion, Rating, Report, RequesterProfile, TrainerProfile, User, now
from core.matching import find_matches, rank_applicants, years_since
from core.privacy import full_trainer_view, mask_name, public_trainer_view
from core.validators import (
    consecutive_sessions, line_id_error, sessions_errors, weekly_sessions,
)
from views import nav


def _me():
    u = auth.current_user()
    with db.session() as s:
        org = s.get(RequesterProfile, u.id)
    return u, org


def _need_profile() -> bool:
    ui.notice("刊登賽事前，先填一下單位資料（只有錄用的防護員看得到你的聯絡方式）。")
    if st.button("填寫單位資料", type="primary"):
        nav.go("org")
    return True


# --- 機構資料 ---------------------------------------------------------------

def profile():
    u, o = _me()
    st.title("單位資料")
    with st.form("org_profile", border=True):
        c1, c2 = st.columns([3, 2])
        org_name = c1.text_input("單位名稱 *", value=o.org_name if o else "", placeholder="例如：○○市籃球委員會")
        org_type = c2.selectbox("單位類型 *", ORG_TYPES,
                                index=ORG_TYPES.index(o.org_type) if o and o.org_type in ORG_TYPES else None,
                                placeholder="請選擇")
        st.markdown("**聯絡人**　🔒 錄用防護員後，對方才看得到")
        c1, c2 = st.columns(2)
        contact = c1.text_input("聯絡人姓名 *", value=o.contact_name if o else u.display_name)
        phone = c2.text_input("聯絡電話 *", value=o.phone if o else "", placeholder="手機或市話")
        c1, c2 = st.columns(2)
        email = c1.text_input("Email *", value=o.email if o else "")
        line_id = c2.text_input("LINE ID *", value=(o.line_contact_id or "") if o else "",
                                help="錄用後讓防護員加你好友聯繫。可填個人或官方帳號 ID（@開頭）")
        consent = st.checkbox("我已閱讀並同意「隱私權聲明」 *", value=bool(o and o.consent_at))
        ok = st.form_submit_button("儲存", type="primary")
    if not ok:
        return
    errors = []
    if not org_name.strip():
        errors.append("請填寫單位名稱")
    if org_type is None:
        errors.append("請選擇單位類型")
    if not contact.strip():
        errors.append("請填寫聯絡人姓名")
    if len(re.sub(r"\D", "", phone)) < 8:
        errors.append("電話號碼看起來不完整")
    if "@" not in email:
        errors.append("Email 格式不正確")
    if err := line_id_error(line_id):
        errors.append(err)
    if not consent:
        errors.append("請勾選同意隱私權聲明")
    if errors:
        for m in errors:
            st.error(m)
        return
    with db.session() as s:
        cur = s.get(RequesterProfile, u.id)
        is_new = cur is None
        if is_new:
            cur = RequesterProfile(user_id=u.id)
            s.add(cur)
        cur.org_name, cur.org_type, cur.contact_name = org_name.strip(), org_type, contact.strip()
        cur.phone, cur.email, cur.line_contact_id = phone.strip(), email.strip(), line_id.strip()
        cur.consent_at = cur.consent_at or now()
        db.log(s, u.id, "org_saved", new=is_new)
    ui.flash("單位資料已儲存。")
    if is_new:
        nav.go("post")
    st.rerun()


# --- 刊登賽事 ---------------------------------------------------------------

def post_case():
    u, org = _me()
    st.title("刊登賽事")
    if org is None:
        _need_profile()
        return

    st.markdown("#### 賽事資訊")
    title = st.text_input("賽會名稱 *", key="pc_title", placeholder="例如：2026 中部盃青少年籃球錦標賽")
    sport = st.selectbox("運動項目 *", SPORTS, index=None, key="pc_sport", placeholder="輸入或選擇")
    st.markdown("#### 賽會時間")
    sessions = _schedule_builder()
    c1, c2 = st.columns([1, 2])
    county = c1.selectbox("賽會縣市 *", COUNTIES, index=None, key="pc_county", placeholder="請選擇")
    detail = c2.text_input("場館地址 *", key="pc_addr", placeholder="例如：○○體育館（○○路 100 號）")
    c1, c2, c3 = st.columns([1.2, 1.5, 1])
    sal_type = c1.radio("薪資方式 *", SALARY_TYPES, horizontal=True, key="pc_saltype", index=1)
    sal = c2.number_input("金額（新台幣）*", min_value=0, step=100, value=2500, key="pc_sal")
    headcount = c3.number_input("需求人數（每個時段）", min_value=1, max_value=20, value=1, key="pc_head",
                                help="多時段賽事是指「每個時段」需要幾位防護員")
    desc = st.text_area("工作內容（選填）", key="pc_desc", height=90,
                        placeholder="例如：駐點場邊、負責貼紮與急性傷害初步處置，需自備防護包")

    st.markdown("#### 防護員條件")
    high_risk = sport in HIGH_RISK_SPORTS
    if high_risk:
        ui.notice(f"⚠ <b>{ui.e(sport)} 屬於高風險接觸性運動</b>　系統會在通知卡片加註警告。"
                  "強烈建議要求防護員具備 EMT 等級的急救證照。", warn=True)
    lic = st.radio("證照要求", list(LICENSE_REQ), format_func=LICENSE_REQ.get, horizontal=True, key="pc_lic")
    emt = st.checkbox("必須具備 EMT-1 或 EMT-2" + ("（建議勾選）" if high_risk else ""),
                      value=high_risk, key=f"pc_emt_{high_risk}")
    c1, c2 = st.columns([2, 1])
    exp_mode = c1.radio("資歷要求", list(EXP_MODES), format_func=EXP_MODES.get, horizontal=True, key="pc_exp")
    exp_years = c2.number_input("X =（年）", min_value=0, max_value=40, value=2, key="pc_expy",
                                disabled=exp_mode == "none")
    school = st.selectbox("指定畢業學校（選填）", SCHOOLS, index=None, key="pc_school",
                          placeholder="不指定", help="想找同校學長姐或學弟妹時使用")

    # 即時預估符合人數
    preview = None
    if county:
        preview = Case(requester_id=u.id, location_county=county, license_req=lic, require_emt=emt,
                       exp_mode=exp_mode, exp_years=int(exp_years), required_school=school)
        with db.session() as s:
            n = len(find_matches(s, preview))
        if n:
            ui.notice(f"目前有 <b>{n} 位</b>審核通過的防護員符合條件，發布後會用 LINE 個別通知他們。")
        else:
            ui.notice("目前沒有防護員符合這些條件。案件仍會刊登，之後符合的人可以在職缺列表看到；"
                      "也可以考慮放寬證照、年資或學校條件。", warn=True)

    st.write("")
    if st.button("發布賽事", type="primary", width="stretch"):
        errors = []
        if not title.strip():
            errors.append("請填寫賽會名稱")
        if not sport:
            errors.append("請選擇運動項目")
        if not county:
            errors.append("請選擇賽會縣市")
        if not detail.strip():
            errors.append("請填寫場館地址")
        if sal <= 0:
            errors.append("請填寫薪資金額")
        errors += sessions_errors(sessions)
        if errors:
            for m in errors:
                st.error(m)
            return
        with db.session() as s:
            c, n = services.publish_case(
                s, s.get(User, u.id), sessions=sessions, title=title.strip(), sport_type=sport,
                location_county=county,
                location_detail=detail.strip(), salary_type=sal_type, salary_amount=int(sal),
                headcount=int(headcount), description=desc.strip(), license_req=lic, require_emt=emt,
                exp_mode=exp_mode, exp_years=int(exp_years), required_school=school,
            )
        for k in [k for k in st.session_state if str(k).startswith("pc_")]:
            del st.session_state[k]
        ui.flash(f"已發布「{c.title}」，並用 LINE 個別通知了 {n} 位符合條件的防護員。")
        nav.go("my-cases")


# --- 賽會時段產生器 -----------------------------------------------------------

MODES = ["連續日期", "每週固定", "自訂時段"]
MODE_HELP = {
    "連續日期": "單日賽，或連續好幾天（例如 11/14～11/16 每天都有）",
    "每週固定": "每週固定幾天，連續好幾週（例如每個週六、日，連續兩週）",
    "自訂時段": "日期不規則、或每天時間不一樣，直接在表格裡一筆一筆填",
}


def _schedule_builder() -> list[tuple]:
    """三種方式快速產生時段，產生後都可以在表格裡再修改。回傳 [(date, time, time), ...]。"""
    today = date.today()
    first = today + timedelta(days=7)
    mode = st.segmented_control("時間安排方式", MODES, default=MODES[0], key="pc_mode") or MODES[0]
    st.caption(MODE_HELP[mode])

    if mode == "連續日期":
        c1, c2, c3 = st.columns([2, 1, 1])
        dates = c1.date_input("日期 *（多日賽事請在日曆上點第一天、再點最後一天）", value=(first, first),
                              min_value=today, max_value=today + timedelta(days=365),
                              key="pc_date", format="YYYY/MM/DD")
        t1 = c2.time_input("每日開始", value=time(8, 0), key="pc_t1", step=1800)
        t2 = c3.time_input("每日結束", value=time(17, 0), key="pc_t2", step=1800)
        dates = tuple(dates) if isinstance(dates, (tuple, list)) else (dates,)
        # 只點一下（還沒選結束日）就當作單日賽
        gen = consecutive_sessions(dates[0] if dates else None, dates[1] if len(dates) > 1 else None, t1, t2)
    elif mode == "每週固定":
        c1, c2 = st.columns([2, 1])
        start = c1.date_input("從哪一天開始 *", value=first, min_value=today,
                              max_value=today + timedelta(days=365), key="pc_wstart", format="YYYY/MM/DD")
        weeks = c2.number_input("連續幾週 *", min_value=1, max_value=12, value=2, key="pc_weeks")
        wds = st.pills("每週哪幾天 *", list(range(7)), selection_mode="multi", default=[5, 6],
                       format_func=lambda i: WEEKDAY_NAMES[i], key="pc_wdays") or []
        c1, c2, _ = st.columns([1, 1, 2])
        t1 = c1.time_input("開始", value=time(8, 0), key="pc_wt1", step=1800)
        t2 = c2.time_input("結束", value=time(17, 0), key="pc_wt2", step=1800)
        gen = weekly_sessions(start, int(weeks), list(wds), t1, t2)
        if not wds:
            st.warning("請至少選一天")
    else:
        gen = [(first, time(8, 0), time(17, 0))]

    st.markdown("**時段明細**　可以直接改某一天的時間、勾選左側刪除某一天，或在表格最下面新增時段。")
    df = pd.DataFrame(gen, columns=["日期", "開始", "結束"])
    # 上面的條件一改，表格就重新產生（換一個 key）
    sig = hashlib.md5(repr((mode, gen)).encode()).hexdigest()[:10]
    edited = st.data_editor(
        df, key=f"pc_sessions_{sig}", num_rows="dynamic", hide_index=True, width="stretch",
        column_config={
            "日期": st.column_config.DateColumn("日期", format="YYYY/MM/DD", min_value=today, required=True),
            "開始": st.column_config.TimeColumn("開始", format="HH:mm", step=1800, required=True),
            "結束": st.column_config.TimeColumn("結束", format="HH:mm", step=1800, required=True),
        },
    )
    sessions = _rows_to_sessions(edited)
    if sessions:
        preview = SimpleNamespace(schedule=[(d, f"{a:%H:%M}", f"{b:%H:%M}") for d, a, b in sorted(sessions)])
        st.caption(f"📅 {fmt.schedule_summary(preview)}")
    for err in sessions_errors(sessions):
        st.warning(err)
    return sessions


def _as_date(v):
    if v is None or pd.isna(v):
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return pd.Timestamp(v).date()


def _as_time(v):
    if v is None or (not isinstance(v, time) and pd.isna(v)):
        return None
    if isinstance(v, time):
        return v.replace(second=0, microsecond=0)
    if isinstance(v, datetime):
        return v.time().replace(second=0, microsecond=0)
    return datetime.strptime(str(v)[:5], "%H:%M").time()


def _rows_to_sessions(df) -> list[tuple]:
    out = []
    for _, r in df.iterrows():
        d, a, b = _as_date(r["日期"]), _as_time(r["開始"]), _as_time(r["結束"])
        if d and a and b:
            out.append((d, a, b))
    return sorted(set(out))[: MAX_SESSIONS + 1]


# --- 我的案件 ---------------------------------------------------------------

def my_cases():
    u, org = _me()
    st.title("我的案件")
    if org is None:
        _need_profile()
        return
    with db.session() as s:
        cases = s.execute(select(Case).where(Case.requester_id == u.id)
                          .order_by(Case.status != "open", Case.event_date.desc())).scalars().all()
        for c in cases:
            _ = c.applications
        pending_q = {cid: n for cid, n in s.execute(
            select(CaseQuestion.case_id, func.count(CaseQuestion.id))
            .join(Case, Case.id == CaseQuestion.case_id)
            .where(Case.requester_id == u.id, CaseQuestion.answer.is_(None))
            .group_by(CaseQuestion.case_id))}
    if not cases:
        ui.empty("還沒有刊登任何賽事。")
        if st.button("刊登第一場賽事", type="primary"):
            nav.go("post")
        return

    total_q = sum(pending_q.values())
    if total_q:
        ui.notice(f"💬 有 <b>{total_q} 則</b>防護員提問等你回覆。")
    tab_open, tab_done = st.tabs([f"招募中（{sum(c.status == 'open' for c in cases)}）", "已結束"])
    for c in cases:
        with (tab_open if c.status == "open" else tab_done):
            _case_block(u, c, pending_q.get(c.id, 0))


def _case_block(u: User, c: Case, pending_q: int = 0):
    apps = [a for a in c.applications if a.status != "withdrawn"]
    hired = sum(a.status == "hired" for a in apps)
    with st.container(border=True):
        ui.case_card(c)
        if len(c.schedule) > 1:
            st.caption(f"已通知 {c.notified_count} 位　應徵 {len(apps)} 位　已錄用 {hired} 位（每個時段需 {c.headcount} 人）")
        else:
            st.caption(f"已通知 {c.notified_count} 位　應徵 {len(apps)} 位　錄用 {hired} / {c.headcount} 位")
        label = f"查看應徵者（{len(apps)}）" + ("與各時段錄用狀況" if len(c.sessions) > 1 else "")
        with st.expander(label, expanded=c.status == "open" and bool(apps)):
            _applicants(u, c)
        _case_records(c)
        q_label = f"防護員提問（{pending_q} 則待回覆）" if pending_q else "防護員提問"
        with st.expander(q_label, expanded=pending_q > 0):
            _questions(u, c)
        if c.status == "open":
            b1, _ = st.columns([1, 3])
            if hired:
                if b1.button("結束招募", key=f"close_{c.id}", help="人數未滿但不再招募"):
                    with db.session() as s:
                        services.close_case(s, s.get(Case, c.id), s.get(User, u.id))
                    st.rerun()
            elif b1.button("取消案件", key=f"cancel_{c.id}"):
                with db.session() as s:
                    services.cancel_case(s, s.get(Case, c.id), s.get(User, u.id))
                st.rerun()


def _applicants(u: User, c: Case):
    with db.session() as s:
        ranked = rank_applicants(s, c)
        fill = services.session_fill(s, c)
        ratings = {r.trainer_id: r for r in s.execute(select(Rating).where(Rating.case_id == c.id)).scalars()}
        reported = {r.trainer_id for r in s.execute(select(Report).where(Report.case_id == c.id)).scalars()}
    multi = len(c.sessions) > 1
    active = [r for r in ranked if r["application"].status in ("applied", "hired")]
    if multi:
        ui.fill_grid(c, fill, {x.id: sum(x.id in r["available_ids"] for r in active) for x in c.sessions})
    if not ranked:
        st.caption("還沒有人應徵。符合條件的防護員都已收到 LINE 通知。")
        return
    ctx = dict(u=u, c=c, fill=fill, open_ids={x.id for x in c.sessions if fill.get(x.id, 0) < c.headcount},
               ratings=ratings, reported=reported)
    if not multi:
        st.caption("排序：條件完全吻合 → 無缺席紀錄且評價高 → 應徵時間早。錄用前只顯示去識別化資料。")
        _applicant_list(ranked, ctx)
        return
    by_person, by_slot = st.tabs(["依應徵者", "依時段挑人"])
    with by_person:
        st.caption("排序：條件完全吻合 → 無缺席紀錄且評價高 → 應徵時間早。每位可以只錄用部分時段。")
        _applicant_list(ranked, ctx)
    with by_slot:
        _by_session(ranked, ctx)


def _tags(r: dict, pub: dict, n_sessions: int) -> list[str]:
    tags = [ui.tag("條件 100% 吻合", "ok") if r["full_match"]
            else ui.tag(f"吻合 {round(r['ratio'] * 100)}%", "grey")]
    if pub["has_at"]:
        tags.append(ui.tag("AT"))
    if pub["has_pt"]:
        tags.append(ui.tag("PT"))
    tags += [ui.tag(x) for x in pub["first_aid"]]
    if r["avg_rating"] is not None:
        tags.append(ui.tag(f"★ {r['avg_rating']}（{r['rating_count']} 筆）", "orange"))
    else:
        tags.append(ui.tag("尚無評價", "grey"))
    if r["no_show"]:
        tags.append(ui.tag(f"缺席紀錄 {r['no_show']} 次", "warn"))
    if n_sessions > 1:
        tags.append(ui.tag(f"可出席 {len(r['available_ids'])} / {n_sessions} 時段"))
    return tags


def _applicant_list(ranked: list[dict], ctx: dict):
    c = ctx["c"]
    multi = len(c.sessions) > 1
    labels = {x.id: fmt.slot_label(x) for x in c.sessions}
    for i, r in enumerate(ranked, 1):
        a, prof = r["application"], r["profile"]
        unlocked = a.status == "hired"
        pub = public_trainer_view(prof, years_since(prof.graduation_year))
        with st.container(border=True):
            tags = _tags(r, pub, len(c.sessions))
            label, kind = ui.APP_STATUS.get(a.status, (a.status, "grey"))
            if a.status != "applied":
                tags.append(ui.tag(label, kind))
            name = prof.name if unlocked else pub["masked_name"]
            yrs = pub["years_since_grad"]
            yrs_txt = f"畢業 {yrs} 年" if yrs > 0 else ("今年畢業" if yrs == 0 else "在學中")
            st.markdown(
                f"**{i}. {ui.e(name)}**　{ui.e(pub['gender'])}　{ui.e(pub['school'])}　{yrs_txt}<br>"
                f"{''.join(tags)}", unsafe_allow_html=True)
            if pub["experience"]:
                st.caption(pub["experience"])
            st.caption(f"應徵時間 {a.applied_at:%m/%d %H:%M}")
            candidates = [x.id for x in c.sessions
                          if x.id in r["available_ids"] - r["hired_ids"] and x.id in ctx["open_ids"]]
            can_hire = c.status == "open" and a.status in ("applied", "hired") and bool(candidates)
            # 下方的「要錄用的時段」按鈕已列出可錄用時段；只有出現「已錄用／已滿」時才另外列一排狀態
            if multi and (not can_hire or set(candidates) != r["available_ids"]):
                chips = []
                for x in c.sessions:
                    if x.id in r["hired_ids"]:
                        chips.append(ui.tag(f"✓ {labels[x.id]}", "ok"))
                    elif x.id in r["available_ids"]:
                        chips.append(ui.tag(labels[x.id]) if x.id in ctx["open_ids"]
                                     else ui.tag(f"{labels[x.id]}（已滿）", "grey"))
                st.markdown("**可出席**　" + "".join(chips), unsafe_allow_html=True)

            if unlocked:
                full = full_trainer_view(prof)
                st.markdown(f"""<div class="contact"><b>{ui.e(full['name'])}</b>　電話：{ui.e(full['phone'])}
　Email：{ui.e(full['email'])}　LINE ID：{ui.e(full['line_contact_id'] or '—')}</div>""",
                            unsafe_allow_html=True)
            elif a.status != "applied":
                st.markdown('<span class="locked">🔒 聯絡方式在錄用後才會顯示</span>', unsafe_allow_html=True)

            if can_hire:
                _hire_control(a, name, candidates, labels, multi, "list")

            if unlocked:
                _after_hire(ctx["u"], c, prof.user_id, ctx["ratings"].get(prof.user_id),
                            prof.user_id in ctx["reported"])


def _hire_control(a, name: str, candidates: list[int], labels: dict, multi: bool, where: str):
    """錄用按鈕 → 頁內確認。where 用來區分同一人出現在兩個分頁時的元件。"""
    pending = st.session_state.get("confirm_hire")
    if pending and pending["app"] == a.id and pending["where"] == where:
        _confirm_hire(a.case_id, a.id, name, pending["sessions"], labels, where)
        return
    if multi and where == "list":
        picked = st.pills("要錄用的時段", candidates, selection_mode="multi", default=candidates,
                          format_func=labels.get, key=f"pick_{a.id}") or []
        verb = "加錄所選時段" if a.status == "hired" else "錄用所選時段"
        btn = f"{verb}（{len(picked)}）"
    else:
        picked = candidates
        btn = "確認錄用" if not multi else "錄用此時段"
    if st.button(btn, key=f"hire_{where}_{a.id}_{'-'.join(map(str, candidates))}", type="primary",
                 disabled=not picked):
        st.session_state.confirm_hire = {"app": a.id, "sessions": list(picked), "where": where}
        st.rerun()


def _by_session(ranked: list[dict], ctx: dict):
    c, fill = ctx["c"], ctx["fill"]
    labels = {x.id: fmt.slot_label(x) for x in c.sessions}
    sid = st.selectbox(
        "選擇時段", [x.id for x in c.sessions], key=f"slot_pick_{c.id}",
        format_func=lambda i: f"{labels[i]}　已錄用 {fill.get(i, 0)}/{c.headcount}")
    people = [r for r in ranked if sid in r["available_ids"] and r["application"].status in ("applied", "hired")]
    is_open = sid in ctx["open_ids"]
    if not is_open:
        st.success("這個時段已經錄滿。")
    if not people:
        st.caption("還沒有人勾選可以出席這個時段。")
        return
    st.caption(f"{len(people)} 位可以出席，排序規則同上。")
    for i, r in enumerate(people, 1):
        a, prof = r["application"], r["profile"]
        pub = public_trainer_view(prof, years_since(prof.graduation_year))
        name = prof.name if a.status == "hired" else pub["masked_name"]
        with st.container(border=True):
            tags = _tags(r, pub, len(c.sessions))
            if sid in r["hired_ids"]:
                tags.append(ui.tag("✓ 此時段已錄用", "ok"))
            st.markdown(f"**{i}. {ui.e(name)}**　{ui.e(pub['school'])}<br>{''.join(tags)}", unsafe_allow_html=True)
            if c.status == "open" and is_open and sid not in r["hired_ids"]:
                _hire_control(a, name, [sid], labels, True, f"slot{sid}")


def _case_records(c: Case):
    """這場賽事的防護紀錄（防護員用防護紀錄表送出的）。"""
    from core import records
    from core.db import InjuryRecord
    with db.session() as s:
        recs = list(s.scalars(select(InjuryRecord).where(
            InjuryRecord.case_id == c.id, InjuryRecord.is_deleted.is_(False)).order_by(InjuryRecord.created_at)))
        names = {p.user_id: p.name for p in s.scalars(
            select(TrainerProfile).where(TrainerProfile.user_id.in_({r.trainer_id for r in recs})))}
    if not recs:
        return
    serious = sum(records.is_serious(r) for r in recs)
    label = f"防護紀錄（{len(recs)} 筆" + (f"，🚨 {serious} 筆轉診或傷退" if serious else "") + "）"
    with st.expander(label):
        tot = records.consumable_totals(recs)
        from core.constants import CONSUMABLES
        st.caption("耗材合計：" + "、".join(f"{lb} {tot[k]} {un}" for k, lb, un in CONSUMABLES)
                   + "。可作為耗材請款與保險申請的依據。")
        df = pd.DataFrame(records.to_rows(recs, names))
        st.download_button("下載 CSV", df.to_csv(index=False).encode("utf-8-sig"),
                           file_name=f"防護紀錄_No{c.id:03d}.csv", mime="text/csv", key=f"rec_csv_{c.id}")
        for r in recs:
            flag = ui.tag("🚨 " + "、".join(t for t in r.treatments if t in {"建議轉診", "傷退"}), "warn") \
                if records.is_serious(r) else ""
            st.markdown(
                f"**{r.created_at:%m/%d %H:%M}**　{ui.e(' '.join(x for x in (r.team, r.athlete) if x))}　{flag}<br>"
                f"<span class='locked'>{ui.e(records.parts_text(r))}｜{ui.e(records.types_text(r))}｜"
                f"{ui.e('、'.join(r.treatments))}{'｜' + ui.e(r.note) if r.note else ''}</span>",
                unsafe_allow_html=True)


def _questions(u: User, c: Case):
    with db.session() as s:
        qs = list(s.scalars(select(CaseQuestion).where(CaseQuestion.case_id == c.id)
                            .order_by(CaseQuestion.answer.isnot(None), CaseQuestion.created_at)))
        names = {p.user_id: p.name for p in s.scalars(
            select(TrainerProfile).where(TrainerProfile.user_id.in_([q.trainer_id for q in qs])))}
    if not qs:
        st.caption("還沒有人提問。")
        return
    st.caption("勾選「公開」的問答，其他看這個案件的防護員也看得到（不顯示發問者），可以減少重複問題。")
    for q in qs:
        with st.container(border=True):
            st.markdown(f"**{ui.e(mask_name(names.get(q.trainer_id, '')))} 問**　{ui.e(q.question)}<br>"
                        f"<span class='locked'>{q.created_at:%m/%d %H:%M}</span>", unsafe_allow_html=True)
            editing = st.session_state.get("edit_answer") == q.id
            if q.answer and not editing:
                pub = ui.tag("公開", "ok") if q.is_public else ui.tag("僅發問者可見", "grey")
                st.markdown(f"**你的回覆**　{ui.e(q.answer)}　{pub}", unsafe_allow_html=True)
                if c.status == "open" and st.button("修改回覆", key=f"edit_{q.id}", type="tertiary"):
                    st.session_state.edit_answer = q.id
                    st.rerun()
                continue
            if c.status != "open" and not q.answer:
                st.caption("案件已結束，無法回覆。")
                continue
            with st.form(f"answer_{q.id}", border=False):
                text = st.text_area("回覆", value=q.answer or "", height=80, label_visibility="collapsed",
                                    placeholder="輸入回覆…")
                public = st.checkbox("公開這則問答給其他防護員看", value=q.is_public if q.answer else True)
                if st.form_submit_button("送出回覆", type="primary"):
                    try:
                        with db.session() as s:
                            services.answer_question(s, s.get(CaseQuestion, q.id), s.get(User, u.id), text, public)
                        st.session_state.pop("edit_answer", None)
                        ui.flash("已送出回覆。")
                        st.rerun()
                    except services.ActionError as err:
                        st.error(str(err))


def _confirm_hire(case_id: int, app_id: int, name: str, session_ids: list[int], labels: dict, where: str):
    with st.container(border=True):
        st.markdown(f"確定要錄用 **{ui.e(name)}** 嗎？", unsafe_allow_html=True)
        if len(labels) > 1:
            st.markdown("錄用時段：" + "".join(ui.tag(labels[i], "ok") for i in session_ids), unsafe_allow_html=True)
        st.caption("錄用後，雙方會立刻看到彼此的姓名與電話，並收到 LINE 通知。所有時段都錄滿後案件會自動關閉。")
        c1, c2, _ = st.columns([1, 1, 2])
        if c1.button("確定錄用", key=f"yes_{where}_{app_id}", type="primary", width="stretch"):
            st.session_state.pop("confirm_hire", None)
            u = auth.current_user()
            try:
                with db.session() as s:
                    closed = services.hire(s, s.get(Case, case_id), s.get(Application, app_id),
                                           s.get(User, u.id), session_ids)
                ui.flash("已錄用！聯絡方式已解鎖" + ("，所有時段都已錄滿，案件已自動關閉。" if closed else "。"))
            except services.ActionError as err:
                ui.flash(str(err), "error")
            st.rerun()
        if c2.button("再想想", key=f"no_{where}_{app_id}", width="stretch"):
            st.session_state.pop("confirm_hire", None)
            st.rerun()


def _after_hire(u: User, c: Case, trainer_id: int, rating: Rating | None, reported: bool):
    with st.expander("賽後評價與檢舉"):
        with st.form(f"rate_{c.id}_{trainer_id}", border=False):
            score = st.select_slider("服務評分", options=[1, 2, 3, 4, 5], value=rating.score if rating else 5,
                                     format_func=lambda x: "★" * x)
            comment = st.text_input("一句話評語（選填）", value=rating.comment if rating else "")
            if st.form_submit_button("送出評價"):
                with db.session() as s:
                    services.rate(s, s.get(Case, c.id), trainer_id, s.get(User, u.id), score, comment)
                ui.flash("評價已送出，謝謝！")
                st.rerun()
        st.divider()
        if reported:
            st.caption("你已檢舉過這位防護員，管理員查證後會處理。")
            return
        with st.form(f"report_{c.id}_{trainer_id}", border=False):
            reason = st.selectbox("檢舉原因", REPORT_REASONS)
            detail = st.text_area("發生了什麼事？", height=80)
            if st.form_submit_button("送出檢舉"):
                with db.session() as s:
                    services.report(s, s.get(Case, c.id), trainer_id, s.get(User, u.id), reason, detail)
                ui.flash("已送出檢舉，管理員查證後會處理。", "info")
                st.rerun()
