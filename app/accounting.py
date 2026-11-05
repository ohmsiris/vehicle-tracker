"""
Accounting (บัญชี) -- Saraburi first. Needs schema_v41.sql (acc_accounts, acc_entries, acc_lines, acc_feed_marks).

The owner's rules (Nov 2026):
* ACCRUAL, standard practice: a cost is booked on the day the goods arrived / the work was done, never on the day it was paid.
  Double entry underneath (expense + input VAT = owed to the shop; a payment clears what is owed); plain words on screen.
* A cost is created ONCE, where the work or goods are recorded (no single inbox): truck repair bills and factory purchase bills
  are already in the app, so their entries are rebuilt from them (source truck_bill / purchase); everything else comes from the
  documents the owner and HQ provide (electricity, water, mid / end-of-month sheets, HQ cheques and salaries, fuel ...).
* The branch that USED it carries the cost (a truck's / machine's own branch); small lending between branches is ignored.
* VAT: with a tax invoice the VAT goes to Input VAT (1400), not the cost; without one the whole amount is the cost.
* The company month runs from the 26th to the 25th and is named after the 25th ("2026-11" = 26 Oct - 25 Nov), cut in two
  halves: 1 = 26th - 10th, 2 = 11th - 25th. Monthly documents (electricity, HQ cheques ...) belong to the second half.

Pure functions (period_of, period_bounds, truck_entry, purchase_entry, manual_entry, pnl_tree, feed_status) are unit-tested.
"""
import re
import time
import uuid
from datetime import date, timedelta
from typing import Optional

from app import supabase

NEEDS_V41 = "ต้องรัน schema_v41.sql ใน Supabase ก่อน จึงจะใช้บัญชีได้"
PROGRAM_SOURCES = ("truck_bill", "purchase")
SECTIONS = [("revenue", "Revenue", "รายได้"), ("cogs", "Cost of goods sold", "ต้นทุนขาย"),
            ("selling", "Selling & delivery expenses", "ค่าใช้จ่ายในการขายและขนส่ง"), ("admin", "Administrative expenses", "ค่าใช้จ่ายในการบริหาร")]
PAID_FROM = {"ap": "2100", "cash": "1100", "company": "1110", "mum": "1120", "hq": "1130"}
BRANCHES = ("สระบุรี", "แก่งคอย")
VAT_LINE = re.compile(r"^\s*(?:vat|ภาษีมูลค่าเพิ่ม)", re.I)

# What should come in every half-month (the "still missing" list). from: program = already in the app (automatic) / you / hq.
FEEDS = [
    {"key": "truck_bill", "from": "program", "half": None, "en": "Truck repair bills", "th": "บิลซ่อมรถ", "account": None},
    {"key": "purchase", "from": "program", "half": None, "en": "Factory purchase bills", "th": "บิลซื้อของโรงงาน", "account": None},
    {"key": "midmonth", "from": "you", "half": 1, "en": "Mid-month expense documents", "th": "เอกสารเบิกกลางเดือน", "account": "7900"},
    {"key": "endmonth", "from": "you", "half": 2, "en": "End-of-month expense documents", "th": "เอกสารเบิกสิ้นเดือน", "account": "7900"},
    {"key": "electricity", "from": "you", "half": 2, "en": "Electricity (PEA)", "th": "ค่าไฟฟ้า (PEA)", "account": "5100"},
    {"key": "water_irrigation", "from": "you", "half": 2, "en": "Water - irrigation", "th": "ค่าน้ำชลประทาน", "account": "5200"},
    {"key": "water_city", "from": "you", "half": 2, "en": "Water - waterworks (if used)", "th": "ค่าน้ำประปา (ถ้าใช้)", "account": "5210"},
    {"key": "fuel_card", "from": "you", "half": 2, "en": "Fuel card (Bangchak / PT)", "th": "น้ำมันบัตร (บางจาก / PT)", "account": "6120"},
    {"key": "hq_cheques", "from": "hq", "half": 2, "en": "HQ cheques (salt, chemicals, bags)", "th": "เช็คจาก HQ (เกลือ เคมี ถุง)", "account": "5300"},
    {"key": "hq_salary", "from": "hq", "half": 2, "en": "Salaries (HQ)", "th": "เงินเดือน (HQ)", "account": "5400"},
]
FEED_BY_KEY = {f["key"]: f for f in FEEDS}
_probe = {"at": 0.0, "ok": False}


class InvalidEntry(ValueError):
    """User-fixable problem; str() is a Thai message safe to show."""


def has_v41() -> bool:
    now = time.time()
    if now - _probe["at"] < 30:
        return _probe["ok"]
    try:
        supabase.select("acc_accounts", {"select": "code", "limit": "1"})
        ok = True
    except supabase.SupabaseError:
        ok = False
    _probe.update(at=now, ok=ok)
    return ok


def _require() -> None:
    if not has_v41():
        raise InvalidEntry(NEEDS_V41)


# ------------------------------------------------------------------ periods
def _add_month(y: int, m: int, n: int) -> tuple[int, int]:
    t = y * 12 + (m - 1) + n
    return t // 12, t % 12 + 1


def period_of(d: date) -> dict:
    """The company period a day falls in: month named after the 25th, half 1 (26th - 10th) or 2 (11th - 25th)."""
    if d.day >= 26:
        y, m = _add_month(d.year, d.month, 1)
        half = 1
    else:
        y, m = d.year, d.month
        half = 1 if d.day <= 10 else 2
    return {"month": f"{y}-{m:02d}", "half": half}


def period_bounds(month: str, half: Optional[int] = None) -> tuple[date, date]:
    """First and last day of a company month ("2026-11" = 26 Oct - 25 Nov) or of one of its halves."""
    y, m = int(month[:4]), int(month[5:7])
    py, pm = _add_month(y, m, -1)
    start, end = date(py, pm, 26), date(y, m, 25)
    if half == 1:
        return start, date(y, m, 10)
    if half == 2:
        return date(y, m, 11), end
    return start, end


# ------------------------------------------------------------------ entries from the app (pure)
def _money(x) -> float:
    try:
        return round(float(x or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _line(account: str, debit: float = 0.0, credit: float = 0.0, **extra) -> dict:
    return {"account": account, "debit": round(debit, 2), "credit": round(credit, 2), **extra}


def truck_entry(event: dict, lines: list[dict], vehicle: Optional[dict]) -> Optional[dict]:
    """A saved truck repair bill -> one entry: repairs (6100) and tyres (6110) for the truck's own branch, the VAT lines to Input VAT,
    owed to the shop (2100). None when the bill has no amount (an old dealer-history visit, for example)."""
    from app.tyres import is_tyre_line
    vat = sum(_money(ln.get("line_total")) for ln in lines if VAT_LINE.search(str(ln.get("description") or "")))
    rest = [ln for ln in lines if not VAT_LINE.search(str(ln.get("description") or ""))]
    tyres = sum(_money(ln.get("line_total")) for ln in rest if is_tyre_line(ln.get("description")))
    total = _money(event.get("total_cost")) or round(sum(_money(ln.get("line_total")) for ln in rest) + vat, 2)
    if total <= 0:
        return None
    vat = min(vat, total)
    tyres = min(tyres, total - vat)
    other = round(total - vat - tyres, 2)
    branch = (vehicle or {}).get("branch") or event.get("branch")
    vid = (vehicle or {}).get("id") or event.get("vehicle_id")
    out_lines = []
    if other:
        out_lines.append(_line("6100", other, branch=branch, vehicle_id=vid))
    if tyres:
        out_lines.append(_line("6110", tyres, branch=branch, vehicle_id=vid))
    if vat:
        out_lines.append(_line("1400", vat, branch=branch, vehicle_id=vid))
    out_lines.append(_line("2100", credit=total, branch=branch, vehicle_id=vid))
    label = " · ".join(x for x in (f"#{vehicle['vehicle_number']}" if vehicle and vehicle.get("vehicle_number") else "", (vehicle or {}).get("plate_number") or "") if x)
    return {"entry_date": str(event["event_date"])[:10], "branch": branch, "source": "truck_bill", "source_id": event["id"],
            "party": event.get("vendor") or event.get("performed_by") or "ซ่อมเอง", "description": f"ซ่อมรถ {label}".strip() + (f" — {event['summary']}" if event.get("summary") else ""),
            "doc_type": "tax_invoice" if vat else "bill", "lines": out_lines}


def purchase_entry(bill: dict) -> Optional[dict]:
    """A factory purchase bill -> one entry: machine repair (5500) or parts & supplies (5510), VAT to Input VAT, owed to the shop.
    A reference-only bill (a price list, no money) makes no entry."""
    if bill.get("purpose") == "reference":
        return None
    total = _money(bill.get("total"))
    if total <= 0:
        return None
    vat = _money(bill.get("vat_amount")) if bill.get("vat_mode") in ("excluded", "included") else 0.0
    vat = min(vat, total)
    account = "5500" if bill.get("purpose") == "repair" else "5510"
    branch = bill.get("branch")
    lines = [_line(account, round(total - vat, 2), branch=branch, machine_id=bill.get("machine_id"))]
    if vat:
        lines.append(_line("1400", vat, branch=branch))
    lines.append(_line("2100", credit=total, branch=branch))
    return {"entry_date": str(bill["bill_date"])[:10], "branch": branch, "source": "purchase", "source_id": bill["id"], "party": bill.get("vendor_name"),
            "description": "ซ่อมเครื่องจักร" if bill.get("purpose") == "repair" else "ซื้ออะไหล่ / วัสดุโรงงาน", "doc_type": "tax_invoice" if vat else "bill",
            "doc_no": bill.get("bill_no"), "lines": lines}


def manual_entry(data: dict, *, accounts: dict, today: date) -> dict:
    """A cost typed in (electricity, water, a document that is not in the app yet ...): amount, VAT if there is a tax invoice, and
    where it was paid from (or still owed)."""
    try:
        d = date.fromisoformat(str(data.get("entry_date") or "")[:10])
    except ValueError:
        raise InvalidEntry("ใส่วันที่ (วันที่ของมาถึง / วันที่ใช้)")
    if d.year >= 2400:
        raise InvalidEntry("วันที่: ใส่ปี ค.ศ.")
    if d > today + timedelta(days=31):
        raise InvalidEntry("วันที่อยู่ไกลในอนาคตเกินไป")
    acc = str(data.get("account") or "")
    if acc not in accounts or accounts[acc]["kind"] not in ("cogs", "expense", "income"):
        raise InvalidEntry("เลือกหมวดบัญชี")
    branch = data.get("branch")
    if branch not in BRANCHES:
        raise InvalidEntry("เลือกสาขา")
    try:
        amount = round(float(str(data.get("amount") or "").replace(",", "")), 2)
        vat = round(float(str(data.get("vat") or 0).replace(",", "") or 0), 2)
    except ValueError:
        raise InvalidEntry("จำนวนเงินต้องเป็นตัวเลข")
    if amount <= 0:
        raise InvalidEntry("ใส่จำนวนเงิน (ก่อน VAT)")
    if vat < 0 or vat > amount:
        raise InvalidEntry("VAT ไม่ถูกต้อง")
    paid = data.get("paid_from") or "ap"
    if paid not in PAID_FROM:
        raise InvalidEntry("เลือกว่าจ่ายจากไหน")
    feed = data.get("feed") if data.get("feed") in FEED_BY_KEY else "manual"
    income = accounts[acc]["kind"] == "income"
    total = round(amount + vat, 2)
    if income:
        lines = [_line(PAID_FROM[paid] if paid != "ap" else "1100", total, branch=branch), _line(acc, credit=total, branch=branch)]
    else:
        lines = [_line(acc, amount, branch=branch)] + ([_line("1400", vat, branch=branch)] if vat else []) + [_line(PAID_FROM[paid], credit=total, branch=branch)]
    return {"entry_date": d.isoformat(), "branch": branch, "source": feed, "source_id": str(uuid.uuid4()), "party": (str(data.get("party") or "").strip()[:100] or None),
            "description": (str(data.get("description") or "").strip()[:300] or None), "doc_type": data.get("doc_type") or ("tax_invoice" if vat else "receipt"),
            "doc_no": (str(data.get("doc_no") or "").strip()[:60] or None), "lines": lines}


# ------------------------------------------------------------------ reports (pure)
def pnl_tree(rows: list[dict], accounts: dict) -> dict:
    """The P&L as a tree you can open level by level: section -> account -> party (shop) -> each entry (bill).
    `rows` = acc_lines joined with their entry: {account, debit, credit, entry: {...}}. Expense = debit - credit; income = credit - debit."""
    sections: dict = {}
    for r in rows:
        a = accounts.get(r["account"])
        if not a or a["kind"] not in ("income", "cogs", "expense"):
            continue
        amt = (_money(r["credit"]) - _money(r["debit"])) if a["kind"] == "income" else (_money(r["debit"]) - _money(r["credit"]))
        sec = sections.setdefault(a["section"], {"total": 0.0, "accounts": {}})
        acc = sec["accounts"].setdefault(a["code"], {"code": a["code"], "en": a["name_en"], "th": a["name_th"], "total": 0.0, "parties": {}})
        e = r["entry"]
        party = e.get("party") or "(ไม่ระบุ)"
        p = acc["parties"].setdefault(party, {"party": party, "total": 0.0, "entries": []})
        p["entries"].append({"id": e["id"], "date": e["entry_date"], "source": e["source"], "source_id": e.get("source_id"), "description": e.get("description"),
                             "doc_type": e.get("doc_type"), "doc_no": e.get("doc_no"), "branch": r.get("branch") or e.get("branch"), "amount": round(amt, 2)})
        for x in (sec, acc, p):
            x["total"] = round(x["total"] + amt, 2)
    out = []
    for key, en, th in SECTIONS:
        s = sections.get(key, {"total": 0.0, "accounts": {}})
        accs = sorted(s["accounts"].values(), key=lambda a: accounts[a["code"]]["sort_order"])
        for a in accs:
            a["parties"] = sorted(a["parties"].values(), key=lambda p: -abs(p["total"]))
            for p in a["parties"]:
                p["entries"].sort(key=lambda x: (x["date"], x["description"] or ""))
        out.append({"key": key, "en": en, "th": th, "total": round(s["total"], 2), "accounts": accs})
    tot = {s["key"]: s["total"] for s in out}
    gross = round(tot["revenue"] - tot["cogs"], 2)
    return {"sections": out, "revenue": tot["revenue"], "cogs": tot["cogs"], "gross_profit": gross,
            "expenses": round(tot["selling"] + tot["admin"], 2), "net_profit": round(gross - tot["selling"] - tot["admin"], 2)}


def feed_status(half: Optional[int], counts: dict, marks: dict, missing_amount: int = 0) -> list[dict]:
    """The checklist: for each expected feed, whether this period has it. counts = {feed: (entries, total)}; marks = {feed: status}."""
    out = []
    for f in FEEDS:
        if half and f["half"] and f["half"] != half:
            continue
        n, total = counts.get(f["key"], (0, 0.0))
        mark = marks.get(f["key"])
        state = "auto" if f["from"] == "program" else "done" if n or mark == "done" else "none" if mark == "none" else "missing"
        out.append({**f, "entries": n, "total": round(total, 2), "state": state, "mark": mark,
                    "note": f"บิล {missing_amount} ใบไม่มียอดเงิน — ไม่ได้นับ" if f["key"] == "truck_bill" and missing_amount else None})
    return out


# ------------------------------------------------------------------ database
def _accounts() -> dict:
    return {a["code"]: a for a in supabase.select_all("acc_accounts", {"select": "*", "order": "sort_order.asc"})}


def _insert_entries(entries: list[dict]) -> None:
    if not entries:
        return
    heads, lines = [], []
    for e in entries:
        eid = str(uuid.uuid4())
        heads.append({k: e.get(k) for k in ("entry_date", "branch", "source", "source_id", "party", "description", "doc_type", "doc_no")} | {"id": eid})
        for ln in e["lines"]:
            lines.append({"entry_id": eid, "account": ln["account"], "debit": ln["debit"], "credit": ln["credit"], "branch": ln.get("branch"),
                          "vehicle_id": ln.get("vehicle_id"), "machine_id": ln.get("machine_id"), "memo": ln.get("memo")})
    for i in range(0, len(heads), 200):
        supabase.insert("acc_entries", heads[i:i + 200])
    for i in range(0, len(lines), 400):
        supabase.insert("acc_lines", lines[i:i + 400])


def sync_program(start: date, end: date) -> int:
    """Rebuild the entries of the bills already in the app (truck repairs, factory purchases) for these days -- so the books always
    match the app, edits and deletes included. Typed / imported entries are not touched."""
    _require()
    lo, hi = start.isoformat(), end.isoformat()
    events = supabase.select_all("maintenance_events", {"select": "id,vehicle_id,event_date,vendor,performed_by,branch,total_cost,summary",
                                                        "status": "eq.confirmed", "event_date": f"gte.{lo}", "and": f"(event_date.lte.{hi})", "order": "id.asc"})
    lines_by: dict = {}
    if events:
        for i in range(0, len(events), 100):
            ids = ",".join(e["id"] for e in events[i:i + 100])
            for ln in supabase.select_all("bill_line_items", {"select": "event_id,description,line_total", "event_id": f"in.({ids})", "order": "id.asc"}):
                lines_by.setdefault(ln["event_id"], []).append(ln)
    vehicles = {v["id"]: v for v in supabase.select_all("vehicles", {"select": "id,branch,vehicle_number,plate_number", "order": "id.asc"})}
    bills = supabase.select_all("purchase_bills", {"select": "id,vendor_name,bill_date,bill_no,branch,vat_mode,vat_amount,total,purpose,machine_id",
                                                   "bill_date": f"gte.{lo}", "and": f"(bill_date.lte.{hi})", "order": "id.asc"})
    entries = [x for x in (truck_entry(e, lines_by.get(e["id"], []), vehicles.get(e["vehicle_id"])) for e in events) if x]
    entries += [x for x in (purchase_entry(b) for b in bills) if x]
    supabase.delete("acc_entries", {"source": f"in.({','.join(PROGRAM_SOURCES)})", "entry_date": f"gte.{lo}", "and": f"(entry_date.lte.{hi})"})
    _insert_entries(entries)
    return len(entries)


def _missing_amount(start: date, end: date) -> int:
    rows = supabase.select_all("maintenance_events", {"select": "id,total_cost", "status": "eq.confirmed", "event_date": f"gte.{start.isoformat()}",
                                                      "and": f"(event_date.lte.{end.isoformat()})", "order": "id.asc"})
    return sum(1 for r in rows if not r.get("total_cost"))


def report(month: str, half: Optional[int] = None, branch: str = "") -> dict:
    """The page: the checklist and the P&L for a company month (or one half), one branch or both."""
    _require()
    if not re.fullmatch(r"\d{4}-\d{2}", month or ""):
        raise InvalidEntry("เดือนไม่ถูกต้อง")
    if half not in (None, 1, 2):
        raise InvalidEntry("งวดไม่ถูกต้อง")
    start, end = period_bounds(month, half)
    sync_program(start, end)
    accounts = _accounts()
    entries = {e["id"]: e for e in supabase.select_all("acc_entries", {"select": "id,entry_date,branch,source,source_id,party,description,doc_type,doc_no",
                                                                       "entry_date": f"gte.{start.isoformat()}", "and": f"(entry_date.lte.{end.isoformat()})", "order": "id.asc"})}
    ids = list(entries)
    rows = []
    for i in range(0, len(ids), 100):
        params = {"select": "entry_id,account,debit,credit,branch", "entry_id": f"in.({','.join(ids[i:i + 100])})", "order": "id.asc"}
        if branch:
            params["branch"] = f"eq.{branch}"
        rows += [{**r, "entry": entries[r["entry_id"]]} for r in supabase.select_all("acc_lines", params)]
    counts: dict = {}
    seen: set = set()
    for r in rows:
        e = r["entry"]
        if e["id"] in seen:
            continue
        seen.add(e["id"])
        total = sum(_money(x["debit"]) for x in rows if x["entry"]["id"] == e["id"] and accounts.get(x["account"], {}).get("kind") in ("cogs", "expense"))
        n, t = counts.get(e["source"], (0, 0.0))
        counts[e["source"]] = (n + 1, t + total)
    period_key = f"{month}-{half or 0}"
    marks = {m["feed"]: m["status"] for m in supabase.select_all("acc_feed_marks", {"select": "feed,status", "period": f"eq.{period_key}", "branch": f"eq.{branch or 'all'}", "order": "feed.asc"})}
    return {"month": month, "half": half, "branch": branch, "start": start.isoformat(), "end": end.isoformat(),
            "pnl": pnl_tree(rows, accounts), "feeds": feed_status(half, counts, marks, _missing_amount(start, end)),
            "accounts": [{"code": a["code"], "en": a["name_en"], "th": a["name_th"], "kind": a["kind"], "section": a["section"]} for a in accounts.values() if a["active"]],
            "branches": list(BRANCHES)}


def add_entry(data: dict) -> dict:
    from app.service_plan import today_th
    _require()
    e = manual_entry(data, accounts=_accounts(), today=today_th())
    _insert_entries([e])
    return {"ok": True, "entry_date": e["entry_date"]}


def delete_entry(entry_id: str) -> None:
    _require()
    rows = supabase.select("acc_entries", {"select": "id,source", "id": f"eq.{entry_id}", "limit": "1"})
    if not rows:
        raise InvalidEntry("ไม่พบรายการนี้")
    if rows[0]["source"] in PROGRAM_SOURCES:
        raise InvalidEntry("รายการนี้มาจากบิลในโปรแกรม — แก้ที่บิลนั้นแทน")
    supabase.delete("acc_entries", {"id": f"eq.{entry_id}"})


def mark_feed(data: dict) -> dict:
    """"ไม่มีงวดนี้" / "ใส่ครบแล้ว" for a feed in a period (or undo)."""
    _require()
    feed, period, branch, status = data.get("feed"), str(data.get("period") or ""), data.get("branch") or "all", data.get("status")
    if feed not in FEED_BY_KEY or not re.fullmatch(r"\d{4}-\d{2}-[012]", period):
        raise InvalidEntry("ข้อมูลไม่ถูกต้อง")
    if status in (None, ""):
        supabase.delete("acc_feed_marks", {"feed": f"eq.{feed}", "period": f"eq.{period}", "branch": f"eq.{branch}"})
    elif status in ("none", "done"):
        supabase.insert("acc_feed_marks", [{"feed": feed, "period": period, "branch": branch, "status": status}], on_conflict="feed,period,branch")
    else:
        raise InvalidEntry("สถานะไม่ถูกต้อง")
    return {"ok": True}
