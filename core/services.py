"""業務流程：發案、應徵、錄用、結案、評價、檢舉、審核。UI 與測試共用。"""
from __future__ import annotations

from sqlalchemy import func, select

from . import fmt, line_api
from .constants import HIGH_RISK_SPORTS
from .db import Application, ApplicationSession, Case, CaseQuestion, CaseSession, Rating, Report, TrainerProfile, User, log, now
from .matching import find_matches, is_full_match
from .privacy import mask_name, scrub_contact
from .validators import sessions_errors


class ActionError(Exception):
    """可直接顯示給使用者的錯誤。"""


# --- 發案 -------------------------------------------------------------------

def publish_case(s, requester: User, sessions: list[tuple], **fields) -> tuple[Case, int]:
    """sessions：[(date, time開始, time結束), ...]，至少一個。"""
    errs = sessions_errors(sessions)
    if errs:
        raise ActionError("；".join(errs))
    sessions = sorted(sessions)
    c = Case(requester_id=requester.id, **fields)
    c.sessions = [CaseSession(date=d, start_time=t1, end_time=t2) for d, t1, t2 in sessions]
    c.event_date, c.event_end_date = sessions[0][0], sessions[-1][0]
    c.time_slot = fmt.time_summary(sessions)
    c.is_high_risk = c.sport_type in HIGH_RISK_SPORTS
    if c.exp_mode == "none":
        c.exp_years = 0
    s.add(c)
    s.flush()
    matched = [u for u, _ in find_matches(s, c)]
    c.notified_count = line_api.notify_case_invite(s, c, matched)
    log(s, requester.id, "case_posted", case_id=c.id, notified=c.notified_count,
        county=c.location_county, sport=c.sport_type)
    return c, c.notified_count


def cancel_case(s, c: Case, requester: User):
    if c.requester_id != requester.id:
        raise ActionError("只能取消自己的案件")
    if c.status != "open":
        raise ActionError("這個案件已經不是招募中")
    c.status = "cancelled"
    c.closed_at = now()
    for a in c.applications:
        if a.status == "applied":
            a.status = "not_selected"
    log(s, requester.id, "case_cancelled", case_id=c.id)


# --- 應徵 -------------------------------------------------------------------

def session_fill(s, c: Case) -> dict[int, int]:
    """每個時段目前已錄用幾人 {session_id: n}。"""
    fill = {x.id: 0 for x in c.sessions}
    for sid, n in s.execute(
        select(ApplicationSession.session_id, func.count(ApplicationSession.id))
        .join(CaseSession, CaseSession.id == ApplicationSession.session_id)
        .where(CaseSession.case_id == c.id, ApplicationSession.status == "hired")
        .group_by(ApplicationSession.session_id)
    ):
        fill[sid] = n
    return fill


def open_session_ids(s, c: Case) -> set[int]:
    """還沒錄滿的時段。"""
    fill = session_fill(s, c)
    return {sid for sid, n in fill.items() if n < c.headcount}


def _check_session_ids(s, c: Case, session_ids) -> set[int]:
    ids = set(session_ids) if session_ids is not None else {x.id for x in c.sessions}
    valid = {x.id for x in c.sessions}
    if not ids:
        raise ActionError("請至少勾選一個可以出席的時段")
    if not ids <= valid:
        raise ActionError("勾選的時段不屬於這個案件")
    full = ids - open_session_ids(s, c)
    if full:
        raise ActionError("有時段已經錄滿，請重新整理後再選")
    return ids


def apply(s, c: Case, trainer: User, session_ids=None) -> Application:
    """session_ids：可以出席的時段；不給就是全部尚未錄滿的時段。"""
    if c.status != "open":
        raise ActionError("本賽事已招募額滿，感謝您的參與")
    prof = s.get(TrainerProfile, trainer.id)
    if not prof or prof.review_status != "approved":
        raise ActionError("你的防護員資料通過審核後才能應徵")
    if session_ids is None:
        session_ids = open_session_ids(s, c)
    ids = _check_session_ids(s, c, session_ids)
    existing = s.scalar(select(Application).where(
        Application.case_id == c.id, Application.trainer_id == trainer.id))
    if existing and existing.status != "withdrawn":
        raise ActionError("你已經應徵過這個案件")
    a = existing or Application(case_id=c.id, trainer_id=trainer.id, status="applied", applied_at=now())
    if existing:
        a.status, a.applied_at = "applied", now()
        a.slots.clear()
        s.flush()
    else:
        s.add(a)
    a.slots.extend(ApplicationSession(session_id=sid, status="available") for sid in sorted(ids))
    s.flush()
    log(s, trainer.id, "apply", case_id=c.id, full_match=is_full_match(prof, c),
        sessions=len(ids), of=len(c.sessions), again=bool(existing))
    return a


def update_availability(s, a: Application, trainer: User, session_ids):
    """修改可出席時段。已錄用的時段不能取消。"""
    if a.trainer_id != trainer.id or a.status not in ("applied", "hired"):
        raise ActionError("目前無法修改這筆應徵")
    c = s.get(Case, a.case_id)
    if c.status != "open":
        raise ActionError("這個案件已經結束招募")
    wanted = set(session_ids)
    hired = a.hired_ids
    if not hired <= wanted:
        raise ActionError("已錄用的時段不能取消，如有變動請直接聯繫主辦方")
    new_ids = wanted - a.available_ids
    if new_ids:
        _check_session_ids(s, c, new_ids)
    if not wanted:
        raise ActionError("請至少勾選一個時段；若都不能出席，請按「取消應徵」")
    for slot in list(a.slots):
        if slot.session_id not in wanted:
            a.slots.remove(slot)
    a.slots.extend(ApplicationSession(session_id=sid, status="available") for sid in sorted(new_ids))
    s.flush()
    log(s, trainer.id, "update_availability", case_id=c.id, sessions=len(wanted))


def withdraw(s, a: Application, trainer: User):
    if a.trainer_id != trainer.id or a.status != "applied":
        raise ActionError("目前無法取消這筆應徵")
    a.status = "withdrawn"
    log(s, trainer.id, "withdraw", case_id=a.case_id)


# --- 我想發問 ---------------------------------------------------------------

QUESTION_MAX_LEN = 300
MAX_PENDING_QUESTIONS = 3  # 同一案件最多 3 則未回覆的提問，避免洗版


def ask_question(s, c: Case, trainer: User, text: str) -> CaseQuestion:
    text = (text or "").strip()
    if c.status != "open":
        raise ActionError("這個案件已經結束招募，無法再提問")
    if c.requester_id == trainer.id:
        raise ActionError("不能對自己的案件提問")
    if not s.get(TrainerProfile, trainer.id):
        raise ActionError("請先填寫履歷再提問")
    if not text:
        raise ActionError("請輸入問題內容")
    if len(text) > QUESTION_MAX_LEN:
        raise ActionError(f"問題請在 {QUESTION_MAX_LEN} 字以內")
    pending = s.scalar(select(func.count(CaseQuestion.id)).where(
        CaseQuestion.case_id == c.id, CaseQuestion.trainer_id == trainer.id, CaseQuestion.answer.is_(None)))
    if pending >= MAX_PENDING_QUESTIONS:
        raise ActionError(f"你在這個案件已有 {pending} 則問題等待回覆，請等主辦方回覆後再提問")
    # 遮掉電話、Email、LINE ID，避免繞過平台私下聯繫
    q = CaseQuestion(case_id=c.id, trainer_id=trainer.id, question=scrub_contact(text))
    s.add(q)
    s.flush()
    line_api.notify_question(s, c, s.get(User, c.requester_id))
    log(s, trainer.id, "ask", case_id=c.id)
    return q


def answer_question(s, q: CaseQuestion, requester: User, text: str, public: bool = True):
    text = (text or "").strip()
    c = s.get(Case, q.case_id)
    if c.requester_id != requester.id:
        raise ActionError("只能回覆自己案件的提問")
    if not text:
        raise ActionError("請輸入回覆內容")
    if len(text) > 1000:
        raise ActionError("回覆請在 1000 字以內")
    first_time = q.answer is None
    q.answer = scrub_contact(text)
    q.is_public = public
    q.answered_at = now()
    if first_time:
        line_api.notify_answer(s, c, s.get(User, q.trainer_id))
    log(s, requester.id, "answer", case_id=c.id, public=public)


def visible_questions(s, c: Case, viewer_id: int) -> list[CaseQuestion]:
    """防護員看得到的問答：自己問的全部 + 主辦方設為公開的（匿名）。"""
    return list(s.scalars(select(CaseQuestion).where(
        CaseQuestion.case_id == c.id,
        (CaseQuestion.trainer_id == viewer_id) | (CaseQuestion.is_public.is_(True) & CaseQuestion.answer.isnot(None)),
    ).order_by(CaseQuestion.created_at)))


# --- 錄用（個資解鎖 + 自動結案） ---------------------------------------------

def hire(s, c: Case, a: Application, requester: User, session_ids=None) -> bool:
    """依時段錄用。session_ids 不給 = 此人可出席、且尚未錄滿的全部時段。
    回傳 True 表示所有時段都已錄滿、案件已自動關閉。"""
    if c.requester_id != requester.id:
        raise ActionError("只能錄用自己案件的應徵者")
    if c.status != "open":
        raise ActionError("這個案件已經關閉")
    if a.case_id != c.id or a.status not in ("applied", "hired"):
        raise ActionError("這位應徵者目前無法錄用")
    open_ids = open_session_ids(s, c)
    candidates = (a.available_ids - a.hired_ids) & open_ids
    ids = set(session_ids) if session_ids is not None else candidates
    if not ids:
        raise ActionError("請至少選一個要錄用的時段")
    if not ids <= a.available_ids:
        raise ActionError("只能錄用這位防護員勾選可以出席的時段")
    if ids & a.hired_ids:
        raise ActionError("有時段已經錄用過這位防護員")
    if not ids <= open_ids:
        raise ActionError("有時段已經錄滿")
    t = now()
    for slot in a.slots:
        if slot.session_id in ids:
            slot.status, slot.hired_at = "hired", t
    first_hire = a.status != "hired"
    a.status = "hired"
    a.hired_at = a.hired_at or t
    s.flush()

    by_id = {x.id: x for x in c.sessions}
    picked = [by_id[i] for i in sorted(ids, key=lambda i: (by_id[i].date, by_id[i].start_time))]
    trainer_user = s.get(User, a.trainer_id)
    prof = s.get(TrainerProfile, a.trainer_id)
    org = requester.requester.org_name if requester.requester else requester.display_name
    line_api.notify_hired(s, c, trainer_user, requester, org, prof.name,
                          [fmt.session_line(x.date, f"{x.start_time:%H:%M}", f"{x.end_time:%H:%M}") for x in picked],
                          first_hire=first_hire)
    log(s, requester.id, "hire", case_id=c.id, trainer_id=a.trainer_id, sessions=len(ids))

    still_open = open_session_ids(s, c)
    # 某位應徵者還沒被錄用、而他可出席的時段都已經被補滿 → 未錄取
    for other in c.applications:
        if other.status == "applied" and not (other.available_ids & still_open):
            other.status = "not_selected"
    if not still_open:
        c.status = "closed"
        c.closed_at = now()
        log(s, requester.id, "case_closed", case_id=c.id)
        return True
    return False


def close_case(s, c: Case, requester: User):
    """人數未滿但發案方決定提前結束招募。"""
    if c.requester_id != requester.id or c.status != "open":
        raise ActionError("目前無法關閉這個案件")
    c.status = "closed"
    c.closed_at = now()
    for other in c.applications:
        if other.status == "applied":
            other.status = "not_selected"
    log(s, requester.id, "case_closed", case_id=c.id, early=True)


# --- 賽後評價 / 檢舉 ----------------------------------------------------------

def rate(s, c: Case, trainer_id: int, requester: User, score: int, comment: str = ""):
    if c.requester_id != requester.id:
        raise ActionError("只能評價自己案件的防護員")
    r = s.scalar(select(Rating).where(Rating.case_id == c.id, Rating.trainer_id == trainer_id))
    if r:
        r.score, r.comment = score, comment
    else:
        s.add(Rating(case_id=c.id, trainer_id=trainer_id, requester_id=requester.id,
                     score=score, comment=comment))
    log(s, requester.id, "rate", case_id=c.id, trainer_id=trainer_id, score=score)


def report(s, c: Case, trainer_id: int, requester: User, reason: str, detail: str):
    if c.requester_id != requester.id:
        raise ActionError("只能檢舉自己案件的防護員")
    s.add(Report(case_id=c.id, trainer_id=trainer_id, reporter_id=requester.id,
                 reason=reason, detail=detail))
    log(s, requester.id, "report", case_id=c.id, trainer_id=trainer_id, reason=reason)


# --- 管理員 -----------------------------------------------------------------

def review_trainer(s, prof: TrainerProfile, approved: bool, note: str = ""):
    prof.review_status = "approved" if approved else "rejected"
    prof.review_note = note or None
    prof.reviewed_at = now()
    line_api.notify_review_result(s, prof.user, approved, note)
    log(s, None, "review", trainer_id=prof.user_id, approved=approved)


def handle_report(s, r: Report, confirm: bool, block: bool = False):
    r.status = "confirmed" if confirm else "dismissed"
    r.handled_at = now()
    if confirm and block:
        set_blocked(s, r.trainer_user, True, f"檢舉 #{r.id} 查證屬實：{r.reason}")


def set_blocked(s, u: User, blocked: bool, reason: str = ""):
    u.is_blocked = blocked
    u.blocked_reason = reason if blocked else None
    log(s, None, "block" if blocked else "unblock", user_id=u.id, reason=reason)


def display_trainer_name(prof: TrainerProfile, unlocked: bool) -> str:
    return prof.name if unlocked else mask_name(prof.name)
