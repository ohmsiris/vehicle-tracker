"""
Numbers for the dashboard page. Pure functions over rows from the database
views (v_events, v_line_items, v_vehicle_status, v_requests) -- no I/O, so
every figure is unit-tested; app/main.py fetches the rows and calls
build_dashboard().

Definitions worth knowing (they are also shown on the page):
  * Spend = the confirmed events' total_cost, by event date. An event saved
    without a cost (e.g. a manual entry) counts as a repair but adds 0.
  * Spend by category comes from the bill's LINE items, which can differ a
    little from event totals (VAT, discounts), so it is labelled as such.
  * "Repairs done" counts every repair ONCE, however it was recorded. A saved
    bill or manual entry is one repair, on its own date; a board request that
    is linked to one is the SAME repair and adds nothing. Only requests with
    no record behind them (closed with no bill, or fixed and still waiting for
    a bill) are counted on top, by the date they were fixed -- and they drop
    out of that group the moment a bill links to them. Drivers don't always
    write a problem on the FOH sheet, so the count can't depend on requests.
  * "Not serviced lately" is only a prompt to look, not a maintenance due
    date -- the system has no service intervals yet.
"""
from collections import defaultdict
from datetime import date
from typing import Optional

STALE_DAYS = 90       # a truck with no repair record for this long is surfaced for a look
TOP_TRUCKS = 10
TOP_VENDORS = 8


def month_start(d: date) -> date:
    return d.replace(day=1)


def add_months(d: date, n: int) -> date:
    idx = d.year * 12 + (d.month - 1) + n
    return date(idx // 12, idx % 12 + 1, 1)


def _num(v) -> float:
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _d(v) -> Optional[date]:
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


def _money(x: float) -> float:
    return round(x, 2)


def build_dashboard(*, events: list[dict], lines: list[dict], vehicles: list[dict], requests: list[dict],
                    today: date, months: int = 12, branch: Optional[str] = None) -> dict:
    months = max(1, min(int(months), 60))
    this_month = month_start(today)
    first_month = add_months(this_month, -(months - 1))
    month_keys = [add_months(first_month, i) for i in range(months)]

    def in_branch(row_branch) -> bool:
        return not branch or (row_branch or "") == branch

    ev = [e for e in events if in_branch(e.get("vehicle_branch"))]
    ev_period = [e for e in ev if (_d(e.get("event_month")) or date.min) >= first_month]
    ln_period = [l for l in lines if in_branch(l.get("vehicle_branch")) and (_d(l.get("event_month")) or date.min) >= first_month]
    veh = [v for v in vehicles if in_branch(v.get("branch"))]
    req = [r for r in requests if in_branch(r.get("branch"))]

    # ---- by month (every month in the window, including empty ones)
    by_month = defaultdict(lambda: [0.0, 0])
    for e in ev_period:
        m = _d(e.get("event_month"))
        by_month[m][0] += _num(e.get("total_cost"))
        by_month[m][1] += 1
    monthly = [{"month": m.strftime("%Y-%m"), "spend": _money(by_month[m][0]), "count": by_month[m][1]} for m in month_keys]

    # ---- repairs done, each counted once (see the module docstring)
    done = defaultdict(lambda: {"records": 0, "no_bill": 0, "awaiting": 0})
    for e in ev_period:
        done[_d(e.get("event_month"))]["records"] += 1
    for r in req:
        fixed = _d(r.get("fixed_date"))
        if not fixed or r.get("has_record") or r.get("status") not in ("awaiting_bill", "completed"):
            continue          # open / cancelled, or already counted through its bill or manual entry
        if month_start(fixed) >= first_month:
            done[month_start(fixed)]["awaiting" if r["status"] == "awaiting_bill" else "no_bill"] += 1
    for m in monthly:
        d_ = done[date.fromisoformat(m["month"] + "-01")]
        m["repairs"] = d_["records"] + d_["no_bill"] + d_["awaiting"]

    def done_total(months_: list) -> dict:
        t = {"records": sum(done[m]["records"] for m in months_), "no_bill": sum(done[m]["no_bill"] for m in months_),
             "awaiting": sum(done[m]["awaiting"] for m in months_)}
        t["total"] = t["records"] + t["no_bill"] + t["awaiting"]
        return t

    spend_this = by_month[this_month][0]
    spend_last = by_month[add_months(this_month, -1)][0] if months > 1 else 0.0

    # ---- by category (line items)
    cat = defaultdict(lambda: {"amount": 0.0, "category_en": None})
    for l in ln_period:
        key = l.get("category") or "ไม่ระบุหมวด"
        cat[key]["amount"] += _num(l.get("line_total"))
        cat[key]["category_en"] = cat[key]["category_en"] or l.get("category_en")
    cat_total = sum(c["amount"] for c in cat.values())
    by_category = sorted(
        ({"category": k, "category_en": c["category_en"], "amount": _money(c["amount"]),
          "share": round(c["amount"] / cat_total, 4) if cat_total else 0.0} for k, c in cat.items() if c["amount"] > 0),
        key=lambda c: -c["amount"])

    # ---- by truck and by vendor
    trucks = defaultdict(lambda: {"amount": 0.0, "count": 0, "last_date": None, "plate": None, "truck_number": None, "branch": None})
    vendors = defaultdict(lambda: [0.0, 0])
    for e in ev_period:
        key = e.get("vehicle_id") or e.get("plate_number")
        t = trucks[key]
        t["amount"] += _num(e.get("total_cost"))
        t["count"] += 1
        t["plate"], t["truck_number"], t["branch"] = e.get("plate_number"), e.get("vehicle_number"), e.get("vehicle_branch")
        ed = _d(e.get("event_date"))
        if ed and (t["last_date"] is None or ed > t["last_date"]):
            t["last_date"] = ed
        name = (e.get("vendor") or "").strip()
        if name:
            vendors[name][0] += _num(e.get("total_cost"))
            vendors[name][1] += 1
    top_trucks = sorted(
        ({**{k: v for k, v in t.items() if k != "last_date"}, "amount": _money(t["amount"]),
          "last_date": t["last_date"].isoformat() if t["last_date"] else None} for t in trucks.values()),
        key=lambda t: (-t["amount"], -t["count"]))[:TOP_TRUCKS]
    top_vendors = sorted(({"vendor": k, "amount": _money(a), "count": c} for k, (a, c) in vendors.items()),
                         key=lambda v: (-v["amount"], -v["count"]))[:TOP_VENDORS]

    # ---- fleet table: what each truck needs a look at
    spend_by_vehicle = {k: t for k, t in trucks.items()}
    fleet = []
    for v in veh:
        vid = v.get("vehicle_id")
        s = spend_by_vehicle.get(vid, {"amount": 0.0, "count": 0})
        last = _d(v.get("last_service_date"))
        days = (today - last).days if last else None
        flags = []
        if _num(v.get("awaiting_bill_requests")) > 0:
            flags.append("awaiting_bill")
        if _num(v.get("at_shop_requests")) > 0:
            flags.append("at_shop")
        if _num(v.get("open_requests")) > 0:
            flags.append("open_requests")
        if last is None:
            flags.append("no_history")
        elif days >= STALE_DAYS:
            flags.append("not_serviced_lately")
        fleet.append({
            "vehicle_id": vid, "plate": v.get("plate_number"), "truck_number": v.get("vehicle_number"), "branch": v.get("branch"),
            "open_requests": int(_num(v.get("open_requests"))), "awaiting_bill_requests": int(_num(v.get("awaiting_bill_requests"))),
            "at_shop_requests": int(_num(v.get("at_shop_requests"))),
            "last_service_date": last.isoformat() if last else None, "days_since_service": days,
            "last_known_mileage": v.get("last_known_mileage"), "next_service_mileage": v.get("next_service_mileage"),
            "spend_period": _money(s["amount"]), "repairs_period": s["count"], "flags": flags,
        })
    # open work first, then bills outstanding, then staleness; quiet trucks last
    order = {"open_requests": 0, "at_shop": 1, "awaiting_bill": 2, "not_serviced_lately": 3, "no_history": 4}
    fleet.sort(key=lambda f: (min((order[x] for x in f["flags"]), default=9), -(f["days_since_service"] or 0), f["truck_number"] or ""))

    # ---- requests
    active = [r for r in req if r.get("status") in ("open", "at_shop", "awaiting_bill")]
    awaiting = [r for r in active if r["status"] == "awaiting_bill"]
    at_shop = [r for r in active if r["status"] == "at_shop"]
    closed_period = [r for r in req if r.get("status") == "completed" and (_d(r.get("status_changed_date")) or date.min) >= first_month]
    fix_days = [r["days_to_fix"] for r in req if r.get("days_to_fix") is not None and r.get("status") in ("awaiting_bill", "completed")
                and (_d(r.get("fixed_date")) or date.min) >= first_month]

    return {
        "as_of": today.isoformat(), "months": months, "branch": branch or "",
        "kpis": {
            "spend_this_month": _money(spend_this), "spend_last_month": _money(spend_last),
            "spend_period": _money(sum(m["spend"] for m in monthly)),
            # bills / manual entries only (what the spend figures are built from)
            "repairs_this_month": by_month[this_month][1], "repairs_period": sum(m["count"] for m in monthly),
            # every repair once, however it was recorded
            "done_this_month": done_total([this_month]), "done_period": done_total(month_keys),
            "open_requests": sum(1 for r in active if r["status"] == "open"),
            "at_shop": len(at_shop),
            "longest_at_shop_days": max((r.get("days_at_shop") or 0 for r in at_shop), default=0),
            "awaiting_bill": len(awaiting),
            "oldest_awaiting_days": max((r.get("days_in_status") or 0 for r in awaiting), default=0),
            "closed_without_bill": sum(1 for r in closed_period if r.get("no_bill")),
            "avg_days_to_fix": round(sum(fix_days) / len(fix_days), 1) if fix_days else None,
        },
        "monthly": monthly,
        "by_category": by_category,
        "top_trucks": top_trucks,
        "top_vendors": top_vendors,
        "fleet": fleet,
        "notes": {
            "events_without_cost": sum(1 for e in ev_period if not _num(e.get("total_cost"))),
            "stale_days": STALE_DAYS,
        },
    }
