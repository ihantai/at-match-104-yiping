"""介面冒煙測試：各身分的預設頁都能正常渲染、沒有例外。"""
import pytest
from streamlit.testing.v1 import AppTest

from core import db, seed
from core.db import User


@pytest.fixture()
def app_env(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path}/smoke.db"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("ADMIN_PASSWORD", "pw")
    db.init(url)
    with db.session() as s:
        seed.seed(s)
    return url


def _run(**state):
    at = AppTest.from_file("../app.py", default_timeout=30)
    for k, v in state.items():
        at.session_state[k] = v
    return at.run()


def _uid(lid):
    with db.session() as s:
        return s.query(User).filter_by(line_user_id=lid).one().id


def test_landing(app_env):
    at = _run()
    assert not at.exception
    assert any("賽會缺防護員" in m.value for m in at.markdown)


@pytest.mark.parametrize("lid,title", [("demo_t01", "賽會職缺"), ("demo_r02", "我的案件")])
def test_role_home(app_env, lid, title):
    at = _run(uid=_uid(lid))
    assert not at.exception
    assert at.title[0].value == title


def test_requester_never_sees_unhired_phone(app_env):
    at = _run(uid=_uid("demo_r02"))
    html = "".join(m.value for m in at.markdown) + "".join(c.value for c in at.caption)
    assert "0911-000-008" not in html  # 李宗翰應徵中、未錄用
    assert "李O翰" in html


def test_trainer_opens_case_from_line_link(app_env):
    with db.session() as s:
        from core.db import Case
        cid = s.query(Case).filter_by(status="open").first().id
    at = _run(uid=_uid("demo_t08"), view_case_id=cid, jump_to_case=True)
    assert not at.exception
    assert any(b.label in ("取消應徵", "我有興趣，我要應徵") for b in at.button)


def test_admin_dashboard(app_env):
    at = _run(is_admin=True)
    assert not at.exception
    assert at.title[0].value == "數據總覽"
