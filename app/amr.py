"""
PEA AMR reports (โครงการพัฒนาการอ่านหน่วยด้วยระบบอัตโนมัติ): 15-minute kWh per meter, per TOU rate -- owner, 9 Nov 2026.

The report downloads as "kWh<CA><timestamp>.xls", which is really an HTML page: a header (month, CA, meter number) and one row per
15 minutes, "dd/mm/yyyy hh.mm" = the END of the slot ("24.00" closes the day), with the kWh in the RATE A (on-peak), B (weekday
off-peak) or C (weekend / holiday) column. Saraburi's reports are already billed kWh (they match the bill to a few units), so
multiplier = 1.

* parse(html) -> {"month", "ca_no", "meter_no", "rows": [{"slot_start", "tou", "kwh"}]}; month None for a report with no data.
"""
import re
from datetime import datetime, timedelta

THAI_MONTHS = ["มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน", "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม",
               "พฤศจิกายน", "ธันวาคม"]
ROW = re.compile(r"&nbsp;(\d\d)/(\d\d)/(\d{4}) (\d\d)\.(\d\d)</td>(.*?)</tr>", re.S)
CELL = re.compile(r"<td class='rightdetail'>([^<]*)</td>")


class NotAmr(ValueError):
    pass


def _field(html: str, label: str):
    m = re.search(label + r"\s*:\s*</td>\s*<td>([^<&]*)", html)
    return m.group(1).strip() if m else None


def parse(html: str) -> dict:
    if "กิโลวัตต์ชั่วโมง" not in html:
        raise NotAmr("ไม่ใช่รายงาน AMR ของ กฟภ.")
    m = re.search(r"ประจำเดือน : (\S+) (\d{4})\]", html)
    month = f"{int(m.group(2)) - 543}-{THAI_MONTHS.index(m.group(1)) + 1:02d}" if m and m.group(1) in THAI_MONTHS else None
    rows = []
    for d, mo, y, hh, mm, rest in ROW.findall(html):
        end = datetime(int(y), int(mo), int(d)) + timedelta(hours=int(hh), minutes=int(mm))
        cells = CELL.findall(rest)[:3]
        for i, v in enumerate(cells):
            if v.strip():
                rows.append({"slot_start": (end - timedelta(minutes=15)).strftime("%Y-%m-%d %H:%M:00+07"), "tou": "ABC"[i], "kwh": float(v)})
                break
    return {"month": month if rows else None, "ca_no": _field(html, "บัญชีผู้ใช้ไฟ"), "meter_no": _field(html, "หมายเลขมิเตอร์"), "rows": rows}


# ---------------------------------------------------------------------------------------------------------------------------------------
# Saving a report, and what the electricity page shows from the readings (บัญชี › ค่าไฟฟ้า, section "มิเตอร์ราย 15 นาที").
#
# The meter only knows the total of everything on it; which machine runs when comes from the crew's LINE start / stop messages.
# What it can tell exactly:
#   * the load through an average working day and day off, per month (the shape: when machines start and stop)
#   * the daytime load (12:00-17:00 on working days) -- what keeps running at the peak price
#   * early starts / late stops on working days (17:00-22:00 / 09:00-12:00): load above the daytime level by at least half of the
#     night's extra load -- billed at the peak price
#   * the highest 15 minutes in peak hours (the demand charge for the whole month) against a normal working day's highest
# ---------------------------------------------------------------------------------------------------------------------------------------
RATE_PEAK, RATE_OFF, RATE_DEMAND = 4.1839, 2.6037, 132.93     # PEA TOU 4.2.2 on the bills (baht per kWh / per kW); Ft is the same for both
MACHINES = {"020019229615": "หลอด 2, 3, 5", "020019227916": "บ่อซอง ×2, หลอด 1, เล็ก 4"}     # what is on each meter -- owner, 9 Nov 2026
DAY = range(48, 68)            # 12:00-16:45: the daytime level
EVENING = range(68, 88)        # 17:00-21:45: an early start shows here
MORNING = range(36, 48)        # 09:00-11:45: a late stop shows here
NIGHT = range(92, 96)          # 23:00-23:45: the night level
DAWN = range(24, 32)           # 06:00-07:45: still the night level


def save(rep: dict, branch: str) -> dict:
    """One parsed report -> elec_amr_readings (a month loaded again is replaced slot by slot)."""
    from app import supabase
    rows = [r | {"branch": branch, "ca_no": rep["ca_no"], "meter_no": rep["meter_no"], "multiplier": 1} for r in rep["rows"]]
    for i in range(0, len(rows), 1000):
        supabase.insert("elec_amr_readings", rows[i:i + 1000], on_conflict="ca_no,slot_start")
    return {"amr": rep["month"], "ca_no": rep["ca_no"], "slots": len(rows)}


def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    return None if not n else (xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2)


def _grid(d: dict):
    kw, tou = [None] * 96, [None] * 96
    for s, k, t in zip(d["s"], d["k"], d["t"]):
        kw[s], tou[s] = float(k), t
    return kw, tou


def _hhmm(slot: int) -> str:
    return f"{slot // 4:02d}:{slot % 4 * 15:02d}"


def _meter_month(mine: list) -> dict:
    """One meter, one month: [(day, kw[96], tou[96])] -> average shapes, daytime level, kWh per rate, highest peak 15 minutes."""
    prof = {"work": [[] for _ in range(96)], "off": [[] for _ in range(96)]}
    day_lv, daily_max, top = [], [], None
    kwh = {"A": 0.0, "B": 0.0, "C": 0.0}
    for d, kw, tou in mine:
        work = "A" in tou
        for i in range(96):
            if kw[i] is None:
                continue
            prof["work" if work else "off"][i].append(kw[i])
            kwh[tou[i]] += kw[i] / 4
            if tou[i] == "A" and (top is None or kw[i] > top[0]):
                top = (kw[i], d, i)
        if work:
            lv = [kw[i] for i in DAY if kw[i] is not None]
            if len(lv) >= 16:
                day_lv.append(_median(lv))
            pk = [kw[i] for i in range(96) if tou[i] == "A" and kw[i] is not None]
            if pk:
                daily_max.append(max(pk))
    normal = _median(daily_max)
    return {"day_kw": round(sum(day_lv) / len(day_lv)) if day_lv else None,
            "work": [round(sum(v) / len(v)) if v else None for v in prof["work"]],
            "off": [round(sum(v) / len(v)) if v else None for v in prof["off"]],
            "kwh": kwh, "peak_kw": round(top[0], 1) if top else None, "peak_at": f"{top[1]} {_hhmm(top[2])}" if top else None,
            "normal_peak_kw": round(normal, 1) if normal is not None else None}


def _shift(tot: list, tou: list, window: range, base, level, last: bool):
    """Slots in a peak-price window where the load is up by at least half of (base - level): (kWh above level, first/last slot)."""
    if base is None or base - level <= 120:
        return None
    ex = [(i, tot[i] - level) for i in window if tot[i] is not None and tou[i] == "A" and tot[i] - level > (base - level) / 2]
    if not ex:
        return None
    return sum(x for _, x in ex) / 4, (ex[-1][0] + 1 if last else ex[0][0])


def analyse(days: list[dict], names: dict) -> dict:
    """days = amr_days() rows {ca, day, s, k, t}; names = {ca_no: meter label}. Pure."""
    premium = RATE_PEAK - RATE_OFF
    meters = sorted({d["ca"] for d in days})
    grid = {(d["ca"], d["day"]): _grid(d) for d in days}
    months = sorted({d["day"][:7] for d in days})
    out = {"meters": [{"ca_no": ca, "name": names.get(ca, ca), "machines": MACHINES.get(ca)} for ca in meters], "months": months,
           "rates": {"peak": RATE_PEAK, "off": RATE_OFF, "demand": RATE_DEMAND}, "by_month": {}, "events": []}
    for m in months:
        mdays = sorted({d for _, d in grid if d[:7] == m})
        bm = {"work_days": 0, "meters": {}, "spikes": [], "early": {"days": 0, "kwh": 0.0}, "late": {"days": 0, "kwh": 0.0}}
        for ca in meters:
            mm = _meter_month([(d, *grid[(ca, d)]) for d in mdays if (ca, d) in grid])
            bm["meters"][ca] = mm
            if mm["peak_kw"] is not None and mm["normal_peak_kw"] is not None and mm["peak_kw"] - mm["normal_peak_kw"] >= 40:
                bm["spikes"].append({"ca_no": ca, "kw": mm["peak_kw"], "at": mm["peak_at"], "normal_kw": mm["normal_peak_kw"],
                                     "baht": round((mm["peak_kw"] - mm["normal_peak_kw"]) * RATE_DEMAND)})
        ms = list(bm["meters"].values())
        for k in ("work", "off"):            # the branch shape = the meters added up (only where every meter has the slot)
            bm[k] = [sum(x[k][i] for x in ms) if all(x[k][i] is not None for x in ms) else None for i in range(96)]
        bm["day_kw"] = sum(x["day_kw"] for x in ms) if all(x["day_kw"] is not None for x in ms) else None
        bm["kwh"] = {t: round(sum(x["kwh"][t] for x in ms)) for t in "ABC"}
        for x in ms:
            x["kwh"] = {t: round(v) for t, v in x["kwh"].items()}
        for d in mdays:                      # early starts / late stops, on the branch total
            gs = [grid.get((ca, d)) for ca in meters]
            if any(g is None for g in gs) or "A" not in gs[0][1]:
                continue
            bm["work_days"] += 1
            tot = [sum(g[0][i] for g in gs) if all(g[0][i] is not None for g in gs) else None for i in range(96)]
            tou = gs[0][1]
            level = _median([tot[i] for i in DAY if tot[i] is not None])
            if level is None:
                continue
            night = _median([tot[i] for i in NIGHT if tot[i] is not None])
            dawn = _median([tot[i] for i in DAWN if tot[i] is not None])
            for kind, window, base, last in (("early", EVENING, night, False), ("late", MORNING, dawn, True)):
                hit = _shift(tot, tou, window, base, level, last)
                if hit:
                    bm[kind]["days"] += 1
                    bm[kind]["kwh"] += hit[0]
                    out["events"].append({"day": d, "kind": kind, "at": _hhmm(hit[1]), "kwh": round(hit[0]), "baht": round(hit[0] * premium)})
        total = sum(bm["kwh"].values())
        bm["peak_share"] = round(bm["kwh"]["A"] / total, 3) if total else None
        bm["peak_premium"] = round(bm["kwh"]["A"] * premium)            # what the peak-rate kWh cost above the off-peak rate
        for k in ("early", "late"):
            bm[k]["kwh"] = round(bm[k]["kwh"])
            bm[k]["baht"] = round(bm[k]["kwh"] * premium)
        out["by_month"][m] = bm
    return out


def summary(branch: str) -> dict:
    from app import supabase
    days = supabase.rpc("amr_days", {"p_branch": branch}) or []
    names = {b["ca_no"]: b.get("address") or b["ca_no"]
             for b in supabase.select_all("elec_bills", {"select": "ca_no,address", "order": "bill_period.asc"})}
    return analyse(days, names)


def one_day(branch: str, day: str) -> dict:
    """Every meter of a branch through one day, 15 minutes at a time (to put next to the LINE start / stop messages)."""
    from datetime import date, timedelta
    from app import supabase
    d = date.fromisoformat(day)
    rows = [r for r in (supabase.rpc("amr_days", {"p_branch": branch, "p_from": d.isoformat()}) or []) if r["day"] == d.isoformat()]
    out = []
    for r in rows:
        kw, tou = _grid(r)
        out.append({"ca_no": r["ca"], "machines": MACHINES.get(r["ca"]), "kw": kw, "tou": "".join(t or "-" for t in tou)})
    return {"day": d.isoformat(), "prev": (d - timedelta(days=1)).isoformat(), "next": (d + timedelta(days=1)).isoformat(), "meters": out}
