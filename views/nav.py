"""頁面登錄：app.py 每次 rerun 把本 session 的 st.Page 存進 session_state，
讓各頁可以用 nav.go("profile") 互跳（每位使用者的選單依身分不同）。"""
import streamlit as st


def register(pages: dict):
    st.session_state["_pages"] = pages


def go(key: str):
    page = st.session_state.get("_pages", {}).get(key)
    if page is not None:
        st.switch_page(page)
