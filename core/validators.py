"""欄位格式檢查（防護員與發案方表單共用）。"""
import re

from .constants import COUNTIES, COUNTY_ALL

LINE_ID_RE = re.compile(r"^@?[A-Za-z0-9._-]{4,20}$")
MOBILE_RE = re.compile(r"^09\d{2}-?\d{3}-?\d{3}$")


def line_id_error(v: str) -> str | None:
    v = (v or "").strip()
    if not v:
        return "請填寫 LINE ID（錄用後讓對方加你好友用）"
    if not LINE_ID_RE.match(v):
        return "LINE ID 格式不正確：4–20 個英文、數字或 . _ -（官方帳號可加 @ 開頭）"
    return None


def license_no_error(label: str, v: str) -> str | None:
    """證號只做基本檢查（長度、不含空白），真偽由管理員到官方系統核對。"""
    v = (v or "").strip()
    if not v:
        return None
    if len(v) < 4 or len(v) > 40 or re.search(r"\s", v):
        return f"{label}格式看起來不對（4–40 字、不含空白）"
    return None


def expand_counties(selected: list[str]) -> list[str]:
    """把「全台（全選）」展開成全部縣市。"""
    if COUNTY_ALL in selected:
        return list(COUNTIES)
    return [c for c in COUNTIES if c in selected]


def collapse_counties(counties: list[str]) -> list[str]:
    """全部縣市都有 → 顯示成「全台（全選）」一個標籤。"""
    if set(counties or []) >= set(COUNTIES):
        return [COUNTY_ALL]
    return list(counties or [])


# --- 賽會時段 -----------------------------------------------------------------

def consecutive_sessions(start, end, t1, t2) -> list[tuple]:
    """連續日期：start～end 每天同一時段。"""
    from datetime import timedelta
    if not start:
        return []
    end = end or start
    return [(start + timedelta(days=i), t1, t2) for i in range((end - start).days + 1)]


def weekly_sessions(start, weeks: int, weekdays: list[int], t1, t2) -> list[tuple]:
    """每週固定：從 start 那週起連續 weeks 週，每週的 weekdays（0=週一…6=週日）。
    例：start=11/14（六）、2 週、[週六, 週日] → 11/14、11/15、11/21、11/22。"""
    from datetime import timedelta
    if not start or not weekdays or weeks < 1:
        return []
    out = []
    for i in range(weeks * 7):
        d = start + timedelta(days=i)
        if d.weekday() in weekdays:
            out.append((d, t1, t2))
    return out


def sessions_errors(sessions: list[tuple], today=None) -> list[str]:
    """檢查時段：不可為空、不可早於今天、結束要晚於開始、同一天不可重疊、數量上限。"""
    from datetime import date as _date

    from .constants import MAX_SESSIONS
    today = today or _date.today()
    errs = []
    if not sessions:
        return ["請至少設定一個時段"]
    if len(sessions) > MAX_SESSIONS:
        errs.append(f"時段最多 {MAX_SESSIONS} 個，賽季較長請分開刊登")
    for d, t1, t2 in sessions:
        label = f"{d:%m/%d}"
        if d < today:
            errs.append(f"{label} 已經過去了")
        if t2 <= t1:
            errs.append(f"{label} 的結束時間要晚於開始時間")
    by_day: dict = {}
    for d, t1, t2 in sorted(sessions):
        prev = by_day.get(d)
        if prev and t1 < prev:
            errs.append(f"{d:%m/%d} 有時段重疊")
        by_day[d] = max(prev or t2, t2)
    return list(dict.fromkeys(errs))  # 去重、保留順序
