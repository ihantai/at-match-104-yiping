"""LINE 一鍵登入（OAuth 2.1 / LINE Login v2.1）。

Streamlit 在跳轉到 LINE 再回來後會開新的 session，所以 OAuth state 不能放在
session_state，這裡改用 HMAC 簽章的 state（含時間戳與登入後要前往的案件編號）。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from urllib.parse import urlencode

import requests
import streamlit as st

from . import config, db
from .db import User, now

AUTHORIZE_URL = "https://access.line.me/oauth2/v2.1/authorize"
TOKEN_URL = "https://api.line.me/oauth2/v2.1/token"
PROFILE_URL = "https://api.line.me/v2/profile"
STATE_TTL = 600


class LoginError(Exception):
    pass


def _sign(payload: bytes) -> str:
    return hmac.new(config.state_secret().encode(), payload, hashlib.sha256).hexdigest()[:32]


def make_state(next_case: int | None = None, next_page: str | None = None, rec_case: int | None = None) -> str:
    raw = json.dumps({"n": secrets.token_hex(8), "t": int(time.time()), "c": next_case,
                      "p": next_page, "rc": rec_case}).encode()
    body = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return f"{body}.{_sign(raw)}"


def read_state(state: str) -> dict:
    try:
        body, sig = state.rsplit(".", 1)
        raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
    except Exception as e:
        raise LoginError("登入狀態格式錯誤，請重新登入") from e
    if not hmac.compare_digest(sig, _sign(raw)):
        raise LoginError("登入驗證失敗，請重新登入")
    data = json.loads(raw)
    if time.time() - data["t"] > STATE_TTL:
        raise LoginError("登入逾時，請重新登入")
    return data


def login_url(next_case: int | None = None, next_page: str | None = None, rec_case: int | None = None) -> str:
    params = {
        "response_type": "code",
        "client_id": config.line_login_channel_id(),
        "redirect_uri": config.app_base_url() + "/",
        "state": make_state(next_case, next_page, rec_case),
        "scope": "profile openid",
        # 登入時順便邀請加入官方帳號好友，否則之後推播會失敗
        "bot_prompt": "aggressive",
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def exchange_code(code: str) -> dict:
    r = requests.post(TOKEN_URL, data={
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": config.app_base_url() + "/",
        "client_id": config.line_login_channel_id(),
        "client_secret": config.line_login_channel_secret(),
    }, timeout=10)
    if r.status_code != 200:
        raise LoginError(f"LINE 登入失敗（{r.status_code}），請再試一次")
    token = r.json()["access_token"]
    p = requests.get(PROFILE_URL, headers={"Authorization": f"Bearer {token}"}, timeout=10)
    if p.status_code != 200:
        raise LoginError("無法取得 LINE 個人資料，請再試一次")
    return p.json()  # userId, displayName, pictureUrl


def upsert_user(line_user_id: str, display_name: str, picture_url: str | None = None) -> int:
    with db.session() as s:
        u = s.query(User).filter_by(line_user_id=line_user_id).one_or_none()
        if u is None:
            u = User(line_user_id=line_user_id, display_name=display_name, picture_url=picture_url)
            s.add(u)
            s.flush()
            db.log(s, u.id, "signup")
        else:
            u.display_name = display_name or u.display_name
            u.picture_url = picture_url or u.picture_url
        if u.is_blocked:
            raise LoginError("此帳號已被停權，無法登入。如有疑問請聯繫平台管理員。")
        u.last_login_at = now()
        db.log(s, u.id, "login")
        return u.id


def handle_callback() -> dict | None:
    """處理 LINE 回呼。回傳登入前想去的地方 {"c": 案件編號, "p": 頁面}（若有）。"""
    qp = st.query_params
    if "code" not in qp or "state" not in qp:
        if "error" in qp:
            st.query_params.clear()
            raise LoginError("你取消了 LINE 登入")
        return None
    code, state = qp["code"], qp["state"]
    st.query_params.clear()
    data = read_state(state)
    profile = exchange_code(code)
    st.session_state.uid = upsert_user(profile["userId"], profile.get("displayName", ""),
                                       profile.get("pictureUrl"))
    if profile["userId"] in config.admin_line_ids():
        st.session_state.is_admin = True
    return data


# --- LIFF：在 LINE 裡開啟時免登入 --------------------------------------------------

VERIFY_URL = "https://api.line.me/oauth2/v2.1/verify"


def verify_liff_token(id_token: str) -> dict:
    """向 LINE 驗證 LIFF 取得的 ID token（簽章、有效期限、發給哪個 channel 都由 LINE 檢查）。"""
    if not config.line_login_channel_id():
        raise LoginError("尚未設定 LINE_LOGIN_CHANNEL_ID，無法使用 LIFF 登入")
    r = requests.post(VERIFY_URL, data={"id_token": id_token, "client_id": config.line_login_channel_id()},
                      timeout=10)
    if r.status_code != 200:
        raise LoginError("LINE 身分驗證失敗或已逾時，請從 LINE 重新開啟")
    data = r.json()
    if not data.get("sub"):
        raise LoginError("LINE 身分驗證失敗")
    return data  # sub = userId, name, picture


def handle_liff() -> bool:
    """網址帶有 liff_token（由 LIFF 啟動頁轉過來）→ 驗證後直接登入。回傳是否處理過。"""
    tok = st.query_params.get("liff_token")
    if not tok:
        return False
    del st.query_params["liff_token"]  # 立刻從網址移除，避免留在瀏覽紀錄
    data = verify_liff_token(tok)
    st.session_state.uid = upsert_user(data["sub"], data.get("name", ""), data.get("picture"))
    st.session_state.via_liff = True
    if data["sub"] in config.admin_line_ids():
        st.session_state.is_admin = True
    return True


def demo_login(line_user_id: str, display_name: str):
    st.session_state.uid = upsert_user(line_user_id, display_name)


def admin_login(password: str) -> bool:
    expected = config.admin_password()
    if expected and hmac.compare_digest(password, str(expected)):
        st.session_state.is_admin = True
        return True
    return False


def current_user() -> User | None:
    uid = st.session_state.get("uid")
    if not uid:
        return None
    with db.session() as s:
        u = s.get(User, uid)
        if u is None or u.is_blocked:
            st.session_state.pop("uid", None)
            return None
        # 預先載入關聯，離開 session 後仍可讀取
        _ = u.trainer, u.requester
        return u


def is_admin() -> bool:
    return bool(st.session_state.get("is_admin"))


def logout():
    for k in ("uid", "is_admin", "view_case_id"):
        st.session_state.pop(k, None)
