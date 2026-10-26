"""
เอกสารพนักงาน -- หนังสือเตือน (warning notices) and บันทึกข้อความ (memos), kept as a tracked record per employee (employee_documents, schema_v24.sql).

  draft    the form can still be changed; nothing is numbered
  issued   "ออกเอกสาร": gets its number (SB001/2569 | KK001/2569 = branch + running number + พ.ศ., counted per branch per year) and is FROZEN: `snapshot`
           keeps the employee's name / position, the letterhead, the signer and the loan table as they were that day, so an issued paper never changes
           when the employee record does. A wrong one is cancelled and a new one made (duplicate()).
  signed / refused   the employee's acknowledgement, marked by hand after the paper was printed and signed (refused = would not sign; witnesses)
  cancelled          kept in the history, no longer counted (not in the warning count)

The paper itself (Angsana New 16, title 22) is drawn by static/documents.js and printed from the browser; the owner's signature (a PNG with a clear
background, set once on the documents page) is added only when "ใส่ลายเซ็น" is ticked. Pure rules (clean_document, next_level, number_for ...) are
unit-tested; the database calls are thin. Needs schema_v21 / v23 / v24. Same HR_PIN lock as the rest of พนักงาน.
"""
import base64
import re
import time
import uuid
from datetime import date, datetime
from typing import Optional

from app import employees, loans, supabase

COMPANY = "บริษัท สระบุรีวารีเทพ จำกัด"
LETTERHEADS = {
    "สระบุรี": {"prefix": "SB", "name": f"{COMPANY} (สาขาสระบุรี)", "address": "เลขที่ 72/9 หมู่ 8 ตำบลตลิ่งชัน อำเภอเมือง จังหวัดสระบุรี 18000", "phone": "โทร. 080-030-7033"},
    "แก่งคอย": {"prefix": "KK", "name": f"{COMPANY} (สาขาแก่งคอย)", "address": "เลขที่ 150 หมู่ 8 ตำบลบ้านป่า อำเภอแก่งคอย จังหวัดสระบุรี 18110", "phone": "โทร. 080-030-7033"},
}
SIGNER_NAME = "สิริวัชร์ สิริรัตนชัยกุล"
SIGNER_TITLE = {"warning": "ผู้บริหาร", "memo": "รองผู้จัดการ"}          # as written on the existing papers
MEMO_TO = "คุณเสาวลักษณ์ เทพพิทักษ์"
WARNING_SUBJECT = "การฝ่าฝืนคำสั่ง ระเบียบ หรือข้อบังคับเกี่ยวกับการทำงาน"
TYPES = {"warning": "หนังสือเตือน", "memo": "บันทึกข้อความ"}
LEVELS = {0: "ตักเตือนด้วยวาจา (บันทึกไว้)", 1: "เตือนเป็นลายลักษณ์อักษร ครั้งที่ 1", 2: "เตือนเป็นลายลักษณ์อักษร ครั้งที่ 2", 3: "เตือนครั้งสุดท้าย"}
OFFENCES = ["หยุดงานโดยไม่แจ้งล่วงหน้า", "ขาดความรับผิดชอบในหน้าที่", "เล่นโทรศัพท์ขณะทำงาน", "ดื่มสุราในเวลาทำงาน", "ขับรถโดยประมาท", "มาสาย / ออกก่อนเวลา",
            "ทะเลาะวิวาทในที่ทำงาน", "ทำให้ทรัพย์สินของบริษัทเสียหาย"]
STATUSES = ("draft", "issued", "signed", "refused", "cancelled")
STATUS_LABELS = {"draft": "ร่าง", "issued": "ออกแล้ว · รอเซ็น", "signed": "เซ็นรับทราบแล้ว", "refused": "ปฏิเสธลงนาม", "cancelled": "ยกเลิก"}
NEEDS_V24 = "ยังใช้เอกสารพนักงานไม่ได้ — ต้องรันไฟล์ schema_v24.sql ใน Supabase ก่อน"
MAX_SIGNATURE_BYTES = 400_000

InvalidDocument = type("InvalidDocument", (employees.InvalidEmployee,), {"__doc__": "User-fixable problem; str() is a Thai message safe to show."})
THAI_MONTHS = ["มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน", "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม"]

employees.REFERENCED_BY.append(("employee_documents", "employee_id"))        # an employee with papers on file can only be ลาออก, never deleted


# ------------------------------------------------------------------ pure helpers
def thai_date(iso) -> str:
    """"2026-10-26" -> "26 ตุลาคม 2569"; anything else is returned as it was typed."""
    try:
        d = date.fromisoformat(str(iso)[:10])
    except ValueError:
        return str(iso or "")
    return f"{d.day} {THAI_MONTHS[d.month - 1]} {d.year + 543}"


def number_for(branch: str, year_be: int, seq: int) -> str:
    """SB001/2569 -- the branch's two letters, a three-digit running number, a slash and the Buddhist year."""
    return f"{LETTERHEADS[branch]['prefix']}{seq:03d}/{year_be}"


def next_seq(taken: list[int]) -> int:
    return max(taken, default=0) + 1


def _text(value, limit: int) -> str:
    return re.sub(r"[ \t]+", " ", str(value or "").replace("\r", "")).strip()[:limit]


def _block(value, limit: int) -> str:
    """Several lines kept as lines (a memo's body, an offence written on two lines)."""
    return "\n".join(re.sub(r"[ \t]+", " ", ln).strip() for ln in str(value or "").replace("\r", "").split("\n")).strip()[:limit]


def _money_or_none(value, what: str) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        n = float(str(value).replace(",", "").strip())
    except ValueError:
        raise InvalidDocument(f"{what}ไม่ถูกต้อง")
    if n < 0 or n > 1_000_000:
        raise InvalidDocument(f"{what}ไม่ถูกต้อง")
    return round(n, 2)


def clean_document(data: dict, *, existing: Optional[dict] = None, today: Optional[date] = None) -> dict:
    """The fields to write for a new draft (existing=None) or a change to a draft. Raises InvalidDocument in Thai."""
    today = today or employees.today_th()
    creating = existing is None
    values: dict = {}
    if creating:
        doc_type = _text(data.get("doc_type"), 10)
        if doc_type not in TYPES:
            raise InvalidDocument("เลือกประเภทเอกสาร")
        try:
            values["employee_id"] = str(uuid.UUID(str(data.get("employee_id"))))
        except ValueError:
            raise InvalidDocument("เลือกพนักงานด้วย")
        values["doc_type"] = doc_type
    doc_type = values.get("doc_type") or (existing or {}).get("doc_type")
    if creating or "doc_date" in data:
        try:
            d = date.fromisoformat(str(data.get("doc_date") or today.isoformat())[:10])
        except ValueError:
            raise InvalidDocument("วันที่ไม่ถูกต้อง")
        if d.year < 1950 or d.year >= 2400:
            raise InvalidDocument("วันที่ไม่ถูกต้อง (ปีต้องเป็น ค.ศ. เช่น 2026)")
        if d > today:
            raise InvalidDocument("วันที่เป็นวันในอนาคต")
        values["doc_date"] = d.isoformat()
    if creating or "subject" in data:
        subject = _text(data.get("subject"), 200)
        if not subject and doc_type == "warning":
            subject = WARNING_SUBJECT
        values["subject"] = subject or None
    if "with_signature" in data or creating:
        values["with_signature"] = bool(data.get("with_signature", True))
    if "notes" in data:
        values["notes"] = _text(data.get("notes"), 500) or None
    if "loan_id" in data or creating:
        try:
            values["loan_id"] = str(uuid.UUID(str(data["loan_id"]))) if data.get("loan_id") else None
        except ValueError:
            raise InvalidDocument("เลือกเงินกู้ไม่ถูกต้อง")
    if doc_type == "warning":
        if creating or "warning_level" in data:
            try:
                level = int(str(data.get("warning_level") if data.get("warning_level") not in (None, "") else 1))
            except ValueError:
                raise InvalidDocument("เลือกระดับการเตือน")
            if level not in LEVELS:
                raise InvalidDocument("เลือกระดับการเตือน")
            values["warning_level"] = level
        if creating or "content" in data:
            c = data.get("content") or {}
            # a draft may be half written: what must be filled in is checked when the paper is issued
            values["content"] = {"offence_date": _text(c.get("offence_date"), 60), "offence": _block(c.get("offence"), 800), "punishment": _block(c.get("punishment"), 400),
                                 "penalty_amount": _money_or_none(c.get("penalty_amount"), "ยอดที่หัก"), "show_witnesses": bool(c.get("show_witnesses"))}
    elif doc_type == "memo":
        if creating or "content" in data:
            c = data.get("content") or {}
            values["content"] = {"to": _text(c.get("to") or MEMO_TO, 100), "body": _block(c.get("body"), 6000), "preset": _text(c.get("preset"), 30),
                                 "show_ack": bool(c.get("show_ack"))}
    return values


def next_level(history: list[dict]) -> int:
    """The warning level to suggest for someone: one more than the highest written warning (1 -> 2 -> 3) still counting (not cancelled)."""
    done = [d["warning_level"] for d in history if d.get("doc_type") == "warning" and d.get("status") in ("issued", "signed", "refused") and d.get("warning_level")]
    return min(max(done, default=0) + 1, 3)


def valid_signature(data_uri: str) -> str:
    """The owner's signature: a PNG as a data URI, not bigger than MAX_SIGNATURE_BYTES. Returns it cleaned."""
    m = re.match(r"^data:image/png;base64,([A-Za-z0-9+/=]+)$", str(data_uri or "").strip())
    if not m:
        raise InvalidDocument("ลายเซ็นต้องเป็นไฟล์ภาพ PNG")
    try:
        raw = base64.b64decode(m.group(1), validate=True)
    except ValueError:
        raise InvalidDocument("ไฟล์ลายเซ็นเสีย")
    if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
        raise InvalidDocument("ไฟล์ลายเซ็นไม่ใช่ภาพ PNG")
    if len(raw) > MAX_SIGNATURE_BYTES:
        raise InvalidDocument(f"ไฟล์ลายเซ็นใหญ่เกินไป (ไม่เกิน {MAX_SIGNATURE_BYTES // 1000} KB)")
    return f"data:image/png;base64,{m.group(1)}"


# ------------------------------------------------------------------ the database
_probe = {"at": 0.0, "ok": False}


def has_v24() -> bool:
    now = time.time()
    if now - _probe["at"] < 30:
        return _probe["ok"]
    try:
        supabase.select("employee_documents", {"select": "id", "limit": "1"})
        supabase.select("hr_settings", {"select": "key", "limit": "1"})
        ok = True
    except supabase.SupabaseError:
        ok = False
    _probe.update(at=now, ok=ok)
    return ok


def _require() -> None:
    if not (employees.has_v21() and loans.has_v23() and has_v24()):
        raise InvalidDocument(NEEDS_V24)


def _get(doc_id: str) -> dict:
    try:
        did = str(uuid.UUID(str(doc_id)))
    except ValueError:
        raise employees.NotFound()
    rows = supabase.select("employee_documents", {"select": "*", "id": f"eq.{did}", "limit": "1"})
    if not rows:
        raise employees.NotFound()
    return rows[0]


def _all_docs(employee_id: str = "") -> list[dict]:
    params = {"select": "*", "order": "doc_date.desc,created_at.desc,id.asc"}
    if employee_id:
        params["employee_id"] = f"eq.{employee_id}"
    return supabase.select_all("employee_documents", params)


def get_signature() -> Optional[str]:
    rows = supabase.select("hr_settings", {"select": "value", "key": "eq.signature", "limit": "1"})
    return (rows[0]["value"] or {}).get("data_uri") if rows else None


def set_signature(data_uri: str) -> dict:
    _require()
    supabase.insert("hr_settings", [{"key": "signature", "value": {"data_uri": valid_signature(data_uri)}}], on_conflict="key")
    return {"has_signature": True}


def clear_signature() -> dict:
    _require()
    supabase.delete("hr_settings", {"key": "signature"})
    return {"has_signature": False}


def _snapshot(doc: dict, emp: dict, loan: Optional[dict]) -> dict:
    """What the paper says, as of now: written into the row at issue, and used (live) while it is still a draft."""
    head = LETTERHEADS.get(emp.get("branch") or doc.get("branch")) or LETTERHEADS["สระบุรี"]
    snap = {"employee": {"id": emp["id"], "code": emp["code"], "prefix": emp.get("prefix") or "", "gender": emp.get("gender") or "", "full_name": emp["full_name"],
                         "display_name": employees.display_name(emp.get("prefix"), emp["full_name"]), "position": emp.get("position") or "", "branch": emp["branch"]},
            "letterhead": {k: head[k] for k in ("name", "address", "phone")}, "signer": {"name": SIGNER_NAME, "title": SIGNER_TITLE.get(doc["doc_type"], "")},
            "loan": None}
    if loan:
        s = loans.summarize(loan, [], employees.today_th())
        snap["loan"] = {"id": loan["id"], "kind_label": loans.KINDS.get(loan["kind"], ""), "amount": float(loan["amount"]), "plan": loans.describe(loan),
                        "first_month": str(loan.get("first_month") or "")[:7], "schedule": [{"month": r["month"], "amount": r["amount"]} for r in s["schedule"]]}
    return snap


def _card(doc: dict, emp_by_id: dict, loan_by_id: Optional[dict] = None) -> dict:
    emp = emp_by_id.get(doc["employee_id"]) or {}
    live = doc.get("snapshot") or _snapshot(doc, emp, (loan_by_id or {}).get(doc.get("loan_id"))) if emp else (doc.get("snapshot") or {})
    c = doc.get("content") or {}
    return {"id": doc["id"], "doc_type": doc["doc_type"], "type_label": TYPES.get(doc["doc_type"], ""), "employee_id": doc["employee_id"], "code": emp.get("code") or "",
            "employee": employees.display_name(emp.get("prefix"), emp.get("full_name") or "?"), "branch": doc["branch"], "doc_date": doc["doc_date"], "date_text": thai_date(doc["doc_date"]),
            "subject": doc.get("subject") or "", "warning_level": doc.get("warning_level"), "level_label": LEVELS.get(doc.get("warning_level"), ""), "content": c, "loan_id": doc.get("loan_id"),
            "with_signature": bool(doc.get("with_signature")), "status": doc["status"], "status_label": STATUS_LABELS.get(doc["status"], doc["status"]), "number": doc.get("doc_number") or "",
            "issued_at": doc.get("issued_at"), "acknowledged_date": doc.get("acknowledged_date"), "witnesses": doc.get("witnesses") or "", "notes": doc.get("notes") or "",
            "render": live, "frozen": bool(doc.get("snapshot"))}


def options() -> dict:
    return {"ready": employees.has_v21() and loans.has_v23() and has_v24(), "types": TYPES, "levels": {str(k): v for k, v in LEVELS.items()}, "offences": OFFENCES,
            "statuses": STATUS_LABELS, "memo_to": MEMO_TO, "warning_subject": WARNING_SUBJECT}


def list_documents(employee_id: str = "", doc_type: str = "") -> dict:
    if not (employees.has_v21() and loans.has_v23() and has_v24()):
        return {"ready": False, "documents": [], "has_signature": False}
    emps = {e["id"]: e for e in employees._all()}
    docs = [d for d in _all_docs(employee_id) if not doc_type or d["doc_type"] == doc_type]
    cards = [_card(d, emps) for d in docs]
    for c in cards:
        c.pop("render", None)                                   # the list does not need the whole paper
    return {"ready": True, "documents": cards, "has_signature": bool(get_signature())}


def get_document(doc_id: str) -> dict:
    _require()
    doc = _get(doc_id)
    emps = {e["id"]: e for e in employees._all()}
    loan = None
    if doc.get("loan_id"):
        rows = supabase.select("employee_loans", {"select": "*", "id": f"eq.{doc['loan_id']}", "limit": "1"})
        loan = rows[0] if rows else None
    card = _card(doc, emps, {doc["loan_id"]: loan} if loan else {})
    card["history"] = [{"id": d["id"], "number": d.get("doc_number") or "", "date_text": thai_date(d["doc_date"]), "level": d.get("warning_level"), "status": d["status"],
                        "subject": d.get("subject") or ""} for d in _all_docs(doc["employee_id"]) if d["id"] != doc["id"] and d["doc_type"] == "warning"]
    card["suggest_level"] = next_level([d for d in _all_docs(doc["employee_id"]) if d["id"] != doc["id"]])
    return card


def suggest(employee_id: str) -> dict:
    """For the new-warning form: the level to suggest and the earlier written warnings of this person."""
    _require()
    emp = employees._get(employee_id)
    hist = [d for d in _all_docs(emp["id"])]
    return {"next_level": next_level(hist), "warnings": [{"number": d.get("doc_number") or "", "date_text": thai_date(d["doc_date"]), "level": d.get("warning_level"), "status": d["status"]}
                                                          for d in hist if d["doc_type"] == "warning"]}


def create_document(data: dict) -> dict:
    _require()
    values = clean_document(data)
    emp = employees._get(values["employee_id"])
    if emp.get("status") == "resigned":
        raise InvalidDocument("คนนี้ลาออกแล้ว — ออกเอกสารใหม่ไม่ได้")
    values["branch"] = emp["branch"]
    if values["doc_type"] == "warning" and data.get("warning_level") in (None, ""):
        values["warning_level"] = next_level(_all_docs(emp["id"]))            # not chosen: one more than the last written warning
    values.setdefault("content", {})
    if values.get("loan_id"):
        _check_loan(values["loan_id"], emp["id"])
    row = supabase.insert("employee_documents", [values])[0]
    return get_document(row["id"])


def _check_loan(loan_id: str, employee_id: str) -> None:
    rows = supabase.select("employee_loans", {"select": "employee_id", "id": f"eq.{loan_id}", "limit": "1"})
    if not rows or rows[0]["employee_id"] != employee_id:
        raise InvalidDocument("เงินกู้ที่เลือกไม่ใช่ของพนักงานคนนี้")


def update_document(doc_id: str, data: dict) -> dict:
    _require()
    doc = _get(doc_id)
    if doc["status"] != "draft":
        raise InvalidDocument("ออกเอกสารแล้วแก้ไม่ได้ — ยกเลิกแล้วสร้างฉบับใหม่")
    values = clean_document(data, existing=doc)
    if values.get("loan_id"):
        _check_loan(values["loan_id"], doc["employee_id"])
    if values:
        supabase.update("employee_documents", {"id": f"eq.{doc['id']}"}, values)
    return get_document(doc["id"])


def set_print_options(doc_id: str, data: dict) -> dict:
    """Whether the owner's signature is printed on this paper: a print choice, so it may be changed after the paper is issued."""
    _require()
    doc = _get(doc_id)
    values: dict = {}
    if "with_signature" in data:
        values["with_signature"] = bool(data["with_signature"])
    if "show_witnesses" in data and doc["doc_type"] == "warning" and doc["status"] != "cancelled":
        values["content"] = {**(doc.get("content") or {}), "show_witnesses": bool(data["show_witnesses"])}
    if values:
        supabase.update("employee_documents", {"id": f"eq.{doc['id']}"}, values)
    return get_document(doc["id"])


def issue_document(doc_id: str) -> dict:
    """ออกเอกสาร: the number is given and the paper frozen."""
    _require()
    doc = _get(doc_id)
    if doc["status"] != "draft":
        raise InvalidDocument("ออกเอกสารไปแล้ว")
    content = doc.get("content") or {}
    if doc["doc_type"] == "warning" and not content.get("offence"):
        raise InvalidDocument("ใส่ความผิดที่กระทำก่อนออกเอกสาร")
    if doc["doc_type"] == "warning" and not content.get("punishment"):
        raise InvalidDocument("ใส่การลงโทษก่อนออกเอกสาร (เช่น ตักเตือนเป็นลายลักษณ์อักษร หรือ หักเงิน 500 บาท)")
    if doc["doc_type"] == "memo" and not content.get("body"):
        raise InvalidDocument("ใส่เนื้อหาของบันทึกข้อความก่อนออกเอกสาร")
    emp = employees._get(doc["employee_id"])
    loan = None
    if doc.get("loan_id"):
        rows = supabase.select("employee_loans", {"select": "*", "id": f"eq.{doc['loan_id']}", "limit": "1"})
        loan = rows[0] if rows else None
    year = date.fromisoformat(str(doc["doc_date"])[:10]).year + 543
    for attempt in range(3):
        taken = [d["doc_seq"] for d in supabase.select_all("employee_documents", {"select": "doc_seq", "branch": f"eq.{doc['branch']}", "doc_year": f"eq.{year}", "order": "id.asc"}) if d.get("doc_seq")]
        seq = next_seq(taken)
        try:
            supabase.update("employee_documents", {"id": f"eq.{doc['id']}"},
                            {"status": "issued", "doc_year": year, "doc_seq": seq, "doc_number": number_for(doc["branch"], year, seq), "snapshot": _snapshot(doc, emp, loan),
                             "issued_at": datetime.now(employees.BANGKOK).isoformat()})
            break
        except supabase.SupabaseError:                          # two issued at the same moment: the number was taken, try the next
            if attempt == 2:
                raise
    return get_document(doc["id"])


def acknowledge(doc_id: str, data: dict) -> dict:
    """The employee signed (kind 'signed') or would not (kind 'refused', with the witnesses' names); 'issued' puts it back to waiting."""
    _require()
    doc = _get(doc_id)
    if doc["status"] in ("draft", "cancelled"):
        raise InvalidDocument("ต้องออกเอกสารก่อน (หรือเอกสารถูกยกเลิกแล้ว)")
    kind = _text(data.get("kind"), 10)
    if kind not in ("signed", "refused", "issued"):
        raise InvalidDocument("เลือกผลการรับทราบ")
    today = employees.today_th()
    when = None
    if kind != "issued":
        try:
            when = date.fromisoformat(str(data.get("date") or today.isoformat())[:10])
        except ValueError:
            raise InvalidDocument("วันที่ไม่ถูกต้อง")
        if when > today or when < date.fromisoformat(str(doc["doc_date"])[:10]):
            raise InvalidDocument("วันที่ต้องไม่ก่อนวันที่ในเอกสาร และไม่เป็นวันในอนาคต")
    witnesses = _text(data.get("witnesses"), 200) if kind == "refused" else None
    if kind == "refused" and not witnesses:
        raise InvalidDocument("ใส่ชื่อพยานที่รับรู้การปฏิเสธลงนาม")
    supabase.update("employee_documents", {"id": f"eq.{doc['id']}"}, {"status": kind, "acknowledged_date": when.isoformat() if when else None, "witnesses": witnesses})
    return get_document(doc["id"])


def cancel_document(doc_id: str, data: dict) -> dict:
    _require()
    doc = _get(doc_id)
    if doc["status"] in ("draft", "cancelled"):
        raise InvalidDocument("ร่างให้ลบแทน / ยกเลิกไปแล้ว")
    note = _text(data.get("note"), 300)
    supabase.update("employee_documents", {"id": f"eq.{doc['id']}"}, {"status": "cancelled", "notes": " · ".join(x for x in (doc.get("notes"), f"ยกเลิก: {note}" if note else "ยกเลิก") if x)[:500]})
    return get_document(doc["id"])


def delete_document(doc_id: str) -> None:
    """Only a draft (nothing numbered): an issued paper is cancelled, never removed."""
    _require()
    doc = _get(doc_id)
    if doc["status"] != "draft":
        raise InvalidDocument("ลบได้เฉพาะร่าง — ที่ออกแล้วให้ยกเลิก (เก็บไว้ในประวัติ)")
    supabase.delete("employee_documents", {"id": f"eq.{doc['id']}"})


def duplicate_document(doc_id: str) -> dict:
    """A new draft with the same content (to correct a cancelled paper, or write the next warning)."""
    _require()
    doc = _get(doc_id)
    emp = employees._get(doc["employee_id"])
    if emp.get("status") == "resigned":
        raise InvalidDocument("คนนี้ลาออกแล้ว")
    row = supabase.insert("employee_documents", [{"doc_type": doc["doc_type"], "employee_id": doc["employee_id"], "branch": emp["branch"], "doc_date": employees.today_th().isoformat(),
                                                   "subject": doc.get("subject"), "warning_level": doc.get("warning_level"), "content": doc.get("content") or {},
                                                   "loan_id": doc.get("loan_id"), "with_signature": doc.get("with_signature", True)}])[0]
    return get_document(row["id"])
