"""去識別化工具。

錄用前，發案方畫面只會拿到 public_trainer_view() 的輸出，
這個 dict 從頭到尾不含電話、Email、LINE ID 與完整姓名。
Streamlit 是伺服器端渲染，沒有被 render 的資料不會出現在瀏覽器封包中。
"""
import re

_PHONE = re.compile(r"(?:\+?886[-\s]?|0)9\d{2}[-\s]?\d{3}[-\s]?\d{3}|0\d{1,2}[-\s]?\d{3,4}[-\s]?\d{4}")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_LINE = re.compile(r"(?i)(line\s*(?:id)?\s*[:：]?\s*)@?[\w.-]{3,}")


def mask_name(name: str) -> str:
    """王小明 → 王O明；王明 → 王O；歐陽小明 → 歐OO明；John Smith → J***"""
    name = (name or "").strip()
    if not name:
        return "匿名"
    if re.fullmatch(r"[A-Za-z .'-]+", name):
        return name[0].upper() + "***"
    if len(name) == 1:
        return name + "O"
    if len(name) == 2:
        return name[0] + "O"
    return name[0] + "O" * (len(name) - 2) + name[-1]


def scrub_contact(text: str) -> str:
    """把自由文字裡的電話、Email、LINE ID 遮掉，避免繞過平台私下聯繫。"""
    if not text:
        return ""
    text = _PHONE.sub("［電話已隱藏］", text)
    text = _EMAIL.sub("［Email 已隱藏］", text)
    text = _LINE.sub(r"\1［LINE ID 已隱藏］", text)
    return text


def public_trainer_view(profile, years_since_grad: int) -> dict:
    """錄用前給發案方看的資料（白名單欄位）。"""
    return {
        "masked_name": mask_name(profile.name),
        "gender": profile.gender,
        "school": profile.school,
        "years_since_grad": years_since_grad,
        "has_at": profile.has_at_license,
        "has_pt": profile.has_pt_license,
        "first_aid": list(profile.first_aid_licenses or []),
        "counties": list(profile.service_counties or []),
        "experience": scrub_contact(profile.experience or ""),
    }


def full_trainer_view(profile) -> dict:
    """錄用後解鎖。"""
    return {
        "name": profile.name,
        "phone": profile.phone,
        "email": profile.email,
        "line_contact_id": profile.line_contact_id or "",
    }
