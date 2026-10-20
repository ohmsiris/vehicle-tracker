"""
Combines the per-file extractions of one upload batch into a single DRAFT
event + flat line items, ready for the review page. Pure functions, no I/O,
no API calls -- everything here is deterministic and unit-tested.

Beyond merging, this is where the model's output gets sanity-checked by
plain code rather than trusted: dates that are implausible (a Buddhist-Era
conversion slip is the classic one), mileage that contradicts itself across
the batch, line items that don't add up to the stated total, a plate that
doesn't match the vehicle the user picked. Anything suspicious is added to
the event's / line's `low_confidence_fields` so the review page highlights
it, and described in plain Thai in `warnings`.

Event-level flag names match schema.sql's maintenance_events columns
(vendor, event_date, mileage, next_service_mileage, branch, total_cost),
plus 'vehicle' for plate / vehicle mismatches.
"""
import re
from collections import Counter
from datetime import date, timedelta
from typing import Optional

from app.extraction import HEADER_CONFIDENCE_FIELDS
from app.roster import clean_vehicle_number, lookup_vehicle, normalize_plate

# Model-side header field name -> event-level flag name
HEADER_TO_EVENT_FLAG = {
    "shop_name": "vendor",
    "date": "event_date",
    "branch": "branch",
    "vehicle_license": "vehicle",
    "vehicle_number": "vehicle",
    "mileage": "mileage",
    "next_service_mileage": "next_service_mileage",
    "total_cost": "total_cost",
}
assert set(HEADER_TO_EVENT_FLAG) == set(HEADER_CONFIDENCE_FIELDS), "extraction.py header fields drifted from batch.py"

# Categories that say little about WHAT the visit was for, so they don't
# decide the event's job_type (a bill that's mostly labor is still about
# whichever system the parts were for).
NON_DESCRIPTIVE_CATEGORIES = {"ค่าแรง", "อื่นๆ"}

MAX_PLAUSIBLE_MILEAGE = 3_000_000
DATE_LOOKBACK_DAYS = 3 * 365
MONEY_TOLERANCE_BAHT = 1.0


def _digits_to_int(raw) -> Optional[int]:
    digits = re.sub(r"\D", "", str(raw or ""))
    return int(digits) if digits else None


def _parse_date(raw: str) -> Optional[date]:
    m = re.fullmatch(r"\s*(\d{4})-(\d{2})-(\d{2})\s*", raw or "")
    if not m:
        return None
    year, month, day = (int(g) for g in m.groups())
    if 2500 <= year <= 2700:  # Buddhist Era slipped past the model's conversion
        year -= 543
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _most_common(values: list):
    """Most frequent value; first-seen wins ties (Counter keeps insertion order)."""
    return Counter(values).most_common(1)[0][0] if values else None


def _plates_match(a: str, b: str) -> bool:
    a, b = normalize_plate(a), normalize_plate(b)
    return bool(a and b) and (a == b or a in b or b in a)


def build_draft(
    files: list[dict],
    roster: list[dict],
    selected_plate: str = "",
    today: Optional[date] = None,
) -> dict:
    """
    files: one dict per uploaded bill, in upload order:
        {"index", "filename", "media_type", "ok", "error", "usage", "extraction"}
      where extraction is extract_file()'s dict when ok is True.
    roster: fleet rows (see app/roster.py).
    selected_plate: the plate of the vehicle the user picked in the form
      ("" if none) -- compared against what the bill says.
    """
    today = today or date.today()
    flags: set[str] = set()
    warnings: list[str] = []

    ok_files = [f for f in files if f["ok"]]
    for f in files:
        if not f["ok"]:
            warnings.append(f"อ่านไฟล์ {f['filename']} ไม่สำเร็จ: {f['error']}")

    # A file with no money anywhere on it is almost certainly not a bill
    # (a truck photo, an odometer, a handwritten work-order note dropped in
    # the bill slot) -- say so instead of letting it quietly add junk rows.
    for f in ok_files:
        bill = f["extraction"]
        has_money = (bill.get("total_cost") or 0) > 0 or any((it.get("cost") or 0) > 0 for it in bill["line_items"])
        if not has_money:
            warnings.append(
                f"ไฟล์ {f['filename']} ดูไม่เหมือนบิล (ไม่พบยอดเงินเลย) -- ถ้าเป็นรูปอื่นๆ ให้ย้ายไปช่อง 'รูปอื่นๆ'"
            )

    # -- model-reported header uncertainty, plus per-file roster enrichment --
    plates: list[str] = []
    provinces: list[str] = []
    vehicle_numbers: list[str] = []
    for f in ok_files:
        bill = f["extraction"]
        for field in bill.get("low_confidence_fields", []):
            flags.add(HEADER_TO_EVENT_FLAG[field])

        bill["vehicle_number"] = clean_vehicle_number(bill.get("vehicle_number"))
        match = lookup_vehicle(bill.get("vehicle_license") or "", roster)
        if match:
            if not bill["vehicle_number"]:
                bill["vehicle_number"] = match.get("truck_number", "")
            if not (bill.get("vehicle_license_province") or "").strip():
                bill["vehicle_license_province"] = match.get("province", "")
            bill_branch = (bill.get("branch") or "").strip()
            roster_branch = (match.get("branch") or "").strip()
            if bill_branch and roster_branch and bill_branch != roster_branch:
                warnings.append(
                    f"ป้ายทะเบียนตรงกับรถของสาขา{roster_branch} แต่บิลระบุสาขา{bill_branch} -- ช่วยตรวจสอบด้วย"
                )
                flags.add("branch")
        if (bill.get("vehicle_license") or "").strip():
            plates.append(bill["vehicle_license"].strip())
        if (bill.get("vehicle_license_province") or "").strip():
            provinces.append(bill["vehicle_license_province"].strip())
        if bill["vehicle_number"]:
            vehicle_numbers.append(bill["vehicle_number"])

    bill_plate = _most_common(plates) or ""
    if len({normalize_plate(p) for p in plates}) > 1:
        warnings.append("บิลในชุดนี้ระบุป้ายทะเบียนไม่ตรงกัน: " + ", ".join(sorted(set(plates))))
        flags.add("vehicle")
    if selected_plate and bill_plate and not _plates_match(selected_plate, bill_plate):
        warnings.append(f"บิลระบุป้าย {bill_plate} แต่คุณเลือกรถทะเบียน {selected_plate} -- ตรวจสอบว่าเลือกรถถูกคัน")
        flags.add("vehicle")
    bill_match = lookup_vehicle(bill_plate, roster) if bill_plate else None

    # -- vendor / branch --
    shop_names = [b["extraction"]["shop_name"].strip() for b in ok_files if (b["extraction"].get("shop_name") or "").strip()]
    vendor = _most_common(shop_names)
    if len(set(shop_names)) > 1:
        warnings.append("บิลในชุดนี้มาจากหลายร้าน: " + ", ".join(sorted(set(shop_names))))
        flags.add("vendor")
    branches = [b["extraction"]["branch"].strip() for b in ok_files if (b["extraction"].get("branch") or "").strip()]
    branch = _most_common(branches)

    # -- date --
    dates = [d for d in (_parse_date(b["extraction"].get("date")) for b in ok_files) if d]
    event_date = _most_common(dates)
    if len(set(dates)) > 1:
        warnings.append("วันที่ในบิลแต่ละใบไม่ตรงกัน: " + ", ".join(sorted(d.isoformat() for d in set(dates))))
        flags.add("event_date")
    if event_date and not (today - timedelta(days=DATE_LOOKBACK_DAYS) <= event_date <= today + timedelta(days=1)):
        warnings.append(f"วันที่ {event_date.isoformat()} ดูไม่สมเหตุสมผล (อนาคตหรือเก่าเกินไป) -- ตรวจปี พ.ศ./ค.ศ.")
        flags.add("event_date")
    if ok_files and event_date is None:
        flags.add("event_date")

    # -- mileage --
    mileages = [m for m in (_digits_to_int(b["extraction"].get("mileage")) for b in ok_files) if m]
    mileage = _most_common(mileages)
    if len(set(mileages)) > 1:
        warnings.append("เลขไมล์ในบิลแต่ละใบไม่ตรงกัน: " + ", ".join(str(m) for m in sorted(set(mileages))))
        flags.add("mileage")
    if mileage and mileage > MAX_PLAUSIBLE_MILEAGE:
        warnings.append(f"เลขไมล์ {mileage:,} สูงผิดปกติ -- ตรวจสอบจำนวนหลัก")
        flags.add("mileage")
    next_services = [m for m in (_digits_to_int(b["extraction"].get("next_service_mileage")) for b in ok_files) if m]
    next_service_mileage = _most_common(next_services)
    if mileage and next_service_mileage and next_service_mileage <= mileage:
        warnings.append(f"เลขไมล์ครั้งต่อไป ({next_service_mileage:,}) ไม่มากกว่าเลขไมล์ปัจจุบัน ({mileage:,})")
        flags.update({"mileage", "next_service_mileage"})

    # -- line items --
    line_items: list[dict] = []
    for f in ok_files:
        for item in f["extraction"]["line_items"]:
            qty = item.get("quantity") or 0
            cost = item.get("cost") or 0
            line_flags = set(item.get("low_confidence_fields", []))
            if not cost:
                line_flags.add("cost")  # 0 means "not itemized or not legible"
            if "[illegible]" in (item.get("description") or ""):
                line_flags.add("description")
            line_items.append(
                {
                    "file_index": f["index"],
                    "line_number": len(line_items) + 1,
                    "description": item.get("description", ""),
                    "category": item.get("category"),
                    "quantity": qty if qty else None,
                    "unit": (item.get("unit") or "").strip() or None,
                    "unit_cost": round(cost / qty, 2) if qty and cost else None,
                    "line_total": cost if cost else None,
                    "low_confidence_fields": sorted(line_flags),
                }
            )

    # -- total --
    stated_totals = [
        b["extraction"]["total_cost"]
        for b in ok_files
        if b["extraction"].get("has_total_this_page") and (b["extraction"].get("total_cost") or 0) > 0
    ]
    stated_sum = round(sum(stated_totals), 2)
    line_sum = round(sum(li["line_total"] or 0 for li in line_items), 2)
    if stated_sum > 0:
        total_cost: Optional[float] = stated_sum
        if line_sum > 0 and abs(stated_sum - line_sum) > MONEY_TOLERANCE_BAHT:
            warnings.append(
                f"ผลรวมรายการ ({line_sum:,.2f}) ไม่ตรงกับยอดรวมในบิล ({stated_sum:,.2f}) "
                "-- อาจมี VAT/ส่วนลด หรืออ่านรายการผิดบางแถว"
            )
            flags.add("total_cost")
    elif line_sum > 0:
        total_cost = line_sum
        warnings.append("ไม่พบยอดรวมในบิล ใช้ผลรวมของรายการแทน -- อาจขาดหน้าสุดท้ายของบิล")
        flags.add("total_cost")
    else:
        total_cost = None
        if ok_files:
            flags.add("total_cost")
    if ok_files and any(f["extraction"].get("continues_next_page") for f in ok_files[-1:]):
        warnings.append("หน้าสุดท้ายที่อัปโหลดระบุว่าบิลมีต่ออีกหน้า (ยอดยกไป) -- ดูเหมือนจะขาดหน้า")
        flags.add("total_cost")

    # -- job_type: the category with the most money behind it --
    by_category: Counter = Counter()
    for li in line_items:
        if li["category"] and li["category"] not in NON_DESCRIPTIVE_CATEGORIES:
            by_category[li["category"]] += li["line_total"] or 0
    job_type = by_category.most_common(1)[0][0] if by_category else None

    return {
        "event": {
            "event_date": event_date.isoformat() if event_date else None,
            "mileage": mileage,
            "next_service_mileage": next_service_mileage,
            "job_type": job_type,
            "vendor": vendor,
            "branch": branch,
            "total_cost": total_cost,
            "notes": "",
            "low_confidence_fields": sorted(flags),
        },
        "bill_vehicle": {
            "plate": bill_plate,
            "province": _most_common(provinces) or "",
            "vehicle_number": _most_common(vehicle_numbers) or "",
            "roster_match": bill_match,
        },
        "bills": [
            {
                "file_index": f["index"],
                "filename": f["filename"],
                "media_type": f["media_type"],
                "ok": f["ok"],
                "error": f["error"],
                "usage": f["usage"],
                "extraction": f["extraction"],
            }
            for f in files
        ],
        "line_items": line_items,
        "warnings": warnings,
    }
