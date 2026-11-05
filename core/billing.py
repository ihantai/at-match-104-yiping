"""付費功能：方案、試用、結帳與使用權判斷。

目前只有 MockProvider（模擬金流），不會真的扣款，用來驗證「願不願意付費」與整個開通流程。
之後要接綠界 / 藍新 / LINE Pay 時，新增一個 Provider 類別實作 start() 與 parse_result()，
並把 BILLING_MODE 改掉即可；訂閱、付款紀錄、權限判斷都不用改。
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select

from . import config
from .constants import PLANS, TRIAL_DAYS
from .db import Payment, Subscription, User, log, now


class BillingError(Exception):
    pass


# --- 金流供應商介面 -------------------------------------------------------------

@dataclass
class CheckoutStart:
    """start() 的結果。真金流會有 redirect_url（導向綠界/藍新頁面）；模擬金流則在頁內完成。"""
    payment: Payment
    redirect_url: str | None = None


class MockProvider:
    name = "mock"
    label = "模擬金流（測試用，不會扣款）"

    def start(self, s, payment: Payment) -> CheckoutStart:
        return CheckoutStart(payment=payment, redirect_url=None)

    def simulate(self, s, payment: Payment, outcome: str) -> Payment:
        """模擬金流回傳結果：paid / failed / cancelled。真金流這一步是收到供應商的通知（webhook）。"""
        return settle(s, payment, outcome)


def provider():
    mode = config.billing_mode()
    if mode == "mock":
        return MockProvider()
    raise BillingError(f"尚未實作的金流：{mode}。目前請使用 BILLING_MODE = \"mock\"")


# --- 使用權 ---------------------------------------------------------------------

def active_subscription(s, user_id: int, at: datetime | None = None) -> Subscription | None:
    at = at or now()
    return s.scalar(
        select(Subscription)
        .where(Subscription.user_id == user_id, Subscription.status == "active",
               Subscription.starts_at <= at, Subscription.ends_at > at)
        .order_by(Subscription.ends_at.desc())
        .limit(1)
    )


def has_access(s, user_id: int) -> bool:
    return active_subscription(s, user_id) is not None


def trial_used(s, user_id: int) -> bool:
    return s.scalar(select(Subscription.id).where(
        Subscription.user_id == user_id, Subscription.source == "trial").limit(1)) is not None


def _extend(s, user_id: int, days: int, plan: str, source: str, payment_id=None, note=None,
            auto_renew=False) -> Subscription:
    """從「現在」或「目前使用權到期日」（取較晚者）開始延長。"""
    cur = active_subscription(s, user_id)
    start = max(now(), cur.ends_at) if cur else now()
    sub = Subscription(user_id=user_id, plan=plan, source=source, status="active", starts_at=start,
                       ends_at=start + timedelta(days=days), payment_id=payment_id, note=note,
                       auto_renew=auto_renew)
    s.add(sub)
    s.flush()
    return sub


def start_trial(s, user: User) -> Subscription:
    if trial_used(s, user.id):
        raise BillingError("每個帳號只能免費試用一次")
    if has_access(s, user.id):
        raise BillingError("你目前已經開通，不需要試用")
    sub = _extend(s, user.id, TRIAL_DAYS, "trial", "trial")
    log(s, user.id, "trial_start", days=TRIAL_DAYS)
    return sub


# --- 結帳 -----------------------------------------------------------------------

def create_checkout(s, user: User, plan: str, method: str) -> CheckoutStart:
    if plan not in PLANS:
        raise BillingError("找不到這個方案")
    p = PLANS[plan]
    trade_no = f"MOCK{now():%Y%m%d%H%M%S}{secrets.token_hex(2).upper()}"
    pay = Payment(user_id=user.id, trade_no=trade_no, plan=plan, amount=p["price"], method=method,
                  provider=config.billing_mode(), status="pending")
    s.add(pay)
    s.flush()
    log(s, user.id, "checkout_start", plan=plan, amount=p["price"], method=method)
    return provider().start(s, pay)


def settle(s, pay: Payment, outcome: str) -> Payment:
    """付款結果入帳。同一筆只會處理一次（重複通知不會重複延長）。"""
    if outcome not in ("paid", "failed", "cancelled"):
        raise BillingError("未知的付款結果")
    if pay.status != "pending":
        return pay
    pay.status = outcome
    if outcome == "paid":
        pay.paid_at = now()
        _extend(s, pay.user_id, PLANS[pay.plan]["days"], pay.plan, "paid", payment_id=pay.id, auto_renew=True)
    log(s, pay.user_id, f"payment_{outcome}", plan=pay.plan, amount=pay.amount, trade_no=pay.trade_no)
    return pay


def cancel_auto_renew(s, user: User):
    sub = active_subscription(s, user.id)
    if not sub or not sub.auto_renew:
        raise BillingError("目前沒有自動續訂的方案")
    sub.auto_renew = False
    log(s, user.id, "auto_renew_off", plan=sub.plan)


# --- 管理員 ---------------------------------------------------------------------

def admin_grant(s, user_id: int, days: int, note: str = "") -> Subscription:
    sub = _extend(s, user_id, days, "admin", "admin", note=note or "管理員開通")
    log(s, None, "admin_grant", user_id=user_id, days=days)
    return sub


def admin_revoke(s, user_id: int):
    t = now()
    for sub in s.scalars(select(Subscription).where(
            Subscription.user_id == user_id, Subscription.status == "active", Subscription.ends_at > t)):
        sub.status = "revoked"
        sub.ends_at = max(sub.starts_at, t)
    log(s, None, "admin_revoke", user_id=user_id)
