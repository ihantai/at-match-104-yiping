"""LINE Messaging API：一對一推播 Flex Message。

- 有設定 LINE_MESSAGING_ACCESS_TOKEN 且對象是真實 LINE 帳號 → 真的發送
- 否則（測試模式 / demo_ 帳號）→ 只寫入 notifications 表，標記為「模擬發送」
每一則都會記錄，後台「通知紀錄」可直接拿來驗收配對精準度。

每位防護員是「個別」呼叫 Push API，彼此看不到還有誰收到通知。
"""
from __future__ import annotations

import uuid

import requests

from . import config, fmt
from .constants import LICENSE_REQ
from .db import Case, Notification, User

PUSH_URL = "https://api.line.me/v2/bot/message/push"
BRAND = "#0B5E7E"
WARN = "#C62828"


def case_url(case_id: int) -> str:
    return f"{config.app_base_url()}/?case={case_id}"


def salary_text(c: Case) -> str:
    return f"{c.salary_type} NT${c.salary_amount:,}"


def _row(label: str, value: str) -> dict:
    return {
        "type": "box", "layout": "baseline", "spacing": "sm",
        "contents": [
            {"type": "text", "text": label, "color": "#8A97A6", "size": "sm", "flex": 2},
            {"type": "text", "text": value, "wrap": True, "size": "sm", "color": "#1D2B36", "flex": 5},
        ],
    }


def case_flex(c: Case) -> dict:
    body = [
        {"type": "text", "text": c.title, "weight": "bold", "size": "lg", "wrap": True},
        {"type": "separator", "margin": "md"},
        {"type": "box", "layout": "vertical", "spacing": "sm", "margin": "md", "contents": [
            _row("運動項目", c.sport_type),
            _row("時間", "\n".join(fmt.schedule_lines(c, limit=6)) if len(c.schedule) > 1
                 else fmt.schedule_summary(c)),
            _row("地點", c.location_county),
            _row("薪資", salary_text(c)),
            _row("證照", LICENSE_REQ.get(c.license_req, "不限")),
        ]},
    ]
    bubble = {
        "type": "bubble",
        "body": {"type": "box", "layout": "vertical", "contents": body},
        "footer": {"type": "box", "layout": "vertical", "contents": [{
            "type": "button", "style": "primary", "color": BRAND,
            "action": {"type": "uri", "label": "查看詳情與應徵", "uri": case_url(c.id)},
        }]},
    }
    if c.is_high_risk:
        bubble["header"] = {
            "type": "box", "layout": "vertical", "backgroundColor": WARN, "paddingAll": "10px",
            "contents": [{
                "type": "text", "color": "#FFFFFF", "weight": "bold", "size": "sm",
                "text": "⚠ 高風險接觸性運動，請評估自身急救能力",
                "wrap": True,
            }],
        }
    return {"type": "flex", "altText": f"新賽會案件：{c.title}（{c.location_county}）", "contents": bubble}


def text(msg: str) -> dict:
    return {"type": "text", "text": msg}


def _is_real_line_user(u: User) -> bool:
    return bool(u.line_user_id) and not u.line_user_id.startswith("demo_")


def send(s, user: User, kind: str, messages: list[dict], summary: str, case_id: int | None = None) -> Notification:
    n = Notification(case_id=case_id, user_id=user.id, kind=kind, summary=summary[:300])
    if config.line_push_enabled() and _is_real_line_user(user):
        n.channel = "line"
        try:
            r = requests.post(
                PUSH_URL,
                headers={
                    "Authorization": f"Bearer {config.line_messaging_token()}",
                    "X-Line-Retry-Key": str(uuid.uuid4()),
                },
                json={"to": user.line_user_id, "messages": messages},
                timeout=10,
            )
            if r.status_code == 200:
                n.status = "sent"
            else:
                n.status = "failed"
                n.error = f"HTTP {r.status_code}: {r.text[:500]}"
        except requests.RequestException as e:
            n.status = "failed"
            n.error = str(e)[:500]
    else:
        n.channel = "simulated"
        n.status = "simulated"
    s.add(n)
    return n


# --- 業務通知 ---------------------------------------------------------------

def notify_case_invite(s, c: Case, trainers: list[User]) -> int:
    msg = case_flex(c)
    for u in trainers:
        send(s, u, "case_invite", [msg], f"案件 #{c.id} {c.title}", case_id=c.id)
    return len(trainers)


def notify_hired(s, c: Case, trainer: User, requester: User, org_name: str, trainer_name: str,
                 session_lines: list[str] | None = None, first_hire: bool = True):
    when = "\n".join(session_lines) if session_lines else c.time_slot
    head = "🎉 恭喜錄用！" if first_hire else "📅 主辦方加錄了你的時段"
    tail = "請到平台查看主辦方聯絡方式並主動聯繫。" if first_hire else "請留意行程。"
    from .records import record_url
    tail += f"\n\n📋 賽事當天記錄傷患（防護紀錄表）：\n{record_url(c.id)}"
    send(s, trainer, "hired_trainer", [text(
        f"{head}\n賽會：{c.title}\n主辦方：{org_name}\n錄用時段：\n{when}\n{tail}\n{case_url(c.id)}"
    )], f"錄用通知：案件 #{c.id}", case_id=c.id)
    send(s, requester, "hired_requester", [text(
        f"✅ 已錄用 {trainer_name}\n賽會：{c.title}\n時段：\n{when}\n"
        f"請到平台「我的案件」查看防護員聯絡方式並聯繫。\n{config.app_base_url()}"
    )], f"錄用確認：案件 #{c.id}", case_id=c.id)


def notify_review_result(s, user: User, approved: bool, note: str = ""):
    if approved:
        msg = "✅ 你的防護員資料已審核通過！之後有符合條件的賽會案件，我們會用 LINE 通知你。"
    else:
        msg = f"你的防護員資料需要補件：{note or '請確認證號是否正確'}\n請登入平台修改後重新送審。"
    send(s, user, "review_result", [text(msg)], "審核通過" if approved else "審核退件")


def notify_question(s, c: Case, requester: User):
    send(s, requester, "question", [text(
        f"💬 有防護員對你的案件提問\n賽會：{c.title}\n"
        f"請到平台「我的案件」查看並回覆。\n{config.app_base_url()}"
    )], f"新提問：案件 #{c.id}", case_id=c.id)


def notify_answer(s, c: Case, trainer: User):
    send(s, trainer, "answer", [text(
        f"💬 主辦方回覆了你的提問\n賽會：{c.title}\n{case_url(c.id)}"
    )], f"提問已回覆：案件 #{c.id}", case_id=c.id)
