"""
สายรถ (#routes): Saraburi's delivery routes, one truck per row, month by month.

Per truck per company month:
  sales    gross route sales (driver_sales_monthly.gross_sales: written sales + bags + the fuel the big trucks' sales were net of)
  fuel     card fuel (PT + Bangchak, VAT included) + cash fuel, as used for the gross sales
  pay      driver pay (wage + commission + bag pay + attendance bonus)
  repairs  everything else booked on the truck that month (repairs, tyres, parts) -- not fuel
  profit   sales - fuel - pay - repairs: what the route brings in before the ice itself is paid for
  km       from GPS / odometer readings (acc_docs.km_rate), when the truck has them
A month is "ครบ" when both stations' fuel is known truck by truck and the cash fuel too; "ไม่มีน้ำมันเงินสด" when only the cash is
missing (small); otherwise the big trucks' sales and every truck's fuel are short, and the month is shown but not ranked.

summarize() is pure and unit-tested; report() reads the database.
"""
from datetime import date

NOT_REPAIRS = ("6120", "6130")          # fuel: already counted


def month_range(month: str) -> tuple[date, date]:
    y, m = (int(x) for x in month.split("-"))
    py, pm = (y - 1, 12) if m == 1 else (y, m - 1)
    return date(py, pm, 26), date(y, m, 25)


def summarize(rows: list[dict], repairs: dict, km: dict, vehicles: dict, current: dict) -> dict:
    """rows = driver_sales_monthly; repairs = {(month, vehicle_id): baht}; km = {(month, vehicle_id): km};
    vehicles = {id: vehicle}; current = {vehicle_id: today's driver name}."""
    months: dict = {}
    trucks: dict = {}
    for r in rows:
        vid, m = r.get("vehicle_id"), r["month"]
        ms = months.setdefault(m, {"month": m, "complete": True, "cash": False})
        ms["complete"] = ms["complete"] and bool(r.get("fuel_complete"))
        ms["cash"] = ms["cash"] or r.get("fuel_cash") is not None
        if not vid:
            continue
        v = vehicles.get(vid, {})
        t = trucks.setdefault(vid, {"vehicle_id": vid, "number": v.get("vehicle_number"), "plate": v.get("plate_number"),
                                    "driver_now": current.get(vid), "months": {}})
        x = t["months"].setdefault(m, {"sales": 0.0, "written": 0.0, "fuel": 0.0, "pay": 0.0, "drivers": [], "days": 0.0, "bags": 0.0})
        x["sales"] += float(r.get("gross_sales") or r.get("sales_written") or 0)
        x["written"] += float(r.get("sales_written") or 0)
        x["fuel"] += float(r.get("fuel_card") or 0) + float(r.get("fuel_cash") or 0)
        x["pay"] += float(r.get("total_pay") or 0)
        x["days"] = max(x["days"], float(r.get("days") or 0))
        x["bags"] += float(r.get("bags") or 0)
        if float(r.get("sales_written") or 0) or float(r.get("days") or 0):
            x["drivers"].append(r.get("name"))
    for ms in months.values():
        ms["status"] = "ok" if ms["complete"] and ms["cash"] else "no_cash" if ms["complete"] else "short"
    for vid, t in trucks.items():
        for m, x in t["months"].items():
            x["repairs"] = round(repairs.get((m, vid), 0.0), 2)
            x["km"] = km.get((m, vid))
            x["profit"] = round(x["sales"] - x["fuel"] - x["pay"] - x["repairs"], 2)
            x["margin"] = round(x["profit"] / x["sales"], 4) if x["sales"] else None
            x["per_fuel"] = round(x["sales"] / x["fuel"], 2) if x["fuel"] else None
            x["fuel_per_km"] = round(x["fuel"] / x["km"], 2) if x.get("km") else None
            for k in ("sales", "written", "fuel", "pay"):
                x[k] = round(x[k], 2)
    return {"months": sorted(months.values(), key=lambda ms: ms["month"]),
            "trucks": sorted(trucks.values(), key=lambda t: (len(str(t["number"])) > 3, str(t["number"]).zfill(4)))}


def report() -> dict:
    from app import supabase
    from app.acc_docs import km_rate
    rows = supabase.select_all("driver_sales_monthly", {"select": "*", "order": "id.asc"})
    vehicles = {v["id"]: v for v in supabase.select_all("vehicles", {"select": "id,vehicle_number,plate_number,branch,main_driver_id", "order": "id.asc"})}
    drivers = {d["id"]: d["name"] for d in supabase.select_all("drivers", {"select": "id,name", "order": "id.asc"})}
    current = {vid: drivers.get(v.get("main_driver_id")) for vid, v in vehicles.items() if v.get("main_driver_id")}
    vids = sorted({r["vehicle_id"] for r in rows if r.get("vehicle_id")})
    months = sorted({r["month"] for r in rows})
    if not months:
        return {"months": [], "trucks": []}
    first, last = month_range(months[0])[0], month_range(months[-1])[1]
    repairs: dict = {}
    entries = {e["id"]: e["entry_date"] for e in supabase.select_all("acc_entries", {"select": "id,entry_date", "entry_date": f"gte.{first.isoformat()}",
                                                                                    "and": f"(entry_date.lte.{last.isoformat()})", "order": "id.asc"})}
    exp = {a["code"] for a in supabase.select_all("acc_accounts", {"select": "code,kind", "order": "code.asc"}) if a["kind"] == "expense"} - set(NOT_REPAIRS)
    for i in range(0, len(vids), 50):
        for ln in supabase.select_all("acc_lines", {"select": "entry_id,account,debit,credit,vehicle_id", "vehicle_id": f"in.({','.join(vids[i:i + 50])})", "order": "id.asc"}):
            d = entries.get(ln["entry_id"])
            if not d or ln["account"] not in exp:
                continue
            dd = date.fromisoformat(d)
            m = f"{dd.year}-{dd.month:02d}" if dd.day <= 25 else (f"{dd.year + 1}-01" if dd.month == 12 else f"{dd.year}-{dd.month + 1:02d}")
            repairs[(m, ln["vehicle_id"])] = repairs.get((m, ln["vehicle_id"]), 0.0) + float(ln["debit"]) - float(ln["credit"])
    readings = supabase.select_all("km_readings", {"select": "vehicle_id,kind,start_date,end_date,km", "order": "id.asc"}) if vids else []
    km: dict = {}
    for vid in vids:
        mine = [r for r in readings if r["vehicle_id"] == vid]
        if not mine:
            continue
        for m in months:
            s, e = month_range(m)
            rate, _ = km_rate(mine, s, e)
            if rate:
                km[(m, vid)] = round(rate * ((e - s).days + 1))
    return summarize(rows, repairs, km, vehicles, current)

