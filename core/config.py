"""設定讀取：優先讀 Streamlit secrets，其次讀環境變數。

所有金鑰都放在 .streamlit/secrets.toml（本機）或 Streamlit Cloud 的 Secrets 設定，
絕對不要 commit 進 GitHub。
"""
import os
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Taipei")


def _get(key: str, default=None):
    try:
        import streamlit as st

        if key in st.secrets:
            return st.secrets[key]
    except Exception:
        pass
    return os.environ.get(key, default)


def _bool(v) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in {"1", "true", "yes", "on"}


def _encode_credentials(url: str) -> str:
    """密碼含有 @ # / : ? % 等符號時自動做網址編碼。
    以「最後一個 @」切開帳密與主機，所以密碼裡有 @ 也能正確解析；已編碼過的密碼不會被重複編碼。"""
    from urllib.parse import quote, unquote

    scheme, sep, rest = url.partition("://")
    if not sep or "@" not in rest:
        return url
    userinfo, _, hostpart = rest.rpartition("@")
    user, colon, pw = userinfo.partition(":")
    user = quote(unquote(user), safe="")
    if colon:
        return f"{scheme}://{user}:{quote(unquote(pw), safe='')}@{hostpart}"
    return f"{scheme}://{user}@{hostpart}"


def database_url() -> str:
    url = str(_get("DATABASE_URL", "sqlite:///data/app.db")).strip()
    # Supabase / Heroku 給的是 postgres://，SQLAlchemy 需要 postgresql+psycopg2://
    if url.startswith("postgres://"):
        url = "postgresql+psycopg2://" + url[len("postgres://"):]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg2://" + url[len("postgresql://"):]
    if url.startswith("postgresql"):
        url = _encode_credentials(url)
    return url


def app_base_url() -> str:
    return str(_get("APP_BASE_URL", "http://localhost:8501")).rstrip("/")


def line_login_channel_id() -> str | None:
    return _get("LINE_LOGIN_CHANNEL_ID")


def line_login_channel_secret() -> str | None:
    return _get("LINE_LOGIN_CHANNEL_SECRET")


def line_messaging_token() -> str | None:
    return _get("LINE_MESSAGING_ACCESS_TOKEN")


def line_login_enabled() -> bool:
    return bool(line_login_channel_id() and line_login_channel_secret())


def line_push_enabled() -> bool:
    return bool(line_messaging_token())


def demo_mode() -> bool:
    """測試模式：可用假 LINE 帳號登入、一鍵建立種子資料。正式上線請關閉。"""
    return _bool(_get("DEMO_MODE", "true"))


def admin_password() -> str | None:
    return _get("ADMIN_PASSWORD")


def admin_line_ids() -> set[str]:
    raw = _get("ADMIN_LINE_IDS", "")
    if isinstance(raw, (list, tuple)):
        return {str(x).strip() for x in raw if str(x).strip()}
    return {x.strip() for x in str(raw).split(",") if x.strip()}


def state_secret() -> str:
    """簽章 OAuth state 用。未設定時退回 channel secret。"""
    return str(_get("STATE_SECRET") or line_login_channel_secret() or "dev-only-secret")


def db_error_hint(url: str, err: Exception) -> str:
    """資料庫連不上時，依錯誤內容給出具體的中文修正建議（不含密碼）。"""
    from urllib.parse import urlsplit

    msg = str(err)
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        host = ""
    if "@" in msg.split("host name", 1)[-1][:120]:
        return ("密碼裡的特殊符號（例如 `@`）讓系統把密碼的一部分誤認為主機名稱。"
                "請更新到最新版程式（會自動處理密碼符號），或到 Supabase 重設一組只有英數字的密碼。")
    if host.startswith("db.") and host.endswith(".supabase.co"):
        return ("你使用的是 Supabase 的 **Direct connection**（`db.xxxx.supabase.co`），這個位址只支援 IPv6，"
                "Streamlit Cloud 連不上。\n\n請到 Supabase 專案頁面上方的 **Connect** → 選 **Session pooler**，"
                "複製那串連線字串（主機會是 `aws-0-…pooler.supabase.com`、帳號是 `postgres.專案代號`），"
                "貼到 Secrets 的 `DATABASE_URL`，再把 `[YOUR-PASSWORD]` 換成你的資料庫密碼。")
    if "password authentication failed" in msg:
        return ("資料庫密碼錯誤。請確認 `DATABASE_URL` 裡的密碼；"
                "如果密碼含有 `@ # / : ?` 等符號，需要改成網址編碼（例如 `@` 寫成 `%40`），"
                "或到 Supabase → Database Settings 重設一組只有英數字的密碼。")
    if "Tenant or user not found" in msg:
        return ("找不到這個資料庫帳號。使用 Session pooler 時，帳號要寫成 `postgres.你的專案代號`"
                "（不是只有 `postgres`），請直接從 Supabase 的 Connect 畫面複製完整字串。")
    if "could not translate host name" in msg or "Name or service not known" in msg:
        return "找不到資料庫主機，請確認 `DATABASE_URL` 的主機名稱沒有打錯。"
    if "timeout" in msg.lower() or "could not connect" in msg:
        return "連線逾時。請確認 Supabase 專案沒有被暫停（免費方案一週沒使用會自動暫停，到 Supabase 按 Restore 即可）。"
    return "請確認 Secrets 裡的 `DATABASE_URL` 是否正確。"


def liff_id() -> str | None:
    """LINE Developers → LINE Login channel → LIFF 分頁建立的 LIFF ID。"""
    return _get("LIFF_ID") or None


def liff_launcher_url() -> str | None:
    """部署在 GitHub Pages 的 LIFF 啟動頁網址（liff/index.html）。只在說明文字中使用。"""
    return _get("LIFF_LAUNCHER_URL") or None


def billing_mode() -> str:
    """mock = 模擬金流（功能試驗，不會扣款）。之後接真金流時改成 ecpay / newebpay / linepay。"""
    return str(_get("BILLING_MODE", "mock")).lower()
