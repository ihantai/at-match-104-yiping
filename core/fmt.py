"""顯示格式（介面與 LINE 訊息共用）。"""
WEEKDAYS = "一二三四五六日"


def day(d, with_year: bool = True) -> str:
    return f"{d:%Y/%m/%d}（{WEEKDAYS[d.weekday()]}）" if with_year else f"{d:%m/%d}（{WEEKDAYS[d.weekday()]}）"


def session_line(d, t1: str, t2: str, with_year: bool = False) -> str:
    return f"{day(d, with_year)} {t1}-{t2}"


def schedule_summary(c) -> str:
    """一行摘要，依時段型態自動選擇寫法：
    單一時段：2026/11/14（六） 08:00-17:00
    連續多天：2026/11/14（六）～11/16（一），連續 3 天，每日 08:00-17:00
    其他    ：2026/11/14（六）～11/22（日），共 4 個時段（週六、日），每次 08:00-17:00
    """
    sch = c.schedule
    if not sch:
        return "時間未定"
    if len(sch) == 1:
        d, t1, t2 = sch[0]
        return f"{day(d)} {t1}-{t2}"
    dates = sorted({d for d, _, _ in sch})
    first, last = dates[0], dates[-1]
    span = f"{day(first)}～{day(last, with_year=last.year != first.year)}"
    times = {(t1, t2) for _, t1, t2 in sch}
    same_time = len(times) == 1
    t1, t2 = next(iter(times))
    consecutive = (last - first).days + 1 == len(dates) == len(sch)
    if consecutive and same_time:
        return f"{span}，連續 {len(dates)} 天，每日 {t1}-{t2}"
    wds = "、".join(WEEKDAYS[i] for i in sorted({d.weekday() for d in dates}))
    tail = f"，每次 {t1}-{t2}" if same_time else "，各時段時間不同"
    return f"{span}，共 {len(sch)} 個時段（週{wds}）{tail}"


def schedule_lines(c, limit: int | None = None) -> list[str]:
    sch = c.schedule
    shown = sch[:limit] if limit else sch
    lines = [session_line(d, t1, t2) for d, t1, t2 in shown]
    if limit and len(sch) > limit:
        lines.append(f"…還有 {len(sch) - limit} 個時段")
    return lines


def time_summary(sessions) -> str:
    """寫入 Case.time_slot 的摘要字串。"""
    times = {(f"{t1:%H:%M}", f"{t2:%H:%M}") for _, t1, t2 in sessions}
    if len(times) == 1:
        t1, t2 = next(iter(times))
        return f"{t1}-{t2}"
    return "多時段"


def slot_label(x, with_weekday: bool = True) -> str:
    """CaseSession → 11/14（六）08:00-17:00"""
    return session_line(x.date, f"{x.start_time:%H:%M}", f"{x.end_time:%H:%M}") if with_weekday \
        else f"{x.date:%m/%d} {x.start_time:%H:%M}-{x.end_time:%H:%M}"
