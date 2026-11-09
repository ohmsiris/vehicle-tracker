"""
บัญชี › งบกำไรขาดทุน › ต้นทุนต่อหน่วย: what one tube-ice bag (25 kg) and one block (ซอง, ~190 kg) cost to make, month by month -- owner, 9 Nov 2026.

How much was made is not in the books (the route trucks' sales come in as totals), so it is worked out from the 15-minute meters (app/amr.py)
and what each machine is known to draw and make:
  * meter 916 carries the two บ่อซอง (block ice, ~154 kW together, running ~97% of the time) and tube machines 1 and 4;
    meter 615 carries tube machines 2, 3 and 5. Each meter also has a background load (lights, pumps, cold room ...).
  * tube bags = (meter kWh - background - บ่อซอง) / kWh per bag of the machines on that meter (measured: #1 2.28, #2 1.88, #3 1.33,
    #4 ~1.35, #5 1.95 -> 1.72 on 615, 1.82 on 916, weighted by how much each runs). Checked against the on/off sheets: within ~8%.
  * blocks = 27 rows x 24 cans, about half harvested a day when the บ่อซอง run all day (owner's figure, "in theory"), x ~190 kg.
Costs come from the books for the same company month and are split:
  * electricity: by kWh -- the บ่อซอง's kWh to block, the tube machines' to tube, the background by weight made
  * salt -> block (brine); bags / packaging -> tube; machine repairs booked against a machine -> that machine's product
  * production labour, water, ammonia, chemicals, untagged repairs and parts, admin -> by weight made
  * delivery (trucks, tyres, fuel, drivers, vehicle tax) -> by weight, shown apart (an average, not per route)
  * not counted: ice bought from another plant (it is resold, not made), loan interest
compute() is pure and unit-tested; summary() reads the data.
"""
from collections import defaultdict
from datetime import date, timedelta

PARAMS = {
    "สระบุรี": {
        "meters": {"020019229615": {"name": "615", "background_kw": 70.0, "kwh_per_bag": 1.72, "block": False},
                   "020019227916": {"name": "916", "background_kw": 38.0, "kwh_per_bag": 1.82, "block": True}},
        "block_kw": 154.0, "block_run": 0.97, "cans": 27 * 24, "harvest_share": 0.5, "block_kg": 190.0, "bag_kg": 25.0,
    },
}
CATEGORIES = [   # key, label, accounts (None = by the rule in _category)
    ("elec", "ค่าไฟฟ้า"), ("labour", "ค่าแรงฝ่ายผลิต + OT"), ("pack", "ถุง / เกลือ"), ("supply", "น้ำ แอมโมเนีย เคมี"),
    ("repair", "ซ่อมเครื่องจักร + อะไหล่"), ("admin", "บริหาร (สำนักงาน ห้องพัก บัญชี ส่วย ...)"), ("delivery", "ค่าส่ง (รถ น้ำมัน คนขับ) เฉลี่ย"),
]
ACCOUNT_CAT = {"5100": "elec", "5400": "labour", "5410": "labour", "5300": "pack", "5330": "pack", "5200": "supply", "5210": "supply",
               "5310": "supply", "5320": "supply", "5500": "repair", "5510": "repair",
               "6100": "delivery", "6110": "delivery", "6120": "delivery", "6130": "delivery", "6200": "delivery", "6300": "delivery"}
TO_BLOCK, TO_TUBE = {"5300"}, {"5330"}          # salt is for the brine tanks; bags are for tube ice
SKIP = {"5050", "5060"}                          # ice bought from elsewhere


def company_month(d: date) -> str:
    if d.day >= 26:
        y, m = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
        return f"{y}-{m:02d}"
    return f"{d.year}-{d.month:02d}"


def month_bounds(month: str) -> tuple[date, date]:
    y, m = int(month[:4]), int(month[5:7])
    py, pm = (y - 1, 12) if m == 1 else (y, m - 1)
    return date(py, pm, 26), date(y, m, 25)


def production(days: list[dict], month: str, p: dict) -> dict:
    """days = amr_days() rows {ca, day, s, k} (k = kW per 15 minutes). -> bags, blocks, kWh split, how much of the month the meters cover."""
    start, end = month_bounds(month)
    n_days = (end - start).days + 1
    kwh = defaultdict(float)
    hours = defaultdict(float)
    for d in days:
        if d["ca"] not in p["meters"] or not (start.isoformat() <= d["day"] <= end.isoformat()):
            continue
        kwh[d["ca"]] += sum(float(x) for x in d["k"]) / 4
        hours[d["ca"]] += len(d["s"]) / 4
    if not hours or min(hours.get(ca, 0) for ca in p["meters"]) < n_days * 24 * 0.5:
        return {}
    scale = {ca: n_days * 24 / hours[ca] for ca in p["meters"]}          # meters missing a few days: scale to the whole month
    out = {"block_kwh": 0.0, "tube_kwh": 0.0, "background_kwh": 0.0, "bags": 0.0, "blocks": 0.0, "metered_kwh": 0.0,
           "coverage": round(min(hours[ca] for ca in p["meters"]) / (n_days * 24), 3)}
    for ca, m in p["meters"].items():
        h = n_days * 24
        total = kwh[ca] * scale[ca]
        bg = m["background_kw"] * h
        blk = p["block_kw"] * p["block_run"] * h if m["block"] else 0.0
        tube = max(total - bg - blk, 0.0)
        out["metered_kwh"] += total
        out["background_kwh"] += bg
        out["block_kwh"] += blk
        out["tube_kwh"] += tube
        out["bags"] += tube / m["kwh_per_bag"]
    out["blocks"] = p["cans"] * p["harvest_share"] * p["block_run"] * n_days
    return {k: round(v, 3 if k == "coverage" else 1) for k, v in out.items()}


def compute(month: str, prod: dict, costs: dict, tagged: dict, sales: dict, p: dict) -> dict:
    """One company month. costs = {account: baht}, tagged = {account: {"block"|"tube": baht}} (part of costs booked against a machine),
    sales = {"tube": (qty, baht), "block": (qty, baht)} from the sales program."""
    tube_kg, block_kg = prod["bags"] * p["bag_kg"], prod["blocks"] * p["block_kg"]
    w_block = block_kg / (tube_kg + block_kg) if tube_kg + block_kg else 0.0
    split = {k: {"tube": 0.0, "block": 0.0} for k, _ in CATEGORIES}
    other_admin = 0.0
    for acc, amt in costs.items():
        if acc in SKIP or not amt:
            continue
        cat = ACCOUNT_CAT.get(acc) or ("admin" if acc.startswith("7") else None)
        if not cat:
            continue
        if acc == "5100":                        # electricity by kWh
            kwh = prod["metered_kwh"] or 1.0
            b = (prod["block_kwh"] + prod["background_kwh"] * w_block) / kwh
            split[cat]["block"] += amt * b
            split[cat]["tube"] += amt * (1 - b)
            continue
        if acc in TO_BLOCK:
            split[cat]["block"] += amt
            continue
        if acc in TO_TUBE:
            split[cat]["tube"] += amt
            continue
        t = tagged.get(acc, {})
        split[cat]["block"] += t.get("block", 0.0)
        split[cat]["tube"] += t.get("tube", 0.0)
        rest = amt - t.get("block", 0.0) - t.get("tube", 0.0)
        split[cat]["block"] += rest * w_block
        split[cat]["tube"] += rest * (1 - w_block)
    per = {}
    for prod_key, units, unit_kg in (("tube", prod["bags"], p["bag_kg"]), ("block", prod["blocks"], p["block_kg"])):
        rows = {k: (split[k][prod_key] / units if units else None) for k, _ in CATEGORIES}
        plant = sum(v for k, v in rows.items() if k != "delivery" and v is not None)
        qty, baht = sales.get(prod_key, (0, 0.0))
        price = baht / qty if qty else None
        per[prod_key] = {"units": round(units), "kg": round(units * unit_kg), "unit_kg": unit_kg,
                         "per_unit": {k: (round(v, 2) if v is not None else None) for k, v in rows.items()},
                         "plant": round(plant, 2), "delivered": round(plant + (rows["delivery"] or 0), 2),
                         "price": round(price, 2) if price else None, "price_qty": round(qty),
                         "total": {k: round(split[k][prod_key]) for k, _ in CATEGORIES}}
    return {"month": month, "start": month_bounds(month)[0].isoformat(), "end": month_bounds(month)[1].isoformat(),
            "production": prod, "block_share_kg": round(w_block, 3), **per}


_CACHE: dict = {}


def summary(branch: str = "สระบุรี") -> dict:
    """Every company month that has both meter readings and books, for one branch."""
    import time
    from app import supabase
    if branch not in PARAMS:
        return {"branch": branch, "ready": False, "months": [],
                "why": "ยังไม่มีข้อมูลการผลิตของสาขานี้ (กำลังไฟและจำนวนที่ผลิตได้ของแต่ละเครื่อง)"}
    hit = _CACHE.get(branch)
    if hit and time.time() - hit[0] < 300:
        return hit[1]
    p = PARAMS[branch]
    days = supabase.rpc("amr_days", {"p_branch": branch}) or []
    if not days:
        return {"branch": branch, "ready": False, "months": [], "why": "ยังไม่มีข้อมูลมิเตอร์ราย 15 นาที"}
    first = date.fromisoformat(min(d["day"] for d in days))
    last = date.fromisoformat(max(d["day"] for d in days))
    months = sorted({company_month(first + timedelta(days=i)) for i in range((last - first).days + 1)})
    start, end = month_bounds(months[0])[0], month_bounds(months[-1])[1]
    entries = {e["id"]: e["entry_date"] for e in supabase.select_all("acc_entries", {
        "select": "id,entry_date", "entry_date": f"gte.{start.isoformat()}", "and": f"(entry_date.lte.{end.isoformat()})", "order": "id.asc"})}
    kinds = {m["id"]: ("block" if m["kind"] == "บ่อซอง" else "tube") for m in supabase.select_all("machines", {"select": "id,kind", "order": "id.asc"})}
    costs = defaultdict(lambda: defaultdict(float))
    tagged = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))
    revenue = defaultdict(float)
    for ln in supabase.select_all("acc_lines", {"select": "entry_id,account,debit,credit,machine_id", "branch": f"eq.{branch}", "order": "id.asc"}):
        d = entries.get(ln["entry_id"])
        if not d:
            continue
        m = company_month(date.fromisoformat(d))
        amt = float(ln["debit"] or 0) - float(ln["credit"] or 0)
        if ln["account"] == "4100":
            revenue[m] -= amt
            continue
        costs[m][ln["account"]] += amt
        if ln.get("machine_id") in kinds:
            tagged[m][ln["account"]][kinds[ln["machine_id"]]] += amt
    sales = defaultdict(lambda: {"tube": [0.0, 0.0], "block": [0.0, 0.0]})
    for s in supabase.select_all("sales_lines", {"select": "month,product,qty,amount", "branch": f"eq.{branch}",
                                                 "product": "in.(หลอดใหญ่,ซอง)", "order": "id.asc"}):
        k = "tube" if s["product"] == "หลอดใหญ่" else "block"
        if s["qty"] and s["amount"] and float(s["amount"]) > 0:
            sales[s["month"]][k][0] += float(s["qty"])
            sales[s["month"]][k][1] += float(s["amount"])
    out = []
    for m in months:
        prod = production(days, m, p)
        if not prod or revenue.get(m, 0) <= 0:
            continue
        out.append(compute(m, prod, dict(costs[m]), {a: dict(v) for a, v in tagged[m].items()},
                           {k: tuple(v) for k, v in sales[m].items()}, p))
    # a month whose cheque-paid supplies (bags, salt, chemicals) are not booked yet reads too cheap: say so
    def cheque_items(x):
        return sum(x[k]["total"]["pack"] + x[k]["total"]["supply"] for k in ("tube", "block"))
    for x in out:
        others = [cheque_items(y) for y in out if y is not x]
        x["incomplete"] = bool(others) and cheque_items(x) < 0.3 * (sum(others) / len(others))
    res = {"branch": branch, "ready": True, "months": out, "categories": [{"key": k, "label": l} for k, l in CATEGORIES],
           "assumptions": [
               f"น้ำแข็งซอง: บ่อ 27 แถว × 24 ซอง = {p['cans']} ซอง · เก็บได้ประมาณครึ่งหนึ่งต่อวันถ้าเดินตลอด · ก้อนละ ~{p['block_kg']:.0f} กก. (ตัวเลขทางทฤษฎีจากเจ้าของ)",
               f"บ่อซอง 2 เครื่อง ใช้ไฟรวม ~{p['block_kw']:.0f} kW เดิน ~{p['block_run'] * 100:.0f}% ของเวลา (วัดจากมิเตอร์ + ใบบันทึกเปิด-ปิดเครื่อง)",
               "น้ำแข็งหลอด: ถุงละ 25 กก. · จำนวนถุงคิดจากหน่วยไฟของมิเตอร์ ÷ ไฟต่อถุงของเครื่องที่อยู่บนมิเตอร์นั้น (615: 1.72, 916: 1.82 kWh/ถุง) "
               "หักไฟพื้นฐาน (615 ~70 kW, 916 ~38 kW) และบ่อซองออกก่อน · เทียบกับใบบันทึกเปิด-ปิดเครื่องคลาด ~8%",
               "ค่าไฟแบ่งตามหน่วยไฟ · เกลือ → ซอง · ถุง → หลอด · ซ่อมที่ผูกกับเครื่อง → ตามเครื่อง · ค่าแรง น้ำ เคมี อะไหล่ บริหาร ค่าส่ง → ตามน้ำหนักที่ผลิต",
               "ไม่รวม: น้ำแข็งที่ซื้อจากสาขาอื่น (ซื้อมาขายต่อ) และดอกเบี้ยเงินกู้ · ราคาขายเฉลี่ยจากใบขายในโปรแกรม (หลอดใหญ่ / ซอง) ไม่รวมยอดสายรถ",
           ]}
    _CACHE[branch] = (time.time(), res)
    return res
