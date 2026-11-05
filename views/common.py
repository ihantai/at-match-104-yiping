"""首頁、身分選擇、隱私權聲明、意見回饋、側邊欄。"""
from __future__ import annotations

import secrets

import streamlit as st
from sqlalchemy import func, select

from core import auth, config, db, seed, ui
from core.constants import FEATURE_WISHES
from core.db import Case, Feedback, TrainerProfile, User


# --- 首頁 -------------------------------------------------------------------

def landing():
    st.markdown("""
<div class="hero">
  <h1>賽會缺防護員？<br>發一則案件，系統幫你找人。</h1>
  <p>全台、全運動項目的賽會防護員媒合。依證照、年資、學校與地區自動配對，
  用 LINE 一對一通知符合條件的防護員；錄用前不公開任何人的聯絡方式。現在完全免費。</p>
</div>""", unsafe_allow_html=True)

    case_id = st.session_state.get("view_case_id")
    if case_id:
        ui.notice(f"你是從案件 <b>No.{case_id:03d}</b> 的通知連結進來的，登入後會直接打開這個案件。")
    if st.session_state.get("goto") == "record":
        ui.notice("📋 你是從「防護紀錄表」連結進來的，登入後會直接打開紀錄表。")

    _login_box(case_id)

    st.markdown("### 怎麼運作")
    st.markdown("""
<div class="steps">
  <div class="step"><b>發布賽事</b><span>填日期、地點、薪資，勾選證照與年資條件</span></div>
  <div class="step"><b>自動配對</b><span>系統篩出符合的防護員，LINE 個別通知</span></div>
  <div class="step"><b>防護員應徵</b><span>點通知卡片，有疑問先發問，再一鍵應徵</span></div>
  <div class="step"><b>確認錄用</b><span>錄用後雙方才看得到彼此電話與姓名</span></div>
</div>""", unsafe_allow_html=True)

    st.write("")
    c1, c2 = st.columns(2, gap="large")
    with c1:
        st.markdown("#### 我是防護員")
        st.markdown("- 填一次履歷，符合的案子自動送到 LINE\n"
                    "- 只收到你接得到的縣市與條件\n"
                    "- 錄用前主辦方只看到「王O明」，看不到你的電話")
    with c2:
        st.markdown("#### 我要找防護員")
        st.markdown("- 不用再到處貼文、等私訊\n"
                    "- 高風險賽事（柔道、橄欖球、格鬥…）自動提醒 EMT 需求\n"
                    "- 應徵者依條件吻合度、評價與應徵先後排好")

    with db.session() as s:
        n_tr = s.scalar(select(func.count()).select_from(TrainerProfile)
                        .where(TrainerProfile.review_status == "approved")) or 0
        n_case = s.scalar(select(func.count(Case.id))) or 0
    if n_tr >= 10:
        st.caption(f"目前有 {n_tr} 位通過審核的防護員、累計 {n_case} 場賽事在平台上媒合。")


def _login_box(case_id):
    with st.container(border=True):
        if config.line_login_enabled():
            st.markdown("**用 LINE 登入開始使用**（防護員與發案方都用同一個入口）")
            ui.line_button(auth.login_url(case_id, st.session_state.get("goto"),
                                          st.session_state.get("rec_case")))
            st.caption("登入時會邀請你加入官方帳號好友，這樣才收得到案件通知。")
        else:
            st.markdown("**LINE 登入尚未設定**")
            st.caption("管理員請在 Secrets 填入 LINE_LOGIN_CHANNEL_ID / LINE_LOGIN_CHANNEL_SECRET。"
                       "設定前可先用下方測試帳號體驗完整流程。")

        if config.demo_mode():
            st.divider()
            _demo_login()


def _demo_login():
    st.markdown("**測試模式**　不用 LINE，直接用測試帳號體驗。測試帳號不會收到真的 LINE 訊息。")
    with db.session() as s:
        demo_users = s.execute(
            select(User).where(User.line_user_id.like("demo_%"), User.is_blocked.is_(False))
            .order_by(User.role, User.id)
        ).scalars().all()
        has_seed = seed.has_demo_data(s)

    if not has_seed:
        if st.button("建立測試資料（13 位防護員、2 個發案方）", type="secondary"):
            with db.session() as s:
                msg = seed.seed(s)
            ui.flash(msg)
            st.rerun()

    role_label = {"trainer": "防護員", "requester": "發案方", None: "未選身分"}
    tab1, tab2 = st.tabs(["用現有測試帳號", "建立新測試帳號"])
    with tab1:
        if demo_users:
            opts = {u.id: f"{u.display_name}（{role_label.get(u.role)}）" for u in demo_users}
            pick = st.selectbox("選擇帳號", list(opts), format_func=opts.get)
            if st.button("登入這個測試帳號", type="primary"):
                u = next(x for x in demo_users if x.id == pick)
                auth.demo_login(u.line_user_id, u.display_name)
                st.rerun()
        else:
            st.caption("還沒有測試帳號，按上方按鈕建立，或切到「建立新測試帳號」。")
    with tab2:
        name = st.text_input("暱稱", placeholder="例如：測試防護員小林")
        if st.button("建立並登入", disabled=not name.strip()):
            auth.demo_login(f"demo_{secrets.token_hex(5)}", name.strip())
            st.rerun()


# --- 選擇身分 ---------------------------------------------------------------

def choose_role():
    u = auth.current_user()
    st.title(f"歡迎，{u.display_name}")
    st.write("先告訴我們你要用平台做什麼。之後可以在左側選單切換。")
    c1, c2 = st.columns(2, gap="large")
    with c1, st.container(border=True):
        st.markdown("#### 我是防護員")
        st.caption("運動防護員、物理治療師、體育相關科系學生")
        if st.button("我要接賽會案件", type="primary", width="stretch"):
            _set_role(u.id, "trainer")
    with c2, st.container(border=True):
        st.markdown("#### 我要找防護員")
        st.caption("賽事主辦方、學校校隊、單項協會、俱樂部")
        if st.button("我要刊登賽事", type="primary", width="stretch"):
            _set_role(u.id, "requester")


def _set_role(uid: int, role: str):
    with db.session() as s:
        u = s.get(User, uid)
        u.role = role
        db.log(s, uid, "choose_role", role=role)
    st.rerun()


# --- 隱私權聲明 -------------------------------------------------------------

def privacy_page():
    st.title("隱私權聲明與個資蒐集告知")
    ui.notice("這是測試階段的範本內容，正式上線前請交由法律顧問依《個人資料保護法》審閱。", warn=True)
    st.markdown("""
**蒐集目的**　賽會防護人力媒合、身分與證照查核（證號僅供平台管理員核對，不會提供給發案方）、通知服務、平台改善與統計分析。

**蒐集類別**　LINE 使用者識別碼與暱稱、姓名、性別、電話、Email、學歷、證照種類與證號、服務縣市、賽會經歷；發案方之單位名稱與聯絡人資料；平台使用紀錄。

**利用期間、地區、對象及方式**　於平台營運期間及法定保存期間內，在台灣境內與平台所使用之雲端服務地區，由本平台用於上述目的。

**錄用前不公開**　發案方在按下「確認錄用」之前，只會看到去識別化的姓名（例如王O明）、學校、年資與證照，看不到電話、Email、LINE ID 與完整姓名。錄用後，僅錄用的雙方可以看到彼此的聯絡方式。

**防護紀錄**　防護員使用「防護紀錄表」記錄的選手傷病與處置資料，屬於健康相關的個人資料，僅提供給記錄的防護員與該場賽事的主辦方，用於傷病追蹤、耗材請款與保險申請；平台不會用於其他目的。選手或家長可要求查詢或刪除。

**付費功能**　目前為功能試驗，付款流程為模擬，不會實際扣款，也不會蒐集信用卡資料。

**你的權利**　你可以隨時要求查詢、閱覽、複製、補充、更正、停止蒐集利用或刪除你的個人資料，請透過「意見回饋」頁或官方 LINE 帳號聯繫我們。

**不提供的影響**　若不提供必要欄位（姓名、電話、證照等），將無法完成防護員審核或刊登賽事。
""")


# --- 意見回饋 ---------------------------------------------------------------

def feedback_page():
    u = auth.current_user()
    st.title("意見回饋")
    st.write("平台還在測試階段，你的意見會直接決定下一步做什麼。")
    with st.form("feedback", clear_on_submit=True, border=True):
        st.markdown("整體來說，這個平台好用嗎？")
        stars = st.feedback("stars")
        wishes = st.multiselect("你最希望增加哪些功能？（可複選）", FEATURE_WISHES)
        source = st.selectbox("你是從哪裡知道這個平台的？",
                              ["朋友 / 同學介紹", "學校 / 老師", "協會 / 主辦方", "社群媒體", "搜尋", "其他"],
                              index=None, placeholder="請選擇")
        msg = st.text_area("其他想說的（遇到的問題、建議都可以）", height=120)
        if st.form_submit_button("送出回饋", type="primary"):
            if stars is None and not wishes and not msg.strip():
                st.warning("至少給個星等或寫一句話再送出。")
            else:
                with db.session() as s:
                    s.add(Feedback(user_id=u.id if u else None, role=u.role if u else None,
                                   score=None if stars is None else stars + 1,
                                   wishes=wishes, source=source, message=msg.strip()))
                    db.log(s, u.id if u else None, "feedback")
                st.success("已送出，謝謝你！")


# --- 側邊欄 -----------------------------------------------------------------

def sidebar(user, admin: bool):
    with st.sidebar:
        if user:
            role = {"trainer": "防護員", "requester": "發案方"}.get(user.role, "尚未選擇身分")
            st.markdown(f"**{ui.e(user.display_name)}**　{ui.tag(role)}", unsafe_allow_html=True)
            if user.line_user_id.startswith("demo_"):
                st.caption("測試帳號")
            if user.role:
                other = "requester" if user.role == "trainer" else "trainer"
                label = "切換為發案方" if other == "requester" else "切換為防護員"
                if st.button(label, width="stretch"):
                    with db.session() as s:
                        s.get(User, user.id).role = other
                        db.log(s, user.id, "switch_role", role=other)
                    st.session_state.pop("view_case_id", None)
                    st.rerun()
            if st.button("登出", width="stretch"):
                auth.logout()
                st.rerun()
        if admin:
            st.divider()
            st.caption("目前為管理員模式")
            if not user and st.button("離開管理員模式", width="stretch"):
                auth.logout()
                st.rerun()
        elif config.admin_password():
            with st.expander("管理員登入"):
                pw = st.text_input("管理員密碼", type="password")
                if st.button("進入後台"):
                    if auth.admin_login(pw):
                        st.rerun()
                    else:
                        st.error("密碼不正確")
        st.divider()
        st.caption("測試版：功能還在調整，有任何問題請到「意見回饋」告訴我們。")
