"""防護員端：賽會職缺、案件詳情、我的應徵、我的履歷。"""
from __future__ import annotations

from datetime import date

import streamlit as st
from sqlalchemy import func, select

from core import auth, db, fmt, services, ui
from core.constants import (
    COUNTIES, COUNTY_ALL, FIRST_AID_LICENSES, GENDERS, LICENSE_FIELDS, SCHOOL_OTHER, SCHOOLS, SPORTS,
)
from core.db import Application, Case, RequesterProfile, TrainerProfile, User, now
from core.validators import (
    MOBILE_RE, collapse_counties, expand_counties, license_no_error, line_id_error,
)
from core.matching import criteria_checks, current_year, is_full_match
from views import nav


def _me():
    u = auth.current_user()
    with db.session() as s:
        prof = s.get(TrainerProfile, u.id)
    return u, prof


# --- 賽會職缺 ---------------------------------------------------------------

def jobs():
    u, prof = _me()
    if st.session_state.get("view_case_id"):
        return case_detail(u, prof, st.session_state.view_case_id)

    st.title("賽會職缺")
    if prof is None:
        ui.notice("先填好履歷與證號，審核通過後就能應徵，也會開始收到 LINE 案件通知。")
        if st.button("填寫我的履歷", type="primary"):
            nav.go("profile")
        return
    _review_banner(prof)

    c1, c2, c3 = st.columns([2, 2, 1.4], vertical_alignment="bottom")
    mine = prof.service_counties if set(prof.service_counties or []) < set(COUNTIES) else []
    counties = c1.multiselect("地區", COUNTIES, default=mine, placeholder="全部縣市")
    sports = c2.multiselect("運動項目", SPORTS, placeholder="全部項目")
    only_match = c3.toggle("只看符合我的", value=True)

    with db.session() as s:
        # 多日賽事：最後一天還沒過就繼續顯示
        q = select(Case).where(Case.status == "open",
                               func.coalesce(Case.event_end_date, Case.event_date) >= date.today())
        if counties:
            q = q.where(Case.location_county.in_(counties))
        if sports:
            q = q.where(Case.sport_type.in_(sports))
        cases = s.execute(q.order_by(Case.event_date)).scalars().all()
        applied = {a.case_id: a.status for a in s.execute(
            select(Application).where(Application.trainer_id == u.id)).scalars()}

    shown = 0
    for c in cases:
        if c.requester_id == u.id:
            continue
        full = is_full_match(prof, c)
        if only_match and not full:
            continue
        extra = [ui.tag("符合你的條件", "ok") if full else ui.tag("部分條件不符", "grey")]
        if c.id in applied and applied[c.id] != "withdrawn":
            extra.append(ui.tag("已應徵", "orange"))
        with st.container(border=True):
            ui.case_card(c, extra, show_status=False)
            if st.button("查看詳情", key=f"open_{c.id}"):
                st.session_state.view_case_id = c.id
                st.rerun()
        shown += 1
    if shown == 0:
        ui.empty("目前沒有符合篩選的招募中賽事。新案件一出現，符合條件的話我們會用 LINE 通知你。")


def case_detail(u: User, prof: TrainerProfile | None, case_id: int):
    if st.button("← 回職缺列表", type="tertiary"):
        st.session_state.pop("view_case_id", None)
        st.rerun()

    with db.session() as s:
        c = s.get(Case, case_id)
        if c is None:
            st.error("找不到這個案件，可能已被刪除。")
            return
        app = s.scalar(select(Application).where(
            Application.case_id == c.id, Application.trainer_id == u.id))
        org = s.get(RequesterProfile, c.requester_id)

    # 已結案、且不是被錄用的人 → 統一顯示額滿訊息
    if c.status != "open" and not (app and app.status == "hired"):
        st.title(c.title)
        ui.notice("<b>本賽事已招募額滿，感謝您的參與。</b><br>之後有符合條件的賽會，我們會再用 LINE 通知你。")
        return

    st.title(c.title)
    with st.container(border=True):
        ui.case_card(c)
    if c.is_high_risk:
        ui.notice("⚠ <b>高風險接觸性運動</b>　這場賽事受傷機率較高，請評估自己的急救能力與裝備再應徵。",
                  warn=True)

    st.markdown("#### 賽事資訊")
    st.markdown(f"**主辦單位**　{ui.e(org.org_name if org else '—')}（{ui.e(org.org_type if org else '')}）  \n"
                f"**賽會時間**　{ui.e(fmt.schedule_summary(c))}  \n"
                f"**場館地址**　{ui.e(c.location_detail)}  \n"
                f"**需求人數**　{c.headcount} 人", unsafe_allow_html=True)
    if len(c.schedule) > 1:
        ui.schedule_table(c)
    if c.description:
        st.markdown(f"**工作內容**\n\n{c.description}")

    if prof:
        st.markdown("#### 你的條件比對")
        for label, ok in criteria_checks(prof, c):
            st.markdown(f"{'✅' if ok else '❌'} {label}")

    st.divider()
    _apply_section(u, prof, c, app, org)
    st.divider()
    _qa_section(u, prof, c)


def _apply_section(u: User, prof: TrainerProfile | None, c: Case, app: Application | None, org):
    multi = len(c.sessions) > 1
    with db.session() as s:
        fill = services.session_fill(s, c)
    open_ids = {x.id for x in c.sessions if fill.get(x.id, 0) < c.headcount}

    if app and app.status == "hired":
        st.success("🎉 你已被錄用！請主動聯繫主辦方確認細節。")
        if multi:
            _slot_status(c, app, open_ids)
        _requester_contact(org)
        if multi and c.status == "open" and (app.available_ids - app.hired_ids or open_ids - app.available_ids):
            _edit_availability(u, c, app, open_ids)
        return
    if prof is None or prof.review_status != "approved":
        st.info("履歷審核通過後才能應徵。")
        return
    if app and app.status == "applied":
        st.markdown(ui.tag("已應徵，等待主辦方挑選", "orange"), unsafe_allow_html=True)
        st.caption(f"應徵時間：{app.applied_at:%Y/%m/%d %H:%M}")
        if multi:
            _slot_status(c, app, open_ids)
            _edit_availability(u, c, app, open_ids)
        if st.button("取消應徵"):
            with db.session() as s:
                services.withdraw(s, s.get(Application, app.id), s.get(User, u.id))
            st.rerun()
        return

    chosen = None
    if multi:
        st.markdown("**勾選你可以出席的時段**　主辦方會依時段錄用，不用每個時段都能到。")
        opts = [x.id for x in c.sessions if x.id in open_ids]
        labels = {x.id: fmt.slot_label(x) for x in c.sessions}
        chosen = st.pills("可出席時段", opts, selection_mode="multi", default=opts,
                          format_func=labels.get, key=f"avail_{c.id}", label_visibility="collapsed") or []
        full = [labels[x.id] for x in c.sessions if x.id not in open_ids]
        if full:
            st.caption("已額滿的時段：" + "、".join(full))
        label = f"我要應徵（{len(chosen)} / {len(c.sessions)} 個時段）"
    else:
        label = "我有興趣，我要應徵"
    if st.button(label, type="primary", width="stretch", disabled=multi and not chosen):
        try:
            with db.session() as s:
                services.apply(s, s.get(Case, c.id), s.get(User, u.id), chosen)
            ui.flash("已送出應徵！主辦方挑選後會用 LINE 通知你。")
            st.rerun()
        except services.ActionError as err:
            st.error(str(err))
    st.caption("應徵後主辦方只會看到你的去識別化資料（例如王O明、學校、年資、證照），錄用後才會看到電話。")


def _slot_status(c: Case, app: Application, open_ids: set[int]):
    """我的各時段狀態：已錄用 / 等待挑選 / 已由他人補滿。"""
    chips = []
    for x in c.sessions:
        if x.id in app.hired_ids:
            chips.append(ui.tag(f"✓ {fmt.slot_label(x)} 已錄用", "ok"))
        elif x.id in app.available_ids:
            chips.append(ui.tag(f"{fmt.slot_label(x)} 等待挑選", "orange") if x.id in open_ids
                         else ui.tag(f"{fmt.slot_label(x)} 已由他人補滿", "grey"))
    st.markdown("**你的時段**<br>" + "".join(chips), unsafe_allow_html=True)


def _edit_availability(u: User, c: Case, app: Application, open_ids: set[int]):
    with st.expander("修改可出席時段"):
        labels = {x.id: fmt.slot_label(x) for x in c.sessions}
        opts = [x.id for x in c.sessions if x.id in open_ids or x.id in app.available_ids]
        picked = st.pills("可出席時段", opts, selection_mode="multi",
                          default=[i for i in opts if i in app.available_ids],
                          format_func=labels.get, key=f"edit_avail_{app.id}", label_visibility="collapsed") or []
        if app.hired_ids:
            st.caption("已錄用的時段不能取消，如有變動請直接聯繫主辦方。")
        if st.button("儲存時段", key=f"save_avail_{app.id}"):
            try:
                with db.session() as s:
                    services.update_availability(s, s.get(Application, app.id), s.get(User, u.id), picked)
                ui.flash("已更新可出席時段。")
                st.rerun()
            except services.ActionError as err:
                st.error(str(err))


def _qa_section(u: User, prof: TrainerProfile | None, c: Case):
    st.markdown("#### 我想發問")
    st.caption("對工作內容、交通、裝備有疑問可以先問主辦方，不用先應徵。"
               "提問不會顯示你的姓名；主辦方回覆後會用 LINE 通知你。")
    with db.session() as s:
        qs = services.visible_questions(s, c, u.id)
    for q in qs:
        mine = q.trainer_id == u.id
        with st.container(border=True):
            who = "你的提問" if mine else "其他防護員的提問"
            st.markdown(f"**問**　{ui.e(q.question)}<br><span class='locked'>{who}　{q.created_at:%m/%d %H:%M}</span>",
                        unsafe_allow_html=True)
            if q.answer:
                st.markdown(f"**主辦方回覆**　{ui.e(q.answer)}", unsafe_allow_html=True)
            else:
                st.markdown(ui.tag("等待主辦方回覆", "orange"), unsafe_allow_html=True)
    if c.status != "open":
        return
    if prof is None:
        st.caption("填寫履歷後就可以提問。")
        return
    with st.form(f"ask_{c.id}", clear_on_submit=True, border=False):
        text = st.text_area("你的問題", max_chars=services.QUESTION_MAX_LEN, height=90,
                            placeholder="例如：現場有提供冰塊和帳篷嗎？可以只接其中一天嗎？")
        st.caption("為了保護雙方，問題中的電話、Email、LINE ID 會被自動隱藏，錄用後才交換聯絡方式。")
        if st.form_submit_button("送出問題"):
            try:
                with db.session() as s:
                    services.ask_question(s, s.get(Case, c.id), s.get(User, u.id), text)
                ui.flash("問題已送出，主辦方回覆後會用 LINE 通知你。")
                st.rerun()
            except services.ActionError as err:
                st.error(str(err))


def _requester_contact(org: RequesterProfile | None):
    if not org:
        return
    st.markdown(f"""<div class="contact"><b>{ui.e(org.org_name)}</b><br>
聯絡人：{ui.e(org.contact_name)}<br>電話：{ui.e(org.phone)}<br>Email：{ui.e(org.email)}
<br>LINE ID：{ui.e(org.line_contact_id or '—')}</div>""",
                unsafe_allow_html=True)


# --- 我的應徵 ---------------------------------------------------------------

def my_applications():
    u = auth.current_user()
    st.title("我的應徵")
    with db.session() as s:
        rows = s.execute(
            select(Application, Case).join(Case, Case.id == Application.case_id)
            .where(Application.trainer_id == u.id).order_by(Application.applied_at.desc())
        ).all()
        orgs = {o.user_id: o for o in s.execute(select(RequesterProfile)).scalars()} if rows else {}
    if not rows:
        ui.empty("還沒有應徵紀錄。到「賽會職缺」看看有沒有適合的賽事。")
        return
    for a, c in rows:
        label, kind = ui.APP_STATUS.get(a.status, (a.status, "grey"))
        with st.container(border=True):
            ui.case_card(c, [ui.tag(label, kind)], show_status=False)
            if len(c.sessions) > 1 and a.status in ("applied", "hired"):
                done = [fmt.slot_label(x) for x in c.sessions if x.id in a.hired_ids]
                wait = [fmt.slot_label(x) for x in c.sessions if x.id in a.available_ids - a.hired_ids]
                if done:
                    st.markdown("**錄用時段**　" + "、".join(done))
                if wait and c.status == "open":
                    st.caption("等待挑選：" + "、".join(wait))
            if a.status == "hired":
                _requester_contact(orgs.get(c.requester_id))
            if a.status in ("applied", "hired") and c.status == "open":
                if st.button("查看案件", key=f"view_{a.id}"):
                    st.session_state.view_case_id = c.id
                    nav.go("jobs")


# --- 我的履歷 ---------------------------------------------------------------

def _review_banner(prof: TrainerProfile):
    if prof.review_status == "pending":
        ui.notice("📋 <b>資料審核中</b>　管理員確認證照後，你才會開始收到 LINE 案件通知並可以應徵。")
    elif prof.review_status == "rejected":
        ui.notice(f"<b>需要補件</b>　{ui.e(prof.review_note or '請確認證號是否正確')}。修改後按「儲存並送審」。",
                  warn=True)


def _holds(p: TrainerProfile, code: str) -> bool:
    if code == "AT":
        return bool(p.has_at_license)
    if code == "PT":
        return bool(p.has_pt_license)
    return code in (p.first_aid_licenses or [])


def profile():
    u, prof = _me()
    st.title("我的履歷")
    if prof:
        label, kind = ui.REVIEW_STATUS.get(prof.review_status, ("", "grey"))
        st.markdown(f"審核狀態　{ui.tag(label, kind)}", unsafe_allow_html=True)
        _review_banner(prof)
    else:
        st.write("填一次就好。審核通過後，符合你條件的賽會會直接用 LINE 通知你。")
    if prof:
        missing = [code for field, code, _l, _h in LICENSE_FIELDS
                   if _holds(prof, code) and not getattr(prof, field)]
        if missing:
            ui.notice("平台改為<b>所有證照都要填證號</b>。你先前登錄的 " + "、".join(missing)
                      + " 還沒有證號，請補填後儲存。儲存時沒有證號的證照將不再列入配對。", warn=True)

    cy = current_year()
    p = prof
    school_idx = None
    school_other_default = ""
    if p:
        if p.school in SCHOOLS:
            school_idx = SCHOOLS.index(p.school)
        else:
            school_idx = len(SCHOOLS)
            school_other_default = p.school

    with st.form("trainer_profile", border=True):
        st.markdown("**基本資料**　🔒 電話、Email、LINE ID 在錄用前不會給主辦方看到")
        c1, c2 = st.columns(2)
        name = c1.text_input("姓名 *", value=p.name if p else u.display_name)
        gender = c2.radio("性別 *", GENDERS, horizontal=True,
                          index=GENDERS.index(p.gender) if p and p.gender in GENDERS else None)
        c1, c2, c3 = st.columns(3)
        phone = c1.text_input("手機 *", value=p.phone if p else "", placeholder="0912-345-678")
        email = c2.text_input("Email *", value=p.email if p else "")
        line_id = c3.text_input("LINE ID *", value=(p.line_contact_id or "") if p else "",
                                help="錄用後讓主辦方加你好友聯繫。LINE → 主頁 → 設定 → 個人檔案 可以看到你的 ID")

        st.markdown("**學歷**")
        c1, c2 = st.columns([3, 2])
        school = c1.selectbox("畢業學校 *", SCHOOLS + [SCHOOL_OTHER], index=school_idx,
                              placeholder="輸入關鍵字搜尋，例如「體育」")
        grad = c2.number_input("畢業年份（西元）*", min_value=1970, max_value=cy + 6,
                               value=p.graduation_year if p else cy, step=1,
                               help="還在學請填預計畢業年份")
        school_other = st.text_input("學校名稱（選「其他」時填寫）", value=school_other_default)

        st.markdown("**接案地區**")
        counties = st.multiselect(
            "主要接案縣市 *（可複選；全台都能接就選「全台（全選）」）", [COUNTY_ALL] + COUNTIES,
            default=collapse_counties(p.service_counties) if p else [], placeholder="選擇縣市")

        st.markdown("**證照**　🔒 證號只給平台管理員核對，不會給主辦方看到。"
                    "**有填證號才算具備該證照**，沒有的留空即可。")
        nos = {}
        for row in (LICENSE_FIELDS[:2], LICENSE_FIELDS[2:]):
            cols = st.columns(3)
            for col, (field, _code, label, help_) in zip(cols, row):
                nos[field] = col.text_input(label, value=(getattr(p, field) or "") if p else "", help=help_)
        experience = st.text_area("近期賽會經歷", value=p.experience if p else "", height=120,
                                  placeholder="例如：2025 全國中等學校運動會 田徑場邊防護")
        consent = st.checkbox("我已閱讀並同意「隱私權聲明」，同意平台為媒合目的蒐集與利用我的資料 *",
                              value=bool(p and p.consent_at))
        submitted = st.form_submit_button("儲存並送審" if not p or p.review_status != "approved" else "儲存",
                                          type="primary")

    if not submitted:
        return

    school_val = school_other.strip() if school == SCHOOL_OTHER else school
    nos = {k: v.strip() for k, v in nos.items()}
    errors = []
    if not name.strip():
        errors.append("請填寫姓名")
    if gender is None:
        errors.append("請選擇性別")
    if not MOBILE_RE.match(phone.strip()):
        errors.append("手機格式請填 09 開頭共 10 碼")
    if "@" not in email:
        errors.append("Email 格式不正確")
    if err := line_id_error(line_id):
        errors.append(err)
    if not school_val:
        errors.append("請選擇或填寫畢業學校")
    if not counties:
        errors.append("請至少選一個接案縣市")
    for field, _code, label, _h in LICENSE_FIELDS:
        if err := license_no_error(label, nos[field]):
            errors.append(err)
    if not any(nos.values()):
        errors.append("請至少填寫一個證照的證號")
    if not consent:
        errors.append("請勾選同意隱私權聲明")
    if errors:
        for m in errors:
            st.error(m)
        return

    held = {code for field, code, _l, _h in LICENSE_FIELDS if nos[field]}
    first_aid = [x for x in FIRST_AID_LICENSES if x in held]
    with db.session() as s:
        cur = s.get(TrainerProfile, u.id)
        is_new = cur is None
        if is_new:
            cur = TrainerProfile(user_id=u.id)
            s.add(cur)
        license_changed = is_new or (
            any((getattr(cur, f) or "") != nos[f] for f, *_ in LICENSE_FIELDS)
            or sorted(cur.first_aid_licenses or []) != sorted(first_aid)
            or cur.school != school_val or cur.graduation_year != int(grad))
        cur.name, cur.gender = name.strip(), gender
        cur.phone, cur.email = phone.strip(), email.strip()
        cur.line_contact_id = line_id.strip()
        cur.school, cur.graduation_year = school_val, int(grad)
        cur.service_counties = expand_counties(counties)
        for f, *_ in LICENSE_FIELDS:
            setattr(cur, f, nos[f] or None)
        cur.has_at_license, cur.has_pt_license = "AT" in held, "PT" in held
        cur.first_aid_licenses, cur.experience = first_aid, experience.strip()
        cur.consent_at = cur.consent_at or now()
        resubmit = license_changed or cur.review_status == "rejected"
        if resubmit:
            cur.review_status, cur.review_note = "pending", None
        db.log(s, u.id, "profile_saved", new=is_new, resubmit=bool(resubmit))
    ui.flash("已儲存並送出審核。審核通過後會用 LINE 通知你。" if resubmit else "已儲存。")
    st.rerun()
