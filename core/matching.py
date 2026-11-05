"""核心配對引擎與應徵者排序演算法。

配對邏輯（AND 交集）：
    防護員.主要接案縣市 包含 賽會縣市
    AND 防護員.證照 符合 賽會證照要求（含 EMT 要求）
    AND 防護員.動態畢業年資 符合 賽會資歷要求
    AND (若有設定) 防護員.畢業學校 = 指定學校
另外只有「審核通過」且「未被封鎖」的防護員才會被配對。

註：縣市為 JSON 陣列，為了 SQLite / PostgreSQL 都能跑，第一步用 SQL 篩掉
未審核、封鎖與不符學校者，剩下條件在 Python 端判斷。上千位防護員內都很快；
規模變大時可改用 PostgreSQL 的 jsonb ? 運算子或拆成關聯表。
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import func, select

from .config import TZ
from .constants import EMT_LICENSES, EXP_MODES, LICENSE_REQ
from .db import Application, Case, Rating, Report, TrainerProfile, User


def current_year() -> int:
    from datetime import datetime
    return datetime.now(TZ).year


def years_since(graduation_year: int, year: int | None = None) -> int:
    return (year or current_year()) - int(graduation_year)


def _license_ok(p: TrainerProfile, req: str) -> bool:
    first_aid = set(p.first_aid_licenses or [])
    if req == "at":
        return bool(p.has_at_license)
    if req == "pt":
        return bool(p.has_pt_license)
    if req == "at_or_pt":
        return bool(p.has_at_license or p.has_pt_license)
    if req == "first_aid":
        return bool(first_aid)
    return True


def _exp_ok(p: TrainerProfile, mode: str, years: int, year: int | None = None) -> bool:
    y = years_since(p.graduation_year, year)
    if mode == "min":
        return y >= years
    if mode == "max":
        return y <= years
    return True


def criteria_checks(p: TrainerProfile, c: Case, year: int | None = None) -> list[tuple[str, bool]]:
    """回傳每一項條件的 (說明, 是否符合)。只列出發案方有設定的條件。"""
    checks = [(f"可接 {c.location_county} 的案件", c.location_county in (p.service_counties or []))]
    if c.license_req != "none":
        checks.append((LICENSE_REQ[c.license_req], _license_ok(p, c.license_req)))
    if c.require_emt:
        checks.append(("具備 EMT-1 或 EMT-2", bool(EMT_LICENSES & set(p.first_aid_licenses or []))))
    if c.exp_mode != "none":
        label = EXP_MODES[c.exp_mode].replace("X", str(c.exp_years))
        checks.append((label, _exp_ok(p, c.exp_mode, c.exp_years, year)))
    if c.required_school:
        checks.append((f"畢業於 {c.required_school}", p.school == c.required_school))
    return checks


def match_ratio(p: TrainerProfile, c: Case, year: int | None = None) -> float:
    checks = criteria_checks(p, c, year)
    return sum(ok for _, ok in checks) / len(checks)


def is_full_match(p: TrainerProfile, c: Case, year: int | None = None) -> bool:
    return all(ok for _, ok in criteria_checks(p, c, year))


def eligible_trainers_query():
    return (
        select(User, TrainerProfile)
        .join(TrainerProfile, TrainerProfile.user_id == User.id)
        .where(TrainerProfile.review_status == "approved", User.is_blocked.is_(False))
    )


def find_matches(s, c: Case, year: int | None = None) -> list[tuple[User, TrainerProfile]]:
    q = eligible_trainers_query().where(User.id != c.requester_id)
    if c.required_school:
        q = q.where(TrainerProfile.school == c.required_school)
    rows = s.execute(q).all()
    return [(u, p) for u, p in rows if is_full_match(p, c, year)]


def trainer_stats(s, trainer_ids: list[int]) -> dict[int, dict]:
    stats = {tid: {"avg": None, "count": 0, "no_show": 0} for tid in trainer_ids}
    if not trainer_ids:
        return stats
    for tid, avg, cnt in s.execute(
        select(Rating.trainer_id, func.avg(Rating.score), func.count(Rating.id))
        .where(Rating.trainer_id.in_(trainer_ids))
        .group_by(Rating.trainer_id)
    ):
        stats[tid]["avg"] = round(float(avg), 1)
        stats[tid]["count"] = cnt
    for tid, cnt in s.execute(
        select(Report.trainer_id, func.count(Report.id))
        .where(Report.trainer_id.in_(trainer_ids), Report.status == "confirmed")
        .group_by(Report.trainer_id)
    ):
        stats[tid]["no_show"] = cnt
    return stats


# 尚無評價的新人以中性分數計，避免被 2 星的人排在後面
NEUTRAL_RATING = 3.5


def rank_applicants(s, c: Case, year: int | None = None) -> list[dict]:
    """依開發書 4.4 的三層權重排序應徵者：
    1. 條件 100% 吻合者優先（再依吻合比例）
    2. 無放鳥紀錄者優先，再依歷史評分高低
    3. 應徵時間先後（先到先得）
    """
    rows = s.execute(
        select(Application, TrainerProfile)
        .join(TrainerProfile, TrainerProfile.user_id == Application.trainer_id)
        .where(Application.case_id == c.id, Application.status != "withdrawn")
    ).all()
    stats = trainer_stats(s, [a.trainer_id for a, _ in rows])
    out = []
    for app, prof in rows:
        st_ = stats[app.trainer_id]
        ratio = match_ratio(prof, c, year)
        out.append({
            "application": app,
            "profile": prof,
            "checks": criteria_checks(prof, c, year),
            "ratio": ratio,
            "full_match": ratio == 1.0,
            "avg_rating": st_["avg"],
            "rating_count": st_["count"],
            "no_show": st_["no_show"],
            "available_ids": app.available_ids,
            "hired_ids": app.hired_ids,
        })
    out.sort(key=lambda r: (
        not r["full_match"],
        -r["ratio"],
        r["no_show"] > 0,
        -(r["avg_rating"] if r["avg_rating"] is not None else NEUTRAL_RATING),
        r["application"].applied_at,
    ))
    return out


def rank_for_session(s, c: Case, session_id: int, year: int | None = None) -> list[dict]:
    """某一個時段可以出席的應徵者，排序規則同上。"""
    return [r for r in rank_applicants(s, c, year)
            if session_id in r["available_ids"] and r["application"].status in ("applied", "hired")]


def is_event_past(c: Case) -> bool:
    return c.end_date < date.today()
