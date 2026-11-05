"""賽會防護員媒合平台 — Streamlit 進入點。

執行：streamlit run app.py
"""
import streamlit as st

from core import auth, config, db, ui

st.set_page_config(page_title="賽會防護員媒合平台", page_icon="🩹", layout="centered")


@st.cache_resource
def _engine():
    return db.make_engine(config.database_url())


try:
    db.use(_engine())
except Exception as _err:  # 資料庫設定錯誤：顯示中文診斷，而不是一大串英文錯誤
    st.title("系統暫時無法使用")
    st.write("網站正在維護中，請稍後再試。")
    with st.expander("管理員：資料庫連線失敗的原因與修正方式", expanded=True):
        st.markdown(config.db_error_hint(config.database_url(), _err))
        st.caption("修改 Secrets 後，到 Streamlit Cloud 右下角 Manage app → ⋮ → Reboot app。")
    st.stop()
ui.inject_css()

# --- LINE 登入回呼、LIFF 免登入、各種深層連結 ------------------------------------
try:
    auth.handle_liff()
    back = auth.handle_callback()
    if back:
        if back.get("c"):
            st.session_state.view_case_id = int(back["c"])
            st.session_state.jump_to_case = True
        if back.get("p"):
            st.session_state.goto = back["p"]
            st.session_state.rec_case = back.get("rc")
except auth.LoginError as err:
    st.error(str(err))
except Exception:  # 網路錯誤等
    st.error("登入時發生問題，請再試一次。")

qp = st.query_params
if "case" in qp:
    try:
        st.session_state.view_case_id = int(qp["case"])
        st.session_state.jump_to_case = True
    except ValueError:
        pass
    del st.query_params["case"]
if qp.get("page") == "record":  # 防護紀錄表（LIFF 或 LINE 訊息裡的連結）
    st.session_state.goto = "record"
    if qp.get("rec_case", "").isdigit():
        st.session_state.rec_case = int(qp["rec_case"])
    for k in ("page", "rec_case"):
        if k in st.query_params:
            del st.query_params[k]

user = auth.current_user()
admin = auth.is_admin()

# --- 依身分組合選單 -----------------------------------------------------------
from views import admin as v_admin  # noqa: E402
from views import common, nav, records as v_records, requester, trainer  # noqa: E402

P: dict[str, st.Page] = {}  # 自訂鍵 → 頁面（預設頁的 url_path 會是空字串，不能拿來當鍵）


def page(fn, title: str, icon: str, key: str, default: bool = False):
    P[key] = st.Page(fn, title=title, icon=f":material/{icon}:", url_path=key, default=default)
    return P[key]


pages: dict[str, list] = {}
if user is None:
    pages[""] = [page(common.landing, "首頁", "home", "home", default=not admin),
                 page(common.privacy_page, "隱私權聲明", "shield", "privacy"),
                 page(common.feedback_page, "意見回饋", "forum", "feedback")]
elif user.role is None:
    pages[""] = [page(common.choose_role, "選擇身分", "badge", "start", default=True),
                 page(common.privacy_page, "隱私權聲明", "shield", "privacy")]
elif user.role == "trainer":
    pages["防護員"] = [page(trainer.jobs, "賽會職缺", "work", "jobs", default=True),
                    page(trainer.my_applications, "我的應徵", "assignment_turned_in", "my-applications"),
                    page(trainer.profile, "我的履歷", "badge", "profile")]
    pages["防護紀錄"] = [page(v_records.record_page, "防護紀錄表", "assignment", "record"),
                     page(v_records.records_page, "紀錄查詢", "folder_open", "records"),
                     page(v_records.plan_page, "方案與付費", "workspace_premium", "plan")]
    pages["其他"] = [page(common.feedback_page, "意見回饋", "forum", "feedback"),
                   page(common.privacy_page, "隱私權聲明", "shield", "privacy")]
else:
    pages["發案方"] = [page(requester.my_cases, "我的案件", "list_alt", "my-cases", default=True),
                    page(requester.post_case, "刊登賽事", "add_circle", "post"),
                    page(requester.profile, "單位資料", "apartment", "org")]
    pages["其他"] = [page(common.feedback_page, "意見回饋", "forum", "feedback"),
                   page(common.privacy_page, "隱私權聲明", "shield", "privacy")]

if admin:
    pages["管理後台"] = [
        page(v_admin.dashboard, "數據總覽", "monitoring", "admin", default=user is None),
        page(v_admin.review, "證照審核", "verified", "admin-review"),
        page(v_admin.reports, "檢舉與黑名單", "gpp_bad", "admin-reports"),
        page(v_admin.cases, "案件與通知紀錄", "notifications", "admin-cases"),
        page(v_admin.billing_page, "付費功能試驗", "payments", "admin-billing"),
        page(v_admin.export, "資料匯出", "download", "admin-export"),
    ]

nav.register(P)
pg = st.navigation(pages)
common.sidebar(user, admin)

# 案件連結（LINE 通知卡片上的按鈕）：
# 已登入的防護員 → 帶到「賽會職缺」頁並直接打開該案件；未登入 → 保留到登入後
if user and st.session_state.get("view_case_id"):
    on_jobs = "jobs" in P and pg.title == P["jobs"].title
    if user.role == "trainer":
        if st.session_state.pop("jump_to_case", False) and not on_jobs:
            st.switch_page(P["jobs"])
        elif not on_jobs:
            st.session_state.pop("view_case_id", None)
    else:
        st.session_state.pop("view_case_id", None)

# 防護紀錄表連結（LIFF / LINE 訊息）：防護員登入後直接打開紀錄表
if user and st.session_state.get("goto") == "record":
    if user.role == "trainer":
        st.session_state.pop("goto")
        if "record" in P and pg.title != P["record"].title:
            st.switch_page(P["record"])
    elif user.role == "requester":
        st.session_state.pop("goto")
        st.session_state.pop("rec_case", None)

ui.show_flash()
pg.run()
