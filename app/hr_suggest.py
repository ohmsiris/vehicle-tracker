"""
พนักงาน › จากเอกสารสิ้นเดือน: HR items read from the mid / end-of-month papers (data/acc_docs/*.py, key "hr") wait here until the owner
confirms or skips each one. Nothing changes in พนักงาน without that tap.

  resign      the last day and the reason -> employees.resign (or the date / reason corrected on someone already marked resigned)
  warning     a หนังสือเตือน that was printed and signed outside the app -> a 'signed' warning in employee_documents (level = one more
              than the last), so the warning count is right
  loan        a factory loan / accident deduction -> employee_loans; never a second one: if the person already has an active loan of that
              kind (the balances entered on 30 Sep), it only says so
  loan_terms  a memo changing how a loan is paid back -> that loan's plan

sync_batch is called by the documents import; pending items are rebuilt from the papers, confirmed / skipped ones are kept as they are.
"""
from datetime import datetime, timezone
from typing import Optional

from app import documents, employees, loans, supabase

KINDS = {"resign": "ลาออก / เลิกจ้าง", "warning": "หนังสือเตือน", "loan": "เงินกู้ / หักค่าเสียหาย", "loan_terms": "เปลี่ยนการผ่อน"}
Invalid = type("Invalid", (employees.InvalidEmployee,), {})


def sync_batch(batch: str, items: list[dict]) -> int:
    """Rebuild a batch's pending suggestions from its "hr" list."""
    decided = {(r["page"], r["kind"], r["employee_code"]) for r in supabase.select_all(
        "hr_suggestions", {"select": "page,kind,employee_code", "batch": f"eq.{batch}", "status": "neq.pending", "order": "id.asc"})}
    supabase.delete("hr_suggestions", {"batch": f"eq.{batch}", "status": "eq.pending"})
    rows = []
    for it in items:
        key = (it["page"], it["kind"], it["code"])
        if it["kind"] not in KINDS or key in decided:
            continue
        payload = {k: v for k, v in it.items() if k not in ("page", "kind", "code", "summary")}
        rows.append({"batch": batch, "page": it["page"], "kind": it["kind"], "employee_code": it["code"], "payload": payload, "summary": it.get("summary")})
    if rows:
        supabase.insert("hr_suggestions", rows)
    return len(rows)


def _by_code() -> dict:
    return {e["code"]: e for e in supabase.select_all("employees", {"select": "*", "order": "code.asc"})}


def _active_loans() -> dict:
    out: dict = {}
    for ln in supabase.select_all("employee_loans", {"select": "id,employee_id,kind,amount,repay_mode,repay_amount,first_month,status",
                                                     "status": "eq.active", "order": "loan_date.asc"}):
        out.setdefault(ln["employee_id"], []).append(ln)
    return out


def _doc_ids() -> dict:
    return {(d["batch"], d["page"]): d["id"] for d in supabase.select_all("acc_docs", {"select": "id,batch,page", "order": "id.asc"})}


def state(s: dict, emp: Optional[dict], loans_of: dict) -> str:
    """What is in พนักงาน now, in a few words, so the owner can see what confirming would change."""
    if not emp:
        return "ไม่พบรหัสนี้ในรายชื่อพนักงาน"
    p = s["payload"]
    if s["kind"] == "resign":
        if emp.get("status") == "resigned":
            return f"ในระบบ: ลาออกแล้ว วันสุดท้าย {emp.get('end_date') or 'ไม่ได้ใส่'}" + (" (ตรงกัน)" if emp.get("end_date") == p.get("end_date") else "")
        return "ในระบบ: ยังทำงานอยู่"
    if s["kind"] in ("loan", "loan_terms"):
        same = [ln for ln in loans_of.get(emp["id"], []) if ln["kind"] == p.get("loan_kind", "loan")]
        if not same:
            return "ในระบบ: ยังไม่มีเงินกู้ประเภทนี้"
        return "ในระบบมีอยู่แล้ว: " + " · ".join(f"{float(ln['amount']):,.0f} ({ln['repay_mode']}{' ' + format(float(ln['repay_amount']), ',.0f') if ln.get('repay_amount') else ''})" for ln in same)
    return ""


def list_suggestions() -> dict:
    rows = supabase.select_all("hr_suggestions", {"select": "*", "order": "created_at.desc,page.asc"})
    emps, loans_of, docs = _by_code(), _active_loans(), _doc_ids()
    out = []
    for s in rows:
        emp = emps.get(s["employee_code"])
        out.append({**s, "kind_label": KINDS[s["kind"]], "employee": ({"id": emp["id"], "name": employees.display_name(emp.get("prefix"), emp["full_name"]),
                                                                       "status": emp.get("status")} if emp else None),
                    "now": state(s, emp, loans_of), "doc_id": docs.get((s["batch"], s["page"]))})
    return {"suggestions": out, "pending": sum(1 for s in rows if s["status"] == "pending")}


def _decide(sid: str, status: str, result: str) -> None:
    supabase.update("hr_suggestions", {"id": f"eq.{sid}"}, {"status": status, "result": result[:300], "decided_at": datetime.now(timezone.utc).isoformat()})


def _get(sid: str) -> dict:
    rows = supabase.select("hr_suggestions", {"select": "*", "id": f"eq.{sid}"})
    if not rows:
        raise employees.NotFound()
    if rows[0]["status"] != "pending":
        raise Invalid("รายการนี้ตัดสินไปแล้ว")
    return rows[0]


def dismiss(sid: str) -> dict:
    _get(sid)
    _decide(sid, "dismissed", "ไม่ใช้")
    return list_suggestions()


def apply(sid: str) -> dict:
    s = _get(sid)
    emp = _by_code().get(s["employee_code"])
    if not emp:
        raise Invalid("ไม่พบรหัสนี้ในรายชื่อพนักงาน — เพิ่มพนักงานก่อน หรือกด 'ไม่ใช้'")
    p = s["payload"]
    if s["kind"] == "resign":
        if emp.get("status") == "resigned":
            supabase.update("employees", {"id": f"eq.{emp['id']}"}, {"end_date": p["end_date"], "end_reason": p.get("reason") or emp.get("end_reason")})
            result = f"แก้วันสุดท้ายเป็น {p['end_date']}"
        else:
            employees.resign(emp["id"], {"end_date": p["end_date"], "reason": p.get("reason")})
            result = f"บันทึกลาออก วันสุดท้าย {p['end_date']}"
    elif s["kind"] == "warning":
        history = [d for d in supabase.select_all("employee_documents", {"select": "*", "employee_id": f"eq.{emp['id']}", "order": "doc_date.asc"})]
        if any(d["doc_type"] == "warning" and d["doc_date"] == p["doc_date"] and d["status"] != "cancelled" for d in history):
            result = "มีหนังสือเตือนวันนี้อยู่แล้ว — ไม่ได้เพิ่มซ้ำ"
        else:
            doc = {"doc_type": "warning", "employee_id": emp["id"], "branch": emp["branch"], "doc_date": p["doc_date"],
                   "subject": p.get("subject") or documents.WARNING_SUBJECT, "warning_level": p.get("level") or documents.next_level(history),
                   "content": {"offence_date": p.get("offence_date") or "", "offence": p.get("offence") or "", "punishment": p.get("punishment") or "",
                               "penalty_amount": p.get("penalty_amount"), "show_witnesses": False},
                   "with_signature": False, "status": "signed", "issued_at": f"{p['doc_date']}T09:00:00+07:00", "acknowledged_date": p["doc_date"],
                   "notes": f"บันทึกจากเอกสารสิ้นเดือน ({s['batch']} หน้า {s['page']}) — กระดาษออกนอกโปรแกรม เซ็นแล้ว"}
            doc["snapshot"] = documents._snapshot(doc, emp, None)
            supabase.insert("employee_documents", [doc])
            result = f"เพิ่มหนังสือเตือนครั้งที่ {doc['warning_level']} (เซ็นแล้ว)"
    elif s["kind"] == "loan":
        kind = p.get("loan_kind", "loan")
        same = [ln for ln in _active_loans().get(emp["id"], []) if ln["kind"] == kind]
        if same:
            result = "มีเงินกู้ประเภทนี้อยู่แล้ว — ไม่ได้เพิ่มซ้ำ (แก้ยอดในหน้าเงินกู้ถ้าไม่ตรง)"
        else:
            loans.create_loan({"employee_id": emp["id"], "kind": kind, "amount": p["amount"], "loan_date": p["loan_date"], "reason": p.get("reason"),
                               "memo_ref": f"{s['batch']} หน้า {s['page']}", "repay_mode": "monthly_fixed", "repay_amount": p["repay_amount"],
                               "first_month": p.get("first_month")})
            result = f"เพิ่มเงินกู้ {float(p['amount']):,.0f} ผ่อน {float(p['repay_amount']):,.0f}/เดือน"
    else:   # loan_terms
        same = [ln for ln in _active_loans().get(emp["id"], []) if ln["kind"] == p.get("loan_kind", "loan")]
        if not same:
            raise Invalid("ไม่พบเงินกู้ที่ยังผ่อนอยู่ของคนนี้")
        loans.edit_loan(same[0]["id"], {"repay_mode": "monthly_fixed", "repay_amount": p["repay_amount"], "first_month": p.get("first_month")})
        result = f"เปลี่ยนเป็นผ่อน {float(p['repay_amount']):,.0f}/เดือน เริ่ม {p.get('first_month')}"
    _decide(sid, "applied", result)
    return list_suggestions()
