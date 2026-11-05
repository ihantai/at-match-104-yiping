"""防護紀錄表：建立、驗證、摘要、通知主辦方、耗材統計。"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select

from . import billing, config, line_api
from .constants import (
    BODY_PARTS, CONSUMABLES, INJURY_TYPES, LATERAL_PARTS, SERIOUS_TREATMENTS, SIDES, TREATMENTS,
)
from .db import Application, Case, InjuryRecord, TrainerProfile, User, log


class RecordError(Exception):
    pass


def record_url(case_id: int | None = None) -> str:
    """LINE 訊息裡「開啟防護紀錄表」的連結：有 LIFF 就走 LIFF（在 LINE 裡免登入），否則走一般網址。"""
    q = "page=record" + (f"&rec_case={case_id}" if case_id else "")
    if config.liff_id():
        return f"https://liff.line.me/{config.liff_id()}?{q}"
    return f"{config.app_base_url()}/?{q}"


def hired_cases(s, trainer_id: int) -> list[Case]:
    """防護員被錄用的案件。今天有時段的排最前面，其次是最近的。"""
    cases = list(s.scalars(
        select(Case).join(Application, Application.case_id == Case.id)
        .where(Application.trainer_id == trainer_id, Application.status == "hired")
    ).unique())
    today = date.today()

    def key(c):
        has_today = any(d == today for d, _, _ in c.schedule)
        return (not has_today, abs((c.event_date - today).days))
    return sorted(cases, key=key)


def validate(data: dict) -> list[str]:
    errs = []
    if not (data.get("athlete") or "").strip():
        errs.append("STEP 1：請填寫選手姓名或背號")
    for bp in data.get("body_parts", []):
        if bp["part"] not in BODY_PARTS:
            errs.append(f"未知的部位：{bp['part']}")
        elif bp["part"] in LATERAL_PARTS and bp.get("side") not in SIDES:
            errs.append(f"STEP 2：請選擇「{bp['part']}」是左、右或雙側")
    types = data.get("injury_types", [])
    if not types:
        errs.append("STEP 2：請至少選一種受傷類型")
    if any(t not in INJURY_TYPES for t in types):
        errs.append("未知的受傷類型")
    if "其他" in types and not (data.get("injury_other") or "").strip():
        errs.append("STEP 2：選了「其他」請簡單說明")
    tr = data.get("treatments", [])
    if not tr:
        errs.append("STEP 3：請至少選一項處置")
    if any(t not in TREATMENTS for t in tr):
        errs.append("未知的處置項目")
    for k, v in (data.get("consumables") or {}).items():
        if k not in {c[0] for c in CONSUMABLES} or not isinstance(v, int) or v < 0 or v > 99:
            errs.append("耗材數量需為 0–99")
            break
    return errs


def create(s, trainer: User, data: dict, notify: bool = True, source: str = "web") -> InjuryRecord:
    if not billing.has_access(s, trainer.id):
        raise RecordError("防護紀錄表是付費功能，請先開通方案")
    prof = s.get(TrainerProfile, trainer.id)
    if not prof:
        raise RecordError("請先填寫防護員履歷")
    errs = validate(data)
    if errs:
        raise RecordError("；".join(errs))
    case = None
    if data.get("case_id"):
        case = s.get(Case, data["case_id"])
        hired = case and s.scalar(select(Application.id).where(
            Application.case_id == case.id, Application.trainer_id == trainer.id,
            Application.status == "hired"))
        if not hired:
            raise RecordError("只能記錄你被錄用的案件")
    event_name = case.title if case else (data.get("event_name") or "").strip()
    if not event_name:
        raise RecordError("STEP 1：請填寫賽事名稱")
    rec = InjuryRecord(
        trainer_id=trainer.id, case_id=case.id if case else None, event_name=event_name,
        venue=(data.get("venue") or "").strip() or None,
        team=(data.get("team") or "").strip(), athlete=data["athlete"].strip(),
        category=(data.get("category") or "").strip(),
        body_parts=[{"part": b["part"], "side": b.get("side")} for b in data.get("body_parts", [])],
        injury_types=list(data["injury_types"]), injury_other=(data.get("injury_other") or "").strip() or None,
        treatments=list(data["treatments"]),
        consumables={k: int(v) for k, v in (data.get("consumables") or {}).items() if int(v) > 0},
        note=(data.get("note") or "").strip(), fill_seconds=data.get("fill_seconds"), source=source,
    )
    s.add(rec)
    s.flush()
    if notify and case:
        requester = s.get(User, case.requester_id)
        line_api.send(s, requester, "injury_record", [line_api.text(summary_text(rec, prof.name))],
                      f"防護紀錄：案件 #{case.id} {rec.athlete}", case_id=case.id)
        rec.notified_requester = True
    log(s, trainer.id, "record_created", case_id=rec.case_id, seconds=rec.fill_seconds, source=source,
        serious=is_serious(rec))
    return rec


def delete(s, rec: InjuryRecord, trainer: User):
    if rec.trainer_id != trainer.id:
        raise RecordError("只能刪除自己的紀錄")
    rec.is_deleted = True
    log(s, trainer.id, "record_deleted", record_id=rec.id)


# --- 顯示 -----------------------------------------------------------------------

def parts_text(rec: InjuryRecord) -> str:
    return "、".join(f"{b['side'] if b.get('side') and b['side'] != '雙側' else ''}{b['part']}"
                     + ("（雙側）" if b.get("side") == "雙側" else "") for b in rec.body_parts) or "—"


def types_text(rec: InjuryRecord) -> str:
    return "、".join(t if t != "其他" else f"其他：{rec.injury_other}" for t in rec.injury_types)


def consumables_text(rec: InjuryRecord) -> str:
    return "、".join(f"{label} {rec.consumables[k]}{unit}" for k, label, unit in CONSUMABLES
                     if rec.consumables.get(k)) or "無"


def is_serious(rec: InjuryRecord) -> bool:
    return bool(SERIOUS_TREATMENTS & set(rec.treatments))


def summary_text(rec: InjuryRecord, trainer_name: str) -> str:
    flag = "🚨 需留意：" + "、".join(t for t in rec.treatments if t in SERIOUS_TREATMENTS) + "\n" \
        if is_serious(rec) else ""
    who = " ".join(x for x in (rec.team, rec.athlete) if x)
    lines = [
        f"📋 防護紀錄｜{rec.event_name}" + (f"（{rec.venue}）" if rec.venue else ""),
        flag + f"時間：{rec.created_at:%m/%d %H:%M}",
        f"選手：{who}" + (f"（{rec.category}）" if rec.category else ""),
        f"部位：{parts_text(rec)}",
        f"狀況：{types_text(rec)}",
        f"處置：{'、'.join(rec.treatments)}",
        f"耗材：{consumables_text(rec)}",
    ]
    if rec.note:
        lines.append(f"備註：{rec.note}")
    lines.append(f"防護員：{trainer_name}")
    return "\n".join(lines)


def consumable_totals(records: list[InjuryRecord]) -> dict[str, int]:
    tot = {k: 0 for k, _, _ in CONSUMABLES}
    for r in records:
        for k, v in (r.consumables or {}).items():
            tot[k] = tot.get(k, 0) + int(v)
    return tot


def to_rows(records: list[InjuryRecord], names: dict[int, str] | None = None) -> list[dict]:
    """匯出用（CSV / 表格）。"""
    names = names or {}
    rows = []
    for r in records:
        row = {
            "時間": r.created_at, "賽事": r.event_name, "場地/時段": r.venue or "", "隊伍/單位": r.team,
            "選手": r.athlete, "項目/類別": r.category, "部位": parts_text(r), "受傷類型": types_text(r),
            "處置": "、".join(r.treatments), "備註": r.note, "防護員": names.get(r.trainer_id, ""),
        }
        for k, label, unit in CONSUMABLES:
            row[f"{label}（{unit}）"] = (r.consumables or {}).get(k, 0)
        rows.append(row)
    return rows
