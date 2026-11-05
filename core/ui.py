"""共用 UI：色彩、字型與職缺卡片。

視覺方向：人力仲介平台的清楚好掃讀，加上賽會的「號碼布」元素——
每張案件卡左側是一塊號碼布，上面印案件編號，高風險賽事的號碼布為紅色。
"""
from __future__ import annotations

import html

import streamlit as st

from . import fmt
from .constants import LICENSE_REQ

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+TC:wght@400;500;700;900&display=swap');
:root{
  --navy:#14365A; --teal:#0B5E7E; --teal-soft:#E3F0F5; --orange:#E8740C;
  --red:#C62828; --red-soft:#FDECEC; --ink:#1D2B36; --muted:#5E6E7C;
  --line:#DCE3E8; --paper:#FFFFFF; --bg:#F3F6F8; --line-green:#06C755;
}
.stApp{font-family:'Noto Sans TC','PingFang TC','Microsoft JhengHei',sans-serif;}
.block-container{max-width:980px;padding-top:2rem;}
h1,h2,h3{color:var(--navy);letter-spacing:.01em;}
h1{font-weight:900;}

/* 號碼布職缺卡 */
.case{display:flex;gap:16px;align-items:stretch;margin-bottom:6px;}
.bib{flex:0 0 78px;background:var(--paper);border:2px solid var(--navy);border-radius:6px;
  display:flex;flex-direction:column;align-items:center;justify-content:center;position:relative;
  padding:14px 4px 10px;}
.bib::before,.bib::after{content:"";position:absolute;top:6px;width:6px;height:6px;border-radius:50%;
  background:var(--muted);}
.bib::before{left:7px}.bib::after{right:7px}
.bib .no{font-size:.68rem;color:var(--muted);}
.bib .num{font-size:1.55rem;font-weight:900;color:var(--navy);line-height:1.1;font-variant-numeric:tabular-nums;}
.bib.risk{border-color:var(--red);background:var(--red-soft);}
.bib.risk .num{color:var(--red);}
.case .main{flex:1;min-width:0;}
.case .title{font-size:1.12rem;font-weight:700;color:var(--ink);margin:0 0 4px;line-height:1.4;}
.case .meta{color:var(--muted);font-size:.92rem;line-height:1.7;}
.case .pay{color:var(--orange);font-weight:900;font-size:1.15rem;white-space:nowrap;}
.case .side{text-align:right;display:flex;flex-direction:column;justify-content:space-between;gap:6px;}

.tag{display:inline-block;padding:1px 9px;border-radius:999px;font-size:.8rem;margin:3px 4px 0 0;
  background:var(--teal-soft);color:var(--teal);font-weight:500;}
.tag.warn{background:var(--red-soft);color:var(--red);}
.tag.ok{background:#E6F4EA;color:#1E7B3A;}
.tag.grey{background:#EDF0F2;color:var(--muted);}
.tag.orange{background:#FDF0E3;color:#A6520A;}

.notice{border-left:4px solid var(--teal);background:var(--teal-soft);padding:12px 16px;border-radius:4px;
  color:var(--ink);margin:6px 0 14px;}
.notice.warn{border-color:var(--red);background:var(--red-soft);}

/* 首頁 */
.hero h1{font-size:2.3rem;line-height:1.3;margin:0 0 .4rem;}
.hero p{font-size:1.08rem;color:var(--muted);max-width:36em;}
.steps{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:8px 0 4px;}
.step{border-top:3px solid var(--navy);padding:10px 2px 0;}
.step b{display:block;color:var(--navy);margin-bottom:2px;}
.step span{color:var(--muted);font-size:.9rem;}
@media (max-width:640px){
  .stApp h1{font-size:1.75rem;}
  .steps{grid-template-columns:repeat(2,1fr);}
  .hero h1{font-size:1.7rem;}
  .case{gap:10px;flex-wrap:wrap;}
  .bib{flex-basis:62px;}
  .case .side{text-align:left;flex-direction:row;align-items:center;width:100%;}
}

a.line-btn{display:inline-flex;align-items:center;gap:8px;background:var(--line-green);color:#fff!important;
  font-weight:700;padding:10px 22px;border-radius:8px;text-decoration:none!important;font-size:1rem;}
a.line-btn:hover{filter:brightness(.95);}
a.line-btn:focus-visible{outline:3px solid var(--navy);outline-offset:2px;}

.slots{display:grid;grid-template-columns:repeat(auto-fill,minmax(112px,1fr));gap:8px;margin:6px 0 12px;}
.slot{border:1px solid var(--line);border-top:3px solid var(--teal);border-radius:6px;padding:6px 8px;
  display:flex;flex-direction:column;line-height:1.35;background:var(--paper);}
.slot b{color:var(--navy);font-size:1.02rem;font-variant-numeric:tabular-nums;}
.slot span{color:var(--muted);font-size:.82rem;}
.slot em{font-style:normal;color:var(--ink);font-size:.86rem;font-variant-numeric:tabular-nums;}
.slot.done{border-top-color:#1E7B3A;background:#F3FAF5;}
.slot.done em{color:#1E7B3A;font-weight:700;}
/* 防護紀錄表：手機上「儲存並發送」固定在畫面底部，耗材計數器維持 2×2 */
.st-key-rec_footer button{min-height:3rem;font-size:1.05rem;font-weight:700;}
.st-key-rec_cons [data-testid="stHorizontalBlock"]{flex-wrap:nowrap;gap:.75rem;}
.st-key-rec_cons [data-testid="stColumn"]{min-width:0;width:auto;flex:1 1 0;}
@media (max-width:640px){
  .st-key-rec_footer{position:fixed;left:0;right:0;bottom:0;z-index:999990;background:var(--paper);
    padding:10px 16px calc(10px + env(safe-area-inset-bottom,0px));border-top:1px solid var(--line);
    box-shadow:0 -4px 12px rgba(20,54,90,.08);}
  .stMain:has(.st-key-rec_footer) .block-container{padding-bottom:7rem;}
}
.contact{border:2px dashed var(--orange);border-radius:8px;padding:10px 14px;background:#FFFAF4;}
.locked{color:var(--muted);font-size:.9rem;}
</style>
"""


def inject_css():
    st.markdown(CSS, unsafe_allow_html=True)


def e(x) -> str:
    return html.escape(str(x if x is not None else ""))


def tag(text: str, kind: str = "") -> str:
    return f'<span class="tag {kind}">{e(text)}</span>'


def notice(text_html: str, warn: bool = False):
    st.markdown(f'<div class="notice {"warn" if warn else ""}">{text_html}</div>', unsafe_allow_html=True)


CASE_STATUS = {"open": ("招募中", "ok"), "closed": ("已結案", "grey"), "cancelled": ("已取消", "grey")}
APP_STATUS = {
    "applied": ("已應徵，等待主辦方挑選", "orange"),
    "hired": ("已錄用", "ok"),
    "not_selected": ("未錄取", "grey"),
    "withdrawn": ("已取消應徵", "grey"),
}
REVIEW_STATUS = {
    "pending": ("審核中", "orange"),
    "approved": ("審核通過", "ok"),
    "rejected": ("需補件", "warn"),
}


def case_card(c, extra_tags: list[str] | None = None, show_status: bool = True):
    tags = [tag(c.sport_type)]
    if c.is_high_risk:
        tags.append(tag("⚠ 高風險接觸性運動", "warn"))
    if c.license_req != "none":
        tags.append(tag(LICENSE_REQ[c.license_req]))
    if c.require_emt:
        tags.append(tag("需 EMT"))
    if c.exp_mode == "min":
        tags.append(tag(f"畢業 {c.exp_years} 年以上"))
    elif c.exp_mode == "max":
        tags.append(tag(f"畢業 {c.exp_years} 年以內"))
    if c.required_school:
        tags.append(tag(f"限 {c.required_school}"))
    if c.headcount > 1:
        tags.append(tag(f"需 {c.headcount} 人", "orange"))
    tags += extra_tags or []
    status = ""
    if show_status:
        label, kind = CASE_STATUS.get(c.status, (c.status, "grey"))
        status = tag(label, kind)
    st.markdown(f"""
<div class="case">
  <div class="bib {'risk' if c.is_high_risk else ''}" aria-label="案件編號 {c.id}">
    <span class="no">No.</span><span class="num">{c.id:03d}</span>
  </div>
  <div class="main">
    <p class="title">{e(c.title)}</p>
    <div class="meta">📅 {e(fmt.schedule_summary(c))}<br>
    📍 {e(c.location_county)}</div>
    <div>{''.join(tags)}</div>
  </div>
  <div class="side"><span class="pay">{e(c.salary_type)} ${c.salary_amount:,}</span><span>{status}</span></div>
</div>""", unsafe_allow_html=True)


def schedule_table(c):
    """多時段明細：以小卡格狀排列，一眼看出哪幾天、幾點。"""
    cells = "".join(
        f'<div class="slot"><b>{d:%m/%d}</b><span>週{fmt.WEEKDAYS[d.weekday()]}</span><em>{e(t1)}–{e(t2)}</em></div>'
        for d, t1, t2 in c.schedule)
    st.markdown(f'<div class="slots" aria-label="賽會時段明細">{cells}</div>', unsafe_allow_html=True)


def fill_grid(c, fill: dict, avail: dict):
    """發案方看的時段總覽：每個時段錄用幾人、幾人可出席。"""
    cells = []
    for x in c.sessions:
        n = fill.get(x.id, 0)
        done = n >= c.headcount
        cells.append(
            f'<div class="slot {"done" if done else ""}"><b>{x.date:%m/%d}</b><span>週{fmt.WEEKDAYS[x.date.weekday()]}'
            f' {x.start_time:%H:%M}–{x.end_time:%H:%M}</span>'
            f'<em>{"✓ 已錄滿" if done else f"已錄 {n}/{c.headcount}"}</em>'
            f'<span>{avail.get(x.id, 0)} 人可出席</span></div>')
    st.markdown(f'<div class="slots" aria-label="各時段錄用狀況">{"".join(cells)}</div>', unsafe_allow_html=True)


def line_button(url: str, label: str = "用 LINE 登入"):
    st.markdown(f'<a class="line-btn" href="{e(url)}" target="_blank" rel="noopener">'
                f'<b>LINE</b> {e(label)}</a>', unsafe_allow_html=True)


def empty(text: str):
    st.markdown(f'<div class="notice">{text}</div>', unsafe_allow_html=True)


def flash(msg: str, kind: str = "success"):
    """跨 rerun 的提示訊息（st.rerun 後一般的 st.success 會消失）。"""
    st.session_state["_flash"] = (kind, msg)


def show_flash():
    item = st.session_state.pop("_flash", None)
    if item:
        kind, msg = item
        getattr(st, kind)(msg)
