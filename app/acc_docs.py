"""
บัญชี › เอกสารเบิก: the mid-month / end-of-month expense documents of a branch, read page by page (needs schema_v42: acc_docs and
acc_entries.doc_id).

* Every page is kept as a row of acc_docs (what it is, the total written on it, the total of the lines read, ok / mismatch). A page
  whose lines add up to its written total needs no review; only mismatches come up.
* Every money line becomes one accrual entry (source = the feed, midmonth / endmonth), paid from cash: the expense account (or 1500
  employee advances for the mid-month advance lists, which are not a cost), with the truck on cash-fuel / tyre / repair lines.
* Memos and HR papers are filed with a one-line summary (no money).
* fuel_check: cash fuel per truck for a period against the km it drove (GPS where there is one, else the odometer readings), and
  against the office's typed fuel table.

The documents themselves are transcribed into data/acc_docs/<month>_<branch>.py (BATCH) and loaded with
scripts/import_acc_docs.py. build() is pure and unit-tested.
"""
import json
import re
from datetime import date, timedelta
from typing import Optional

from app import supabase

KINDS = ("cover", "expense", "fuel", "fuel_table", "advance", "memo", "other")


class BadBatch(ValueError):
    pass


def norm_plate(s: str) -> str:
    return re.sub(r"[\s\-]", "", s or "")


def line_date(dm: str, month: str) -> date:
    """'26/9' in the batch of company month '2026-10' -> 2026-09-26 (the year of the month, or the year before for a December
    line in a January month)."""
    d, m = (int(x) for x in dm.split("/"))
    y, mm = (int(x) for x in month.split("-"))
    return date(y - 1 if m > mm + 1 else y, m, d)


def build(batch: dict, vehicles: list[dict]) -> dict:
    """The rows to write for one batch: {docs: [...], entries: [...], problems: [...]}. Each entry carries `doc_page` to link it
    to its doc after insert. A page whose lines do not add up to its written total is a 'mismatch' (kept, flagged)."""
    month, branch, name = batch["month"], batch["branch"], batch["batch"]
    by_plate = {norm_plate(v["plate_number"]): v for v in vehicles if v.get("plate_number")}
    by_number = {}
    for v in vehicles:
        if v.get("vehicle_number") and (v["vehicle_number"] not in by_number or v.get("branch") == branch):
            by_number[v["vehicle_number"]] = v
    y, m = (int(x) for x in month.split("-"))
    pm_y, pm = (y - 1, 12) if m == 1 else (y, m - 1)
    month_from, month_to = date(pm_y, pm, 26), date(y, m, 25)
    docs, entries, problems = [], [], []
    seen_pages = set()

    def doc(p: dict, kind: str, **extra) -> dict:
        if p["page"] in seen_pages:
            raise BadBatch(f"page {p['page']} twice")
        seen_pages.add(p["page"])
        if kind not in KINDS:
            raise BadBatch(f"kind {kind}")
        return {"batch": name, "feed": p.get("feed"), "branch": branch, "file_name": batch["files"].get(p.get("file"), p.get("file")),
                "page": p["page"], "kind": kind, "title": p.get("title"), "written_total": p.get("written_total"),
                "note": p.get("note"), "summary": p.get("summary"), "person": p.get("person"), **extra}

    for p in batch["pages"]:
        if p["kind"] not in ("expense", "fuel", "advance"):
            raise BadBatch(f"page {p['page']}: kind {p['kind']}")
        total = 0.0
        dates = []
        for i, (dm, text, amount, account, party, opt) in enumerate(p["lines"], 1):
            d = line_date(dm, month)
            dates.append(d)
            amount = round(float(amount), 2)
            if amount <= 0:
                raise BadBatch(f"page {p['page']} line {i}: amount")
            total += amount
            v = None
            if opt.get("v"):
                v = by_plate.get(norm_plate(opt["v"])) or by_number.get(str(opt["v"]))
                if not v:
                    problems.append(f"หน้า {p['page']} บรรทัด {i}: ไม่พบรถ {opt['v']}")
            line_branch = opt.get("branch") or (v or {}).get("branch") or branch
            credit = "1100"
            entries.append({"doc_page": p["page"], "entry_date": d.isoformat(), "branch": line_branch, "source": p["feed"],
                            "source_id": f"{name}:p{p['page']}:l{i}", "party": party, "description": text + (f" — {opt['note']}" if opt.get("note") else ""),
                            "doc_type": "petty_cash", "doc_no": f"หน้า {p['file_page']}",
                            "lines": [{"account": account, "debit": amount, "credit": 0.0, "branch": line_branch, "vehicle_id": (v or {}).get("id"), "memo": opt.get("note")},
                                      {"account": credit, "debit": 0.0, "credit": amount, "branch": line_branch, "vehicle_id": None, "memo": None}]})
        total = round(total, 2)
        status = "ok" if p.get("written_total") is None or abs(total - p["written_total"]) < 0.5 else "mismatch"
        if status == "mismatch":
            problems.append(f"หน้า {p['page']} ({p['title']}): อ่านได้ {total:,.0f} เขียนไว้ {p['written_total']:,.0f}")
        docs.append(doc(p, p["kind"], read_total=total, status=status, date_from=min(dates).isoformat(), date_to=max(dates).isoformat()))

    for c in batch.get("covers", []):
        parts = c.get("parts", {})
        status = "ok" if abs(sum(parts.values()) - c["written_total"]) < 0.5 else "mismatch"
        # each part must equal the pages of that kind in the same feed
        for kind, want in parts.items():
            got = sum(d["read_total"] for d in docs if d["feed"] == c["feed"] and d["kind"] == kind)
            wrote = sum(d["written_total"] or 0 for d in docs if d["feed"] == c["feed"] and d["kind"] == kind)
            if abs(wrote - want) >= 0.5:
                status = "mismatch"
                problems.append(f"ปก {c['title']}: {kind} หน้าต่างๆ เขียนรวม {wrote:,.0f} ปกเขียน {want:,.0f}")
            elif abs(got - want) >= 0.5:
                problems.append(f"ปก {c['title']}: {kind} อ่านได้ {got:,.0f} ปกเขียน {want:,.0f} (มีหน้าที่อ่านไม่ตรง)")
        docs.append(doc(c, "cover", read_total=round(sum(parts.values()), 2), status=status, date_from=c["from"], date_to=c["to"],
                        summary=json.dumps(parts, ensure_ascii=False)))

    ft = batch.get("fuel_table")
    if ft:
        s = sum(ft["by_truck"].values())
        docs.append(doc(ft, "fuel_table", read_total=s, status="ok" if abs(s - ft["written_total"]) < 0.5 else "mismatch",
                        date_from=month_from.isoformat(), date_to=month_to.isoformat(), summary=json.dumps(ft["by_truck"], ensure_ascii=False)))
    for mm in batch.get("memos", []):
        docs.append(doc(mm, mm.get("kind", "memo"), status="ok", date_from=month_from.isoformat(), date_to=month_to.isoformat()))
    return {"docs": docs, "entries": entries, "problems": problems}


def import_batch(batch: dict) -> dict:
    """Write one batch, replacing what an earlier import of the same batch wrote (its docs cascade to their entries)."""
    from app.accounting import _insert_entries, _require
    _require()
    vehicles = supabase.select_all("vehicles", {"select": "id,vehicle_number,plate_number,branch", "order": "id.asc"})
    out = build(batch, vehicles)
    supabase.delete("acc_docs", {"batch": f"eq.{batch['batch']}"})
    supabase.delete("acc_entries", {"source_id": f"like.{batch['batch']}:*"})
    cols = sorted({k for d in out["docs"] for k in d})
    rows = supabase.insert("acc_docs", [{k: d.get(k) for k in cols} for d in out["docs"]])
    ids = {r["page"]: r["id"] for r in rows}
    for e in out["entries"]:
        e["doc_id"] = ids[e.pop("doc_page")]
    _insert_entries(out["entries"])
    return {"docs": len(rows), "entries": len(out["entries"]), "problems": out["problems"]}


# ------------------------------------------------------------------ fuel per truck against km
def km_rate(readings: list[dict], start: date, end: date) -> tuple[Optional[float], Optional[str]]:
    """km a day for one truck around [start, end]: GPS periods that overlap it first, else the odometer readings nearest to it."""
    km = days = 0
    for r in readings:
        if r.get("kind") != "gps" or not r.get("start_date") or not r.get("end_date"):
            continue
        s, e = date.fromisoformat(r["start_date"]), date.fromisoformat(r["end_date"])
        if e >= start - timedelta(days=30) and s <= end + timedelta(days=30):
            km += r["km"]
            days += (e - s).days + 1
    if days >= 7:
        return km / days, "GPS"
    pts = sorted((date.fromisoformat(r["end_date"]), r["km"]) for r in readings if r.get("kind") == "odometer" and r.get("end_date") and r.get("km"))
    near = [p for p in pts if start - timedelta(days=150) <= p[0] <= end + timedelta(days=60)]
    if len(near) >= 2 and (near[-1][0] - near[0][0]).days >= 14 and near[-1][1] > near[0][1]:
        return (near[-1][1] - near[0][1]) / (near[-1][0] - near[0][0]).days, "เลขไมล์"
    return None, None


def fuel_check(start: date, end: date, branch: str = "") -> dict:
    """Cash fuel (6130) per truck in [start, end], baht per km, and the office's typed table beside the handwritten lists."""
    entries = {e["id"]: e for e in supabase.select_all("acc_entries", {"select": "id,entry_date,source_id", "entry_date": f"gte.{start.isoformat()}",
                                                                        "and": f"(entry_date.lte.{end.isoformat()})", "order": "id.asc"})}
    ids = list(entries)
    lines = []
    for i in range(0, len(ids), 100):
        params = {"select": "entry_id,account,debit,vehicle_id,branch", "account": "in.(6120,6130)", "entry_id": f"in.({','.join(ids[i:i + 100])})", "order": "id.asc"}
        if branch:
            params["branch"] = f"eq.{branch}"
        lines += supabase.select_all("acc_lines", params)
    if not lines:
        return {"trucks": [], "no_truck": 0, "table": None}
    vehicles = {v["id"]: v for v in supabase.select_all("vehicles", {"select": "id,vehicle_number,plate_number,branch", "order": "id.asc"})}
    per: dict = {}
    no_truck = 0.0
    for ln in lines:
        if not ln.get("vehicle_id"):
            no_truck += float(ln["debit"])
            continue
        t = per.setdefault(ln["vehicle_id"], {"baht": 0.0, "cash": 0.0, "card": 0.0, "fills": 0})
        t["baht"] += float(ln["debit"])
        t["card" if ln["account"] == "6120" else "cash"] += float(ln["debit"])
        t["fills"] += ln["account"] == "6130"
    has_card = any(t["card"] for t in per.values())
    readings = supabase.select_all("km_readings", {"select": "vehicle_id,kind,start_date,end_date,km", "vehicle_id": f"in.({','.join(per)})", "order": "id.asc"})
    days = (end - start).days + 1
    out = []
    for vid, t in per.items():
        v = vehicles.get(vid, {})
        rate, src = km_rate([r for r in readings if r["vehicle_id"] == vid], start, end)
        km = round(rate * days) if rate else None
        out.append({"vehicle_id": vid, "number": v.get("vehicle_number"), "plate": v.get("plate_number"), "branch": v.get("branch"),
                    "baht": round(t["baht"], 2), "cash": round(t["cash"], 2), "card": round(t["card"], 2), "fills": t["fills"], "km": km, "km_source": src, "baht_per_km": round(t["baht"] / km, 2) if km else None})
    known = sorted(x["baht_per_km"] for x in out if x["baht_per_km"])
    median = known[len(known) // 2] if known else None
    for x in out:
        x["high"] = bool(has_card and median and x["baht_per_km"] and x["baht_per_km"] > median * 2)
    out.sort(key=lambda x: -x["baht"])
    table = None
    docs = supabase.select_all("acc_docs", {"select": "summary,written_total,title", "kind": "eq.fuel_table", "date_from": f"lte.{end.isoformat()}",
                                            "date_to": f"gte.{start.isoformat()}", "order": "page.asc"})
    if docs:
        typed = json.loads(docs[0]["summary"] or "{}")
        table = {"title": docs[0]["title"], "total": docs[0]["written_total"], "by_truck": typed}
        for x in out:
            x["typed"] = typed.get(str(x["number"]))
            x["typed_diff"] = round(x["cash"] - (x["typed"] or 0), 2)
    return {"trucks": out, "no_truck": round(no_truck, 2), "median_baht_per_km": median, "has_card": has_card, "table": table, "days": days}


def docs_for(start: date, end: date, branch: str = "") -> list[dict]:
    params = {"select": "id,batch,feed,branch,file_name,page,kind,title,date_from,date_to,written_total,read_total,status,note,summary,person",
              "date_from": f"lte.{end.isoformat()}", "date_to": f"gte.{start.isoformat()}", "order": "batch.asc,page.asc"}
    if branch:
        params["branch"] = f"eq.{branch}"
    return supabase.select_all("acc_docs", params)


def mark_checked(doc_id: str) -> None:
    supabase.update("acc_docs", {"id": f"eq.{doc_id}"}, {"status": "checked"})
