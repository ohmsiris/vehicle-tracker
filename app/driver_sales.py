"""
Driver sales (Saraburi routes) from the owner's Drivers Google Sheet, tab "Driver Sales 2026": a block of 8 columns per month (from H):
days, wage, daily rate, sales (ยอดขาย = payroll column G), commission (% รับ), rate %, %ซอง (bag commission + 1,000 attendance bonus),
total pay. One row per driver (code SB-PDxxx, name, nickname, truck no.).

Gross sales (what the route really sold), per the commission rules
(C:\\FD\\Saraburi\\02_Finance & Accounting\\Expenses\\คชจ\\commission-rules.md):
  * tube-ice big trucks (5%: วินัย มานัส วินทัย มาโนช บุญนำ สำรวย, + สะอาด 5%, สำเริง 3.5%): the written sales are net of the truck's
    fuel -> add the truck's fuel back (PT + Bangchak + cash); small trucks (2%) and ปรีชา: written sales are already gross
  * bag ice (ซอง) is paid per bag, not in ยอดขาย: สะอาด 10 ฿/bag sold at 300; สำเริง 3 ฿/bag at 140;
    ทวาย 3 ฿ per (bags - fuel/140) at 140 -> bags x 140 + fuel
  * the 1,000 attendance bonus sits in %ซอง when the driver worked every day of the period: taken off before counting bags
parse() and gross() are pure and unit-tested.
"""
MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
NET_OF_FUEL = {"SB-PD002", "SB-PD003", "SB-PD004", "SB-PD006", "SB-PD008", "SB-PD009", "SB-PD010", "SB-PD030"}
BAGS = {"SB-PD006": (10, 300), "SB-PD002": (3, 140)}           # (commission per bag, price per bag)
TWAI = "SB-PD001"
BONUS = 1000


def _n(v) -> float:
    return float(v) if isinstance(v, (int, float)) else 0.0


def parse(rows: list[tuple], year: int) -> list[dict]:
    """rows = the tab's cells (row 1 = month names, row 2 = headers, then drivers)."""
    starts = [(i, MONTHS.index(str(v).strip().upper()[:3]) + 1) for i, v in enumerate(rows[0])
              if isinstance(v, str) and str(v).strip().upper()[:3] in MONTHS]
    out = []
    for r in rows[2:]:
        code = str(r[1] or "").strip()
        if not code.startswith("SB-"):
            continue
        for c, m in starts:
            days, wage, rate, sales, comm, _pct, bag, total = (r[c + k] if c + k < len(r) else None for k in range(8))
            if not _n(days) and not _n(sales) and not _n(total):
                continue
            truck = r[4]
            out.append({"month": f"{year}-{m:02d}", "code": code, "name": str(r[2] or "").strip(), "nick": str(r[3] or "").strip(),
                        "truck": (str(int(truck)) if isinstance(truck, float) else str(truck).strip()) if truck not in (None, "") else None,
                        "days": _n(days), "wage": _n(wage), "rate": _n(rate), "sales": _n(sales), "commission": _n(comm),
                        "bag_pay": _n(bag), "total_pay": _n(total)})
    return out


def period_days(month: str) -> int:
    """Days in the company month (26th of the month before .. 25th)."""
    from datetime import date
    y, m = (int(x) for x in month.split("-"))
    py, pm = (y - 1, 12) if m == 1 else (y, m - 1)
    return (date(y, m, 25) - date(py, pm, 26)).days + 1


def gross(row: dict, fuel: float) -> dict:
    """-> {gross, bags, fuel_added}; fuel = the truck's fuel that month (None if not known)."""
    code, sales = row["code"], row["sales"]
    bonus = BONUS if row["days"] >= period_days(row["month"]) else 0
    bag_money = max(row["bag_pay"] - bonus, 0)
    bags, add = 0.0, 0.0
    if code in BAGS:
        per, price = BAGS[code]
        bags = round(bag_money / per)
        add += bags * price
    if code == TWAI:
        net_bags = bag_money / 3
        bags = round(net_bags + (fuel or 0) / 140)
        add += net_bags * 140 + (fuel or 0)
    elif code in NET_OF_FUEL:
        add += fuel or 0
    known = fuel is not None or (code not in NET_OF_FUEL and code != TWAI)
    return {"gross": round(sales + add, 2) if known else None, "bags": bags, "fuel_added": (fuel or 0) if code in NET_OF_FUEL or code == TWAI else 0}


def parse_payroll_text(text: str, month: str) -> list[dict]:
    """The driver payroll summary PDF (สรุปเงินเดือน พขร.), for a month the sheet does not have yet. Each driver: code + name, role, then
    days, wage, sales, commission, [bag pay -- missing when nothing], total pay, ... The bag cell is told apart by the arithmetic
    (total = wage + commission + bag). Truck numbers come from the sheet's latest month."""
    import re
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    num = re.compile(r"^-?[\d,]+(?:\.\d+)?$")
    out = []
    for i, ln in enumerate(lines):
        m = re.match(r"^(SB-PD\d+)\s*(.*)$", ln)
        if not m:
            continue
        name = m.group(2) or lines[i + 1]
        j = i + 2 if m.group(2) else i + 3
        vals = []
        while j < len(lines) and len(vals) < 6 and not lines[j].startswith("SB-"):
            if num.match(lines[j]):
                vals.append(float(lines[j].replace(",", "")))
            j += 1
        if len(vals) < 5:
            continue
        days, wage, sales, comm = vals[:4]
        if abs(vals[4] - (wage + comm)) <= 1.01:
            bag, total = 0.0, vals[4]
        elif len(vals) > 5 and abs(vals[5] - (wage + comm + vals[4])) <= 1.01:
            bag, total = vals[4], vals[5]
        else:       # หน้าโรง drivers: no sales cells -- [days,] wage, [bonus,] total (= wage + bonus)
            days, rest = (vals[0], vals[1:]) if vals[0] <= 31 else (0.0, vals)
            wage, sales, comm = rest[0], 0.0, 0.0
            if abs(rest[1] - wage) <= 0.01:
                bag, total = 0.0, rest[1]
            elif len(rest) > 2 and abs(rest[2] - wage - rest[1]) <= 0.01:
                bag, total = rest[1], rest[2]
            else:
                continue
        out.append({"month": month, "code": m.group(1), "name": re.sub(r"^นาย|^นาง(สาว)?", "", name).strip(), "nick": "", "truck": None,
                    "days": days, "wage": wage, "rate": round(wage / days, 2) if days else 0.0, "sales": sales, "commission": comm,
                    "bag_pay": bag, "total_pay": total})
    return out
