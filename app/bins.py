"""
ถัง: every bin lent to a shop is its own record (owner, 11 Nov 2026 -- "each comma is a different tub; would it be hard to trace?").

* A bin = model letter + size in litres + number: V200-1805 and S200-1805 are different bins (158 numbers exist under more than one
  letter in the Word sheets). `num` is the number as an integer (0412 = 412) for finding it; `number` is how it is written.
* It is at one shop since a date (customer_id null = back at the plant / not known). Every change is a row in bin_moves, so a bin's
  whole story can be read back.
* The Word sheets' text (sale_route_customers.bins) was imported once (scripts/import_bins.py). A bin written on two shops' sheets
  went to the first and shows as ถังซ้ำ on the other until someone checks. Bins written without a number ("500L", "T800") are kept
  as text in bins_other. sale_route_customers.bins (what the phone and the printed sheet show) is rebuilt from here whenever the
  office changes a shop's bins.
"""
import re
from datetime import date, datetime, timezone
from typing import Optional

SIZES = [100, 120, 130, 150, 160, 200, 300, 400, 450, 500, 800, 850]
MODELS = ["V", "S", "C", "T", "L", ""]
TOKEN = re.compile(r"([A-Za-z]*)\s*(\d{2,3})\s*-\s*(\d+)")


class BinError(ValueError):
    """Refused, with a message shown as it is."""


def parse(text: Optional[str]) -> tuple[list[dict], str]:
    """'V200-1805, S200-0412, 500L' -> ([{model V, size 200, num 1805, number '1805'}, {S, 200, 412, '0412'}], '500L')."""
    found = [{"model": m.group(1).upper(), "size": int(m.group(2)), "num": int(m.group(3)), "number": m.group(3)}
             for m in TOKEN.finditer(text or "")]
    rest = ", ".join(x.strip() for x in TOKEN.sub("", text or "").split(",") if x.strip())
    return found, rest


def code(b: dict) -> str:
    return f"{b.get('model') or ''}{b['size']}-{b.get('number') or b['num']}"


def clean(b: dict) -> dict:
    """One bin from the office form -> {model, size, num, number}, or BinError."""
    model = str(b.get("model") or "").strip().upper()[:2]
    if not re.fullmatch(r"[A-Z]{0,2}", model):
        raise BinError("รุ่นถังต้องเป็นตัวอักษร เช่น V S C T")
    try:
        size = int(float(str(b.get("size") or "").strip()))
    except ValueError:
        raise BinError("ใส่ขนาดถัง (ลิตร) เช่น 200")
    number = re.sub(r"\D", "", str(b.get("number") or ""))
    if not number or size <= 0:
        raise BinError("ใส่ขนาดและเลขถังให้ครบ")
    return {"model": model, "size": size, "num": int(number), "number": number}


def text_for(bins: list[dict], other: Optional[str]) -> Optional[str]:
    """What the phone and the printed sheet show for a shop's bins."""
    parts = [code(b) for b in sorted(bins, key=lambda x: (x["size"], x.get("model") or "", x["num"]))]
    if other:
        parts.append(other)
    return ", ".join(parts) or None


# --------------------------------------------------------------------------------------------- reading
def for_customers(customer_ids: list[int]) -> dict:
    """{customer id: [bin, ...]} for these shops."""
    from app import supabase
    out: dict = {}
    ids = sorted({int(i) for i in customer_ids if i})
    for i in range(0, len(ids), 150):
        for b in supabase.select_all("bins", {"select": "id,model,size,num,number,customer_id,since,note",
                                              "customer_id": f"in.({','.join(str(x) for x in ids[i:i + 150])})", "order": "size.asc,num.asc"}):
            b["code"] = code(b)
            out.setdefault(b["customer_id"], []).append(b)
    return out


def clashes_for(customers: list[dict], have: dict) -> dict:
    """{customer id: [codes]}: bins the shop's Word sheet lists that the bin records put at ANOTHER shop (ถังซ้ำ -- someone checks)."""
    out = {}
    for c in customers:
        mine = {(b["model"], b["size"], b["num"]) for b in have.get(c["id"], [])}
        lost = [code(b) for b in parse(c.get("bins_legacy"))[0] if (b["model"], b["size"], b["num"]) not in mine]
        if lost:
            out[c["id"]] = lost
    return out


def history(bin_id: int) -> list[dict]:
    from app import supabase
    return supabase.select_all("bin_moves", {"select": "from_customer,to_customer,at,who,note", "bin_id": f"eq.{int(bin_id)}", "order": "at.asc"})


def find(branch: str, text: str) -> list[dict]:
    """Where is this bin? 'V200-1805' / '200-1805' / '1805'."""
    from app import supabase
    got, _ = parse(text)
    params = {"select": "id,model,size,num,number,customer_id,since", "branch": f"eq.{branch}", "order": "size.asc,num.asc", "limit": "50"}
    if got:
        params.update({"size": f"eq.{got[0]['size']}", "num": f"eq.{got[0]['num']}"})
        if got[0]["model"]:
            params["model"] = f"eq.{got[0]['model']}"
    elif re.fullmatch(r"\s*\d+\s*", text or ""):
        params["num"] = f"eq.{int(text)}"
    else:
        return []
    rows = supabase.select_all("bins", params)
    for b in rows:
        b["code"] = code(b)
    return rows


# --------------------------------------------------------------------------------------------- the office sets a shop's bins
def set_for_customer(customer_id: int, branch: str, wanted: list[dict], other: Optional[str], who: str = "office") -> dict:
    """The office's list of a shop's bins: new bins are created, a bin from another shop is MOVED here (kept in bin_moves), a bin no
    longer listed goes back to "not at a shop". The shop's bins text is rebuilt."""
    from app import supabase
    now = datetime.now(timezone.utc).isoformat()
    today = date.today().isoformat()
    want = {}
    for b in wanted or []:
        c = clean(b)
        want[(c["model"], c["size"], c["num"])] = {**c, "since": (b.get("since") or None)}
    current = {(b["model"], b["size"], b["num"]): b for b in for_customers([customer_id]).get(int(customer_id), [])}
    moved = []
    for key, b in want.items():
        if key in current:
            if b["since"] and b["since"] != current[key].get("since"):
                supabase.update("bins", {"id": f"eq.{current[key]['id']}"}, {"since": b["since"]})
            continue
        found = supabase.select("bins", {"select": "id,customer_id", "branch": f"eq.{branch}", "model": f"eq.{b['model']}",
                                         "size": f"eq.{b['size']}", "num": f"eq.{b['num']}", "limit": "1"})
        if found:
            supabase.update("bins", {"id": f"eq.{found[0]['id']}"}, {"customer_id": int(customer_id), "since": b["since"] or today})
            supabase.insert("bin_moves", [{"bin_id": found[0]["id"], "from_customer": found[0]["customer_id"], "to_customer": int(customer_id),
                                           "at": now, "who": who, "note": "ย้ายมาร้านนี้" if found[0]["customer_id"] else "ให้ยืม"}])
            if found[0]["customer_id"]:
                moved.append({"code": code(b), "from": found[0]["customer_id"]})
                _rebuild_text(found[0]["customer_id"])
        else:
            row = supabase.insert("bins", [{"branch": branch, "model": b["model"], "size": b["size"], "num": b["num"], "number": b["number"],
                                            "customer_id": int(customer_id), "since": b["since"] or today}])[0]
            supabase.insert("bin_moves", [{"bin_id": row["id"], "from_customer": None, "to_customer": int(customer_id), "at": now, "who": who,
                                           "note": "ถังใหม่ในระบบ"}])
    for key, b in current.items():
        if key not in want:
            supabase.update("bins", {"id": f"eq.{b['id']}"}, {"customer_id": None, "since": today})
            supabase.insert("bin_moves", [{"bin_id": b["id"], "from_customer": int(customer_id), "to_customer": None, "at": now, "who": who,
                                           "note": "ไม่อยู่ร้านนี้แล้ว"}])
    other = re.sub(r"\s+", " ", other or "").strip()[:200] or None
    supabase.update("sale_route_customers", {"id": f"eq.{int(customer_id)}"}, {"bins_other": other})
    _rebuild_text(int(customer_id))
    return {"moved": moved}


def _rebuild_text(customer_id: int) -> None:
    from app import supabase
    c = supabase.select("sale_route_customers", {"select": "bins_other", "id": f"eq.{int(customer_id)}", "limit": "1"})
    if not c:
        return
    have = for_customers([customer_id]).get(int(customer_id), [])
    supabase.update("sale_route_customers", {"id": f"eq.{int(customer_id)}"}, {"bins": text_for(have, c[0].get("bins_other"))})


# --------------------------------------------------------------------------------------------- the one-time import (scripts/import_bins.py)
def import_from_sheets(write: bool = False) -> dict:
    """Every active shop's Word-sheet bins text -> bin records (first shop in route order wins a bin written on two sheets). The shops'
    bins text is NOT rewritten here (the phone and the paper keep showing what they showed); bins_legacy keeps the original."""
    from app import supabase
    routes = {r["id"]: r for r in supabase.select_all("sale_routes", {"select": "id,truck_no,branch", "order": "id.asc"})}
    custs = supabase.select_all("sale_route_customers", {"select": "id,route_id,seq,bins,bins_legacy", "active": "eq.true", "order": "route_id.asc,seq.asc"})
    reg_since = {}
    for r in supabase.select_all("customer_register", {"select": "customer_id,loan_date,bins", "order": "cust_no.asc"}):
        for b in r.get("bins") or []:
            if r.get("customer_id") and r.get("loan_date"):
                reg_since[(r["customer_id"], int(b["size"]), int(b["no"]))] = r["loan_date"]
    existing = {(b["branch"], b["model"], b["size"], b["num"]) for b in supabase.select_all("bins", {"select": "branch,model,size,num", "order": "id.asc"})}
    new, clash, other_n, seen = [], 0, 0, {}
    for c in custs:
        branch = (routes.get(c["route_id"]) or {}).get("branch") or "สระบุรี"
        text = c.get("bins_legacy") if c.get("bins_legacy") is not None else c.get("bins")
        found, rest = parse(text)
        if write and c.get("bins_legacy") is None:
            supabase.update("sale_route_customers", {"id": f"eq.{c['id']}"}, {"bins_legacy": c.get("bins") or "", "bins_other": rest or None})
        other_n += 1 if rest else 0
        for b in found:
            key = (branch, b["model"], b["size"], b["num"])
            if key in existing or key in seen:
                clash += 1 if seen.get(key) != c["id"] else 0
                continue
            seen[key] = c["id"]
            new.append({"branch": branch, **b, "customer_id": c["id"], "since": reg_since.get((c["id"], b["size"], b["num"]))})
    if write:
        for i in range(0, len(new), 300):
            rows = supabase.insert("bins", new[i:i + 300])
            supabase.insert("bin_moves", [{"bin_id": r["id"], "from_customer": None, "to_customer": r["customer_id"], "who": "นำเข้า",
                                           "note": "จากใบงานคนขับ (Word)"} for r in rows])
    return {"shops": len(custs), "bins": len(new), "written_on_two_sheets": clash, "shops_with_unnumbered": other_n}
