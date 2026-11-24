"""
บัญชี › ตรวจความถูกต้อง (the checks; owner, 24 Nov 2026): after the month's files are booked, prove that nothing is counted twice and
nothing is left hanging. The owner's rule for the drop box is "one source books a cost, the others only pay or prove it" -- these
checks are where that rule is tested against what is actually in acc_entries / acc_lines, month by month, branch by branch.

run(month, branch="") -> {"month", "branch", "checks": [...]}: six checks, each once per branch (both branches when branch is ""):

  1. petty_cash   เงินสดย่อยลงตัว -- account 1100 (cash at the plant): money IN (Dr: the HQ cheque rows that top up petty cash, a
                  payroll pay-back if a reader books one that way) and money OUT (Cr: the mid / end-of-month papers, payroll's net pay,
                  the estimated cash repairs ...), both sides by source. The plain month total never nets out on the real books (dry
                  run, 24 Nov 2026: Saraburi 2026-07 in 1,127,869 / out 1,676,595): the end-of-month top-up arrives with NEXT month's
                  cheque run, and payroll's Cr 1100 is the whole net pay while the cheque only sends the cash wages. So the state
                  comes from matching each top-up to what it pays for, by the cheque's date (see petty_cash()): mid-month top-up vs
                  mid-month papers (ok within 1% or 1,000 baht, warn up to 5%, bad beyond), next month's end-of-month top-up vs this
                  month's end papers (the rest is the cash wages), and a top-up equal to last month's to the baht = warn.
  2. advances     เงินเบิกล่วงหน้าหักคืนครบ -- account 1500: the mid-month advance lists (Dr) against the payroll deduction
                  เบิกล่วงหน้า (Cr). Same thresholds.
  3. bills        บิลค้างจ่ายถูกจ่ายครบ -- account 2100, bill by bill: a PEA bill (source electricity, source_id pea:<invoice>) against
                  the cheque rows that pay it (Dr 2100, "จ่ายใบแจ้ง PEA <invoice>" in the description, any month); factory purchase and
                  truck repair bills against the payments ticked on #payables; anything else owed (card fuel: the stations' monthly
                  bills have no payer in the app yet) is shown as info with its amount, never bad. Overpaid / paid twice / a payment
                  for a bill that is not in the books = bad; a PEA bill still unpaid after a later cheque run is in = warn.
                  elec_bills is cross-checked too: every stored PEA bill of the month must have its entry, with the same amount.
  4. file_totals  ยอดรวมของแต่ละไฟล์ตรงกับที่ลงบัญชี -- every booked drop-box file of the month (inbox_files, status booked): the
                  total PRINTED in the file vs what its rows add up to, and what its preview booked vs what is in acc_entries NOW for
                  that batch key (source_id == key or key:*). More than 1 baht apart = bad (a file read short, or a batch deleted /
                  doubled after booking).
  5. lookalikes   รายการที่อาจนับซ้ำ -- the same money booked from two DIFFERENT sources: the same amount (within 1 baht) with a
                  similar party / description (Thai / English company words, spaces and tone marks ignored), or the same document
                  number in both (doc_no, '#PC6906-081' tags in descriptions). Pairs that are SUPPOSED to look alike are left out:
                  one side creates what is owed / held and the other clears it (Dr X on one, Cr X on the other for 2100 / 1100 / 1500 /
                  1510 / 1600 / 2400: a bill and the cheque that pays it, petty-cash funding and the papers, an advance and its
                  deduction), and internal branch ice (4150 / 5060) against the 1600 that settles it. The window is this month and
                  the months either side (an HQ cheque for "last month's bill" is dated an estimated 25th, so the 2026-07 purchase
                  bill PC6906-081 dated 26 Jun and its cheque dated 25 Jun sit one day apart in two company months). warn.
  6. vs_usual     เทียบกับเดือนปกติ -- every P&L account of the branch this month against the mean of up to the 6 previous months
                  that have books (a month counts when the branch has any P&L line in it; an account missing that month counts as 0):
                  warn when it is more than +50% AND the jump is more than 20,000 baht, or less than -50% with a usual of more than
                  20,000 (a cost that has not come in yet looks like this too); info when there is no history to compare with, or for
                  an account that is new this month (>= 20,000).

Every check: {"key", "branch", "title" (Thai), "state": ok / warn / bad / info, "summary" (Thai, one line with the numbers),
"numbers": {...}, "rows": [{"text", "amount", "source", "source_id", "entry_id", "date"}] (at most 50, biggest first)}.

The work is in pure functions on plain rows (entries with their lines) -- unit-tested in tests/test_inbox_checks.py with fake rows. The
database part (_load) only READS: acc_accounts, acc_lines joined with acc_entries, elec_bills, inbox_files, the #payables tables.
"""
from __future__ import annotations

import re
from datetime import date
from difflib import SequenceMatcher
from typing import Optional

from app import supabase
from app.inbox_common import BRANCHES, entry_amount, month_label, shift_month

# ------------------------------------------------------------------ constants
CLEARING = ("2100", "1100", "1500", "1510", "1600", "2400")   # opened on one side, cleared on the other: a Dr/Cr pair here is not a double
INTERNAL = ("4150", "5060")                                   # ice sold to / bought from the other branch (acc_sales)
MAX_ROWS = 50
OK_ABS, OK_PCT, WARN_PCT = 1000.0, 0.01, 0.05                 # petty cash / advances: ok within 1% or 1,000 baht, warn up to 5%
JUMP_PCT, JUMP_ABS = 0.50, 20000.0                            # vs_usual: +50% and > 20,000 baht (or -50% of a usual > 20,000)
HISTORY_MONTHS = 6
LOOKALIKE_MIN = 1000.0                                        # amount + similar name: below this, small repeat buys (gas, water) are noise
LOOKALIKE_DAYS = 45                                           # the two rows of a look-alike pair are at most this far apart
TITLES = {"petty_cash": "เงินสดย่อยลงตัว", "advances": "เงินเบิกล่วงหน้าหักคืนครบ", "bills": "บิลค้างจ่ายถูกจ่ายครบ",
          "file_totals": "ยอดรวมของแต่ละไฟล์ตรงกับที่ลงบัญชี", "lookalikes": "รายการที่อาจนับซ้ำ (ต่างแหล่งข้อมูล)",
          "vs_usual": "เทียบกับเดือนปกติ"}
ORDER = ("petty_cash", "advances", "bills", "file_totals", "lookalikes", "vs_usual")
SOURCE_TH = {"hq_cheques": "เช็ค HQ", "mum_cheques": "เช็คคุณรัชนิดา", "midmonth": "เอกสารเบิกกลางเดือน", "endmonth": "เอกสารเบิกสิ้นเดือน",
             "payroll": "เงินเดือน", "electricity": "ค่าไฟ PEA", "card_fuel": "น้ำมันบัตร", "purchase": "บิลซื้อของโรงงาน",
             "truck_bill": "บิลซ่อมรถ", "sales_program": "ยอดขาย (โปรแกรม)", "driver_sales": "ยอดขายสายรถ", "repair_estimate": "ค่าซ่อมรถประมาณ",
             "adjustment": "ปรับปรุง", "loan": "เงินกู้", "manual": "ลงเอง"}
_SEVERITY = {"ok": 0, "info": 1, "warn": 2, "bad": 3}


def source_th(source: Optional[str]) -> str:
    return SOURCE_TH.get(source or "", source or "-")


def money(x) -> float:
    try:
        return round(float(x or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def baht(x: float) -> str:
    return f"{x:,.0f}"


def worst(*states: str) -> str:
    return max(states, key=lambda s: _SEVERITY[s]) if states else "ok"


def dmy(iso: Optional[str]) -> str:
    """'2026-10-25' -> '25/10/2026' (the owner's date format)."""
    s = str(iso or "")[:10]
    return f"{s[8:10]}/{s[5:7]}/{s[:4]}" if len(s) == 10 else s


def line_branch(line: dict, entry: dict) -> Optional[str]:
    return line.get("branch") or entry.get("branch")


def entry_branches(entry: dict) -> set:
    return {line_branch(ln, entry) for ln in entry.get("lines") or []} | {entry.get("branch")}


def in_bounds(entry: dict, start: date, end: date) -> bool:
    return start.isoformat() <= str(entry.get("entry_date") or "")[:10] <= end.isoformat()


def row_of(entry: dict, amount: float, text: Optional[str] = None, **extra) -> dict:
    """One row of a check, pointing at the entry it is about."""
    t = text if text is not None else " · ".join(x for x in (source_th(entry.get("source")), entry.get("party") or "", short_desc(entry.get("description"))) if x)
    return {"text": t, "amount": round(amount, 2), "source": entry.get("source"), "source_id": entry.get("source_id"),
            "entry_id": entry.get("id"), "date": str(entry.get("entry_date") or "")[:10] or None, **extra}


def top_rows(rows: list[dict], key=None) -> list[dict]:
    return sorted(rows, key=key or (lambda r: -abs(r.get("amount") or 0)))[:MAX_ROWS]


def short_desc(desc: Optional[str]) -> str:
    """The first part of a description ('น้ำมันคอม 2 ถัง Castrol · เช็ค 10201535 · ...' -> 'น้ำมันคอม 2 ถัง Castrol')."""
    return str(desc or "").split(" · ")[0].strip()


def check(key: str, branch: str, state: str, summary: str, numbers: dict, rows: list[dict]) -> dict:
    return {"key": key, "branch": branch, "title": TITLES[key], "state": state, "summary": summary, "numbers": numbers, "rows": top_rows(rows)}


# ------------------------------------------------------------------ 1 + 2: an account that must net out (pure)
def sides(entries: list[dict], account: str, branch: str, start: date, end: date) -> dict:
    """Money in (Dr) and out (Cr) of one account for one branch in the month, by source, and the entries behind each side."""
    out = {"in": {}, "out": {}, "in_rows": [], "out_rows": []}
    for e in entries:
        if not in_bounds(e, start, end):
            continue
        dr = cr = 0.0
        for ln in e.get("lines") or []:
            if ln.get("account") != account or line_branch(ln, e) != branch:
                continue
            dr += money(ln.get("debit"))
            cr += money(ln.get("credit"))
        src = e.get("source") or "-"
        if dr:
            out["in"][src] = round(out["in"].get(src, 0.0) + dr, 2)
            out["in_rows"].append(row_of(e, dr))
        if cr:
            out["out"][src] = round(out["out"].get(src, 0.0) + cr, 2)
            out["out_rows"].append(row_of(e, cr))
    return out


def balance_state(money_in: float, money_out: float) -> str:
    """ok within 1% or 1,000 baht, warn up to 5%, bad beyond; nothing at all = info; only one side = warn (the other side's papers
    are not in yet, which is not the same as wrong)."""
    if not money_in and not money_out:
        return "info"
    if not money_in or not money_out:
        return "warn"
    diff, base = abs(money_in - money_out), max(money_in, money_out)
    if diff <= OK_ABS or diff <= OK_PCT * base:
        return "ok"
    return "warn" if diff <= WARN_PCT * base else "bad"


def net_check(key: str, entries: list[dict], account: str, branch: str, start: date, end: date, in_word: str, out_word: str) -> dict:
    s = sides(entries, account, branch, start, end)
    tin, tout = round(sum(s["in"].values()), 2), round(sum(s["out"].values()), 2)
    diff = round(tin - tout, 2)
    state = balance_state(tin, tout)
    pct = abs(diff) / max(tin, tout) * 100 if max(tin, tout) else 0.0
    if state == "info":
        summary = f"ไม่มีรายการบัญชี {account} เดือนนี้"
    else:
        summary = f"{in_word} {baht(tin)} · {out_word} {baht(tout)} · ต่าง {diff:+,.0f} ({pct:.1f}%)"
        if state == "warn" and (not tin or not tout):
            summary += f" — ยังไม่มีฝั่ง{in_word if not tin else out_word}"
    rows = ([{"text": f"{in_word}: {source_th(src)}", "amount": v, "source": src, "source_id": None, "side": "in"} for src, v in s["in"].items()]
            + [{"text": f"{out_word}: {source_th(src)}", "amount": v, "source": src, "source_id": None, "side": "out"} for src, v in s["out"].items()])
    rows = sorted(rows, key=lambda r: -r["amount"])
    # the individual entries after the per-source totals: every money-in row (few: the cheques), then the biggest money-out rows
    rows += sorted((dict(r, side="in") for r in s["in_rows"]), key=lambda r: -r["amount"])
    rows += sorted((dict(r, side="out") for r in s["out_rows"]), key=lambda r: -r["amount"])
    return {"key": key, "branch": branch, "title": TITLES[key], "state": state, "summary": summary,
            "numbers": {"account": account, "in": tin, "out": tout, "diff": diff, "pct": round(pct, 2), "in_by_source": s["in"], "out_by_source": s["out"]},
            "rows": rows[:MAX_ROWS]}


MID_FUND = re.compile(r"เบิกกลางเดือน")                       # คชจ.+เบิกกลางเดือน, ... เบิกกลางเดือนไม่พอ: the mid-month papers' cash
END_FUND = re.compile(r"สิ้นเดือน|เงินเดือนสด")                  # เงินเดือนสด+ค่าใช้จ่ายสิ้นเดือน: last month's end papers + cash wages
_LABEL_MONTH = re.compile(r"(?<!\d)(\d{1,2})\s*[-/]\s*(25\d\d|\d\d)(?!\d)")


def funding_kind(entry: dict) -> str:
    """'mid' (the mid-month papers' cash), 'end' (last month's end papers + cash wages) or 'other' -- from the cheque row's words."""
    d = str(entry.get("description") or "")
    return "mid" if MID_FUND.search(d) else "end" if END_FUND.search(d) else "other"


def label_month(entry: dict) -> Optional[str]:
    """The month a funding row's label names ('คชจ.+เบิกกลางเดือน 7/2569' -> '2026-07', '... 06-69' -> '2026-06'), or None."""
    hits = _LABEL_MONTH.findall(short_desc(entry.get("description")))
    if not hits:
        return None
    mo, y = int(hits[-1][0]), int(hits[-1][1])
    y = (y if y > 2400 else 2500 + y) - 543
    return f"{y}-{mo:02d}" if 1 <= mo <= 12 else None


def _papers_out(entries: list[dict], source: str, branch: str, start: date, end: date) -> float:
    return round(sum(sum_lines(e, "1100", "credit", branch) for e in entries if e.get("source") == source and in_bounds(e, start, end)), 2)


def _short_state(want: float, got: float) -> str:
    """want = what the papers spent, got = what was sent for them: short by more than 1% and 1,000 = warn, more than 5% = bad."""
    short = want - got
    if short <= OK_ABS or short <= OK_PCT * want:
        return "ok"
    return "warn" if short <= WARN_PCT * want else "bad"


def petty_cash(window: list[dict], branch: str, month: str) -> dict:
    """Account 1100 of one branch. The plain month total (money in vs out, by source) is shown, but on the real books it never nets
    out by itself: the end-of-month top-up comes with NEXT month's cheque run, and payroll's Cr 1100 is the whole net pay (cash AND
    bank transfer) while the cheque only sends the cash wages. So the state comes from matching each top-up with what it pays for,
    by the cheque's DATE -- HQ's labels are often last month's (owner, 24 Nov 2026: never trust one label), the label is only shown:
      * mid-month: the "คชจ.+เบิกกลางเดือน" cheques of this month vs this month's mid-month papers (ok within 1% / 1,000, warn 5%, bad);
      * end of month: the "เงินเดือนสด+ค่าใช้จ่ายสิ้นเดือน" cheques of NEXT month vs this month's end-of-month papers -- what is left
        over is the cash wages (shown); short of the papers = warn / bad; not in yet = info;
      * a top-up of exactly the same amount as last month's = warn (a cheque sheet copied from the month before)."""
    from app.accounting import period_bounds
    start, end = period_bounds(month)
    p_start, p_end = period_bounds(shift_month(month, -1))
    n_start, n_end = period_bounds(shift_month(month, 1))
    raw = sides(window, "1100", branch, start, end)
    tin, tout = round(sum(raw["in"].values()), 2), round(sum(raw["out"].values()), 2)

    def funding(s: date, e: date) -> list[tuple[dict, float, str]]:
        return [(x, sum_lines(x, "1100", "debit", branch), funding_kind(x)) for x in window
                if in_bounds(x, s, e) and sum_lines(x, "1100", "debit", branch) and x.get("source") not in ("midmonth", "endmonth")]
    this, prev, nxt = funding(start, end), funding(p_start, p_end), funding(n_start, n_end)
    mid_in = round(sum(a for _, a, k in this if k == "mid"), 2)
    mid_out = _papers_out(window, "midmonth", branch, start, end)
    end_in = round(sum(a for _, a, k in nxt if k == "end"), 2)
    end_out = _papers_out(window, "endmonth", branch, start, end)
    states, rows = [balance_state(mid_in, mid_out)], []
    rows.append({"text": f"กลางเดือน: เช็คเติม {baht(mid_in)} · เอกสารเบิกกลางเดือน {baht(mid_out)} · ต่าง {mid_in - mid_out:+,.0f}",
                 "amount": round(mid_in - mid_out, 2), "source": "midmonth", "source_id": None, "entry_id": None, "date": None})
    if end_in:
        states.append(_short_state(end_out, end_in))
        rows.append({"text": f"สิ้นเดือน: เช็คเติม (รอบเดือนถัดไป) {baht(end_in)} · เอกสารเบิกสิ้นเดือน {baht(end_out)} · "
                             + (f"ที่เหลือ {baht(end_in - end_out)} เป็นเงินเดือนสด" if end_in >= end_out else f"ขาด {baht(end_out - end_in)}"),
                     "amount": round(end_in - end_out, 2), "source": "endmonth", "source_id": None, "entry_id": None, "date": None})
    elif end_out:
        states.append("info")
        rows.append({"text": f"สิ้นเดือน: เอกสารเบิกสิ้นเดือน {baht(end_out)} · เช็คเติมยังไม่มา (มากับรอบเช็คเดือนหน้า)", "amount": end_out,
                     "source": "endmonth", "source_id": None, "entry_id": None, "date": None})
    for x, a, k in this:
        same = [y for y, b, kk in prev if kk == k and abs(b - a) <= 1]
        if same:
            states.append("warn")
            rows.append(row_of(x, a, f"เช็คเติมเงินสดยอดเท่ากับเดือนก่อนทุกบาท ({baht(a)}) — ใบสรุปเช็คอาจคัดลอกมาจากเดือนก่อน · "
                                      f"{short_desc(x.get('description'))} · {dmy(x.get('entry_date'))} / ก่อนหน้า {dmy(same[0].get('entry_date'))}"))
        lm = label_month(x)
        want = month if k == "mid" else shift_month(month, -1) if k == "end" else None
        if lm and want and lm != want:
            rows.append(row_of(x, a, f"ป้ายในใบเช็คบอก {month_label(lm)} แต่วันที่เช็คเป็นของ {month_label(want)} · {short_desc(x.get('description'))}",
                               note="label"))
        if k == "other":
            rows.append(row_of(x, a, f"เติมเงินสดอื่นๆ (ไม่ได้บอกว่ากลางหรือสิ้นเดือน) · {short_desc(x.get('description'))}"))
    state = worst(*states)
    summary = (f"กลางเดือน: เติม {baht(mid_in)} ใช้ {baht(mid_out)} · สิ้นเดือน: "
               + (f"เติม {baht(end_in)} เอกสาร {baht(end_out)} เหลือเป็นเงินเดือนสด {baht(end_in - end_out)}" if end_in
                  else f"เอกสาร {baht(end_out)} เช็คเติมยังไม่มา")
               + f" · ทั้งเดือน เงินเข้า {baht(tin)} จ่ายออก {baht(tout)}")
    if not tin and not tout and not end_in:
        state, summary = "info", "ไม่มีรายการเงินสดเดือนนี้"
    rows += [{"text": f"เงินเข้า: {source_th(src)}", "amount": v, "source": src, "source_id": None, "entry_id": None, "date": None, "side": "in"}
             for src, v in sorted(raw["in"].items(), key=lambda kv: -kv[1])]
    rows += [{"text": f"จ่ายออก: {source_th(src)}", "amount": v, "source": src, "source_id": None, "entry_id": None, "date": None, "side": "out"}
             for src, v in sorted(raw["out"].items(), key=lambda kv: -kv[1])]
    rows += sorted((dict(r, side="in") for r in raw["in_rows"]), key=lambda r: -r["amount"])
    return {"key": "petty_cash", "branch": branch, "title": TITLES["petty_cash"], "state": state, "summary": summary,
            "numbers": {"account": "1100", "in": tin, "out": tout, "diff": round(tin - tout, 2), "in_by_source": raw["in"], "out_by_source": raw["out"],
                        "mid_in": mid_in, "mid_out": mid_out, "end_in_next_run": end_in, "end_out": end_out,
                        "cash_wages": round(end_in - end_out, 2) if end_in else None},
            "rows": rows[:MAX_ROWS]}


def advances(entries: list[dict], branch: str, start: date, end: date) -> dict:
    return net_check("advances", entries, "1500", branch, start, end, "เบิกไป", "หักคืน")


# ------------------------------------------------------------------ 3: bills owed go to zero (pure)
PEA_REF = re.compile(r"PEA\s*(\d{6,})")


def pea_invoice_paid(entry: dict) -> Optional[str]:
    """The PEA invoice a payment names ('... · จ่ายใบแจ้ง PEA 296011397238 · ...'), or None."""
    m = PEA_REF.search(str(entry.get("description") or ""))
    return m.group(1) if m else None


def sum_lines(entry: dict, account: str, side: str, branch: Optional[str] = None) -> float:
    return round(sum(money(ln.get(side)) for ln in entry.get("lines") or [] if ln.get("account") == account
                     and (branch is None or line_branch(ln, entry) == branch)), 2)


def bills_check(owed: list[dict], branch: str, start: date, end: date, *, elec_bills: list[dict], later_run: bool,
                shop_bills: Optional[list[dict]] = None) -> dict:
    """owed = every entry that touches 2100 (any month: a bill of this month may be paid next month, and a payment this month may
    clear an older bill), with its lines. elec_bills = the stored PEA bills of this month. later_run = a cheque run later than this
    month is already booked (so an unpaid PEA bill of this month should have been paid by now). shop_bills = #payables' view of the
    purchase / truck bills ({source: 'purchase'|'truck', id, amount, state: paid|now|due, changed, shop, branch}), None if unknown."""
    rows, states, nums = [], [], {"pea_owed": 0.0, "pea_paid": 0.0, "pea_unpaid": 0.0, "shop_owed": 0.0, "shop_unpaid": 0.0,
                                  "no_payer": {}, "unknown_payments": 0.0}
    bills = [e for e in owed if e.get("source") == "electricity" and str(e.get("source_id") or "").startswith("pea:")]
    payers: dict = {}
    for e in owed:
        dr = sum_lines(e, "2100", "debit")
        if not dr:
            continue
        inv = pea_invoice_paid(e)
        if inv:
            payers.setdefault(inv, []).append((e, dr))
        elif in_bounds(e, start, end) and branch in entry_branches(e):
            nums["unknown_payments"] = round(nums["unknown_payments"] + dr, 2)
            states.append("warn")
            rows.append(row_of(e, dr, f"จ่ายบิล (Dr 2100) แต่ไม่ได้บอกว่าจ่ายบิลไหน · {source_th(e.get('source'))} · {e.get('party') or ''}"))
    known = {str(e["source_id"])[4:] for e in bills}
    # PEA bills booked this month
    for e in bills:
        if not in_bounds(e, start, end) or sum_lines(e, "2100", "credit", branch) == 0:
            continue
        inv = str(e["source_id"])[4:]
        due = sum_lines(e, "2100", "credit", branch)
        paid_list = payers.get(inv, [])
        paid = round(sum(a for _, a in paid_list), 2)
        nums["pea_owed"] = round(nums["pea_owed"] + due, 2)
        nums["pea_paid"] = round(nums["pea_paid"] + paid, 2)
        label = f"ใบแจ้ง PEA {inv} · {e.get('party') or ''}"
        if len(paid_list) > 1:
            states.append("bad")
            rows.append(row_of(e, paid - due, f"{label}: จ่ายซ้ำ {len(paid_list)} ครั้ง รวม {baht(paid)} (บิล {baht(due)})"))
        elif paid > due + 1:
            states.append("bad")
            rows.append(row_of(e, paid - due, f"{label}: จ่ายเกิน {baht(paid - due)} (บิล {baht(due)} จ่าย {baht(paid)})"))
        elif not paid:
            nums["pea_unpaid"] = round(nums["pea_unpaid"] + due, 2)
            states.append("warn" if later_run else "info")
            rows.append(row_of(e, due, f"{label}: ยังไม่มีเช็คจ่าย" + (" (มีรอบเช็คเดือนถัดไปแล้ว — ตรวจว่าจ่ายเป็นค่าไฟซ้ำหรือไม่)" if later_run
                                                                       else " (รอรอบเช็คเดือนหน้า)")))
        elif paid < due - 1:
            nums["pea_unpaid"] = round(nums["pea_unpaid"] + due - paid, 2)
            states.append("warn")
            rows.append(row_of(e, due - paid, f"{label}: จ่ายไม่ครบ ขาด {baht(due - paid)} (บิล {baht(due)} จ่าย {baht(paid)})"))
    # payments this month for a PEA bill that is not in the books
    for inv, lst in payers.items():
        if inv in known:
            continue
        for e, amt in lst:
            if in_bounds(e, start, end) and branch in entry_branches(e):
                states.append("bad")
                rows.append(row_of(e, amt, f"เช็คจ่ายใบแจ้ง PEA {inv} แต่ไม่มีบิลนี้ในบัญชี · {e.get('party') or ''}"))
    # the stored PEA bills of the month must each have their entry, same amount
    by_inv = {str(e["source_id"])[4:]: e for e in bills}
    for b in elec_bills:
        if b.get("branch") and b["branch"] != branch:
            continue
        due = round(money(b.get("total")) - money(b.get("deposit_interest")), 2)
        e = by_inv.get(str(b.get("invoice_no")))
        if not e:
            states.append("bad")
            rows.append({"text": f"ใบแจ้ง PEA {b.get('invoice_no')} เก็บไว้แล้วแต่ไม่ได้ลงบัญชี", "amount": due, "source": "electricity",
                         "source_id": f"pea:{b.get('invoice_no')}", "entry_id": None, "date": None})
        elif abs(sum_lines(e, "2100", "credit") - due) > 1:
            states.append("bad")
            rows.append(row_of(e, sum_lines(e, "2100", "credit") - due,
                               f"ใบแจ้ง PEA {b.get('invoice_no')}: ลงบัญชี {baht(sum_lines(e, '2100', 'credit'))} แต่บิลเขียน {baht(due)}"))
    # purchase / truck bills of the month: their payments are on #payables (not in the books yet)
    month_ids = {(("truck" if e.get("source") == "truck_bill" else "purchase"), str(e.get("source_id"))): e for e in owed
                 if e.get("source") in ("purchase", "truck_bill") and in_bounds(e, start, end) and sum_lines(e, "2100", "credit", branch)}
    if shop_bills is not None:
        for b in shop_bills:
            e = month_ids.get((b["source"], str(b["id"])))
            if not e:
                continue
            due = sum_lines(e, "2100", "credit", branch)
            nums["shop_owed"] = round(nums["shop_owed"] + due, 2)
            if b.get("state") == "due":
                nums["shop_unpaid"] = round(nums["shop_unpaid"] + due, 2)
                states.append("info")
                rows.append(row_of(e, due, f"ยังไม่จ่าย · {b.get('shop') or e.get('party') or ''} · {source_th(e.get('source'))}"))
            elif b.get("changed"):
                states.append("warn")
                rows.append(row_of(e, b["changed"], f"บิลแก้ยอดหลังจ่ายแล้ว ต่าง {b['changed']:+,.2f} · {b.get('shop') or ''}"))
    # everything else owed this month has no payer in the app yet (card fuel: the stations' monthly bills)
    for e in owed:
        if e.get("source") in ("electricity", "purchase", "truck_bill") or not in_bounds(e, start, end):
            continue
        cr = sum_lines(e, "2100", "credit", branch)
        if cr:
            src = e.get("source") or "-"
            nums["no_payer"][src] = round(nums["no_payer"].get(src, 0.0) + cr, 2)
    for src, amt in nums["no_payer"].items():
        states.append("info")
        rows.append({"text": f"{source_th(src)}: ค้างจ่าย {baht(amt)} — ยังไม่มีข้อมูลการจ่ายในแอป", "amount": amt, "source": src,
                     "source_id": None, "entry_id": None, "date": None})
    state = worst("ok", *states)
    parts = []
    if nums["pea_owed"]:
        parts.append(f"ค่าไฟ PEA {baht(nums['pea_owed'])} จ่ายแล้ว {baht(nums['pea_paid'])}")
    if shop_bills is not None and nums["shop_owed"]:
        parts.append(f"บิลร้านค้า {baht(nums['shop_owed'])} ยังไม่จ่าย {baht(nums['shop_unpaid'])}")
    for src, amt in nums["no_payer"].items():
        parts.append(f"{source_th(src)} ค้าง {baht(amt)} (ยังไม่มีผู้จ่ายในแอป)")
    if nums["unknown_payments"]:
        parts.append(f"จ่ายบิลไม่ระบุใบ {baht(nums['unknown_payments'])}")
    nb = sum(1 for s in states if s == "bad")
    summary = (" · ".join(parts) or "ไม่มีบิลค้างจ่ายเดือนนี้") + (f" · ผิดปกติ {nb} รายการ" if nb else "")
    return check("bills", branch, state, summary, nums, rows)


# ------------------------------------------------------------------ 4: each file's own total (pure)
def file_totals_check(files: list[dict], booked: dict, branch: str) -> dict:
    """files = booked inbox_files of the month ({id, file_name, preview, booked_at}); booked = {batch key: (entries now, total debit now)}.
    For each batch of the branch: printed total vs its rows, and what the preview booked vs what is in the books now."""
    rows, states, n_batches = [], [], 0
    claimed: dict = {}
    for f in sorted(files, key=lambda f: str(f.get("booked_at") or "")):
        for b in (f.get("preview") or {}).get("batches") or []:
            claimed[b["key"]] = f.get("id")          # the latest booked file owns a batch key (re-dropping replaces)
    for f in files:
        for b in (f.get("preview") or {}).get("batches") or []:
            if b.get("branch") and b["branch"] != branch:
                continue
            n_batches += 1
            key = b["key"]
            name = f"{f.get('file_name') or ''} · {b.get('title') or key}"
            ft, rt = b.get("file_total"), b.get("rows_total")
            want = round(sum(entry_amount(e) for e in b.get("entries") or []), 2)
            n_now, now = booked.get(key, (0, 0.0))
            base = {"source": b.get("source"), "source_id": key, "entry_id": None, "date": None, "file_id": f.get("id")}
            if claimed.get(key) != f.get("id"):
                states.append("info")
                rows.append({"text": f"{name}: ถูกแทนด้วยไฟล์ที่ลงทีหลัง (ชุดเดียวกัน)", "amount": want, **base})
                continue
            problems = []
            if ft is not None and rt is not None and abs(money(ft) - money(rt)) > 1:
                problems.append(f"ยอดในไฟล์ {baht(money(ft))} แต่แถวรวมได้ {baht(money(rt))}")
            if abs(now - want) > 1:
                problems.append(f"ลงบัญชีไว้ {baht(want)} แต่ตอนนี้ในบัญชีมี {baht(now)} ({n_now} รายการ)")
            if problems:
                states.append("bad")
                rows.append({"text": f"{name}: " + " · ".join(problems), "amount": round(now - want, 2) if abs(now - want) > 1 else round(money(ft) - money(rt), 2), **base})
            else:
                rows.append({"text": f"{name}: ตรงกัน {baht(now)}" + (f" (ยอดในไฟล์ {baht(money(ft))})" if ft is not None else ""), "amount": now, **base})
    nb = sum(1 for s in states if s == "bad")
    if not n_batches:
        return check("file_totals", branch, "info", "ยังไม่มีไฟล์ที่ลงบัญชีจากกล่องรับไฟล์เดือนนี้", {"batches": 0, "bad": 0}, [])
    state = "bad" if nb else "ok"
    summary = f"{n_batches} ชุดข้อมูล · " + (f"ไม่ตรง {nb} ชุด" if nb else "ตรงกันทุกชุด")
    return check("file_totals", branch, state, summary, {"batches": n_batches, "bad": nb}, rows)


# ------------------------------------------------------------------ 5: look-alikes across sources (pure)
_COMPANY_WORDS = re.compile(r"ห้างหุ้นส่วนจำกัด|ห้างหุ้นส่วน|บริษัท|บจก\.?|หจก\.?|จำกัด|มหาชน|สำนักงานใหญ่|สาขาที่\s*\d+|สาขา\s*\d+|ร้าน"
                            r"|\bco\b\.?|\bltd\b\.?|\bcompany\b|\blimited\b|\binc\b\.?|\bpart\b\.?", re.I)
_TONE = re.compile(r"[็-๎]")                       # ็ ่ ้ ๊ ๋ ์ ํ ๎: spelled differently by every clerk
_NOT_WORD = re.compile(r"[^0-9a-zก-ๆ]")


def norm_name(s: Optional[str]) -> str:
    """'บริษัท พีซี เคมีคอล ลูบ จำกัด (สำนักงานใหญ่)' and 'บจก.พีซี เคมีคอล ลู้บ' -> 'พีซีเคมีคอลลูบ'."""
    t = _COMPANY_WORDS.sub(" ", str(s or "").lower())
    return _NOT_WORD.sub("", _TONE.sub("", t))


def similar(a: Optional[str], b: Optional[str]) -> bool:
    na, nb = norm_name(a), norm_name(b)
    if len(na) < 3 or len(nb) < 3:
        return False
    if na == nb:
        return True
    short, long_ = sorted((na, nb), key=len)
    if len(short) >= 4 and short in long_:
        return True
    return SequenceMatcher(None, na, nb).ratio() >= 0.8


_TAG = re.compile(r"#([A-Za-z0-9][A-Za-z0-9\-/.]{3,})")


def norm_doc(s: Optional[str]) -> Optional[str]:
    """A document number as a key: letters and digits only, upper case. It must look like one: letters with at least 3 digits
    ('PC6906-081'), or at least 7 digits (a cheque, a PEA invoice). Not 'หน้า 24' of a petty-cash paper, a truck number '#58', or a
    plate '#83-2919' (the dry run on 2026-07 paired every route's sales with its own card fuel by the plate)."""
    t = re.sub(r"[^0-9A-Z]", "", str(s or "").upper())
    digits = sum(c.isdigit() for c in t)
    if len(t) >= 5 and digits >= 3 and digits < len(t):
        return t
    return t if digits == len(t) and digits >= 7 else None


def doc_tags(entry: dict) -> set:
    """Document numbers an entry carries: its doc_no and the '#PC6906-081' / 'PEA 296011397238' tags in its description."""
    out = set()
    desc = str(entry.get("description") or "")
    for raw in [entry.get("doc_no")] + _TAG.findall(desc) + PEA_REF.findall(desc):
        k = norm_doc(raw)
        if k:
            out.add(k)
    return out


def _accounts_on(entry: dict, side: str) -> set:
    return {ln.get("account") for ln in entry.get("lines") or [] if money(ln.get(side))}


def legit_pair(a: dict, b: dict) -> bool:
    """Two entries that are SUPPOSED to look alike: one opens what the other clears (a bill and the cheque that pays it, the
    petty-cash top-up and the papers, an advance and its deduction), or internal branch ice against the 1600 that settles it."""
    for x, y in ((a, b), (b, a)):
        if (_accounts_on(x, "debit") & _accounts_on(y, "credit")) & set(CLEARING):
            return True
        accs_x = _accounts_on(x, "debit") | _accounts_on(x, "credit")
        accs_y = _accounts_on(y, "debit") | _accounts_on(y, "credit")
        if accs_x & set(INTERNAL) and "1600" in accs_y:
            return True
    return False


def pnl_side(entry: dict, pnl: dict) -> Optional[str]:
    """'cost' when the entry books a cost (Dr an expense / cogs account), 'income' when it books income, None otherwise."""
    for ln in entry.get("lines") or []:
        a = pnl.get(ln.get("account"))
        if a in ("cogs", "expense") and money(ln.get("debit")):
            return "cost"
        if a == "income" and money(ln.get("credit")):
            return "income"
    return None


def _days_apart(a: dict, b: dict) -> int:
    try:
        return abs((date.fromisoformat(str(a["entry_date"])[:10]) - date.fromisoformat(str(b["entry_date"])[:10])).days)
    except (KeyError, ValueError):
        return 10 ** 6


# Sources whose entry date is the real day of the paper / bill. Two of these with the same amount and name but days apart are two
# purchases (a truck filled with 4,000 of cash fuel twice a month is normal), not one counted twice; every other source is dated by
# convention (the 25th, a cheque's estimated "last month's bill" date), so it only has to be near.
DATED_SOURCES = ("midmonth", "endmonth", "purchase", "truck_bill")
PAPERS = ("midmonth", "endmonth")
SAME_DAY = 3            # a bill in the app vs the paper receipt of it: a day or two apart is the same purchase
# mid- vs end-of-month papers: the SAME receipt in both lists has the same date; 3 days apart (24 Jun vs 27 Jun, truck 1, 4,000) is two
# fills -- the dry run on 2026-07 showed dozens of those


def _vehicles(entry: dict) -> set:
    return {ln.get("vehicle_id") for ln in entry.get("lines") or [] if ln.get("vehicle_id")}


def could_be_same(a: dict, b: dict) -> bool:
    """Dates and trucks do not rule out that two entries are one piece of money."""
    va, vb = _vehicles(a), _vehicles(b)
    if va and vb and not va & vb:
        return False
    if a.get("source") in PAPERS and b.get("source") in PAPERS:
        limit = 0
    elif a.get("source") in DATED_SOURCES and b.get("source") in DATED_SOURCES:
        limit = SAME_DAY
    else:
        limit = LOOKALIKE_DAYS
    return _days_apart(a, b) <= limit


def find_lookalikes(entries: list[dict], start: date, end: date, pnl: dict) -> list[dict]:
    """Pairs [{a, b, amount, reason, why}] of entries from two different sources, at least one of them in this month, that look like
    the same money booked twice. pnl = {account code: kind} of the P&L accounts."""
    amt = {e["id"]: entry_amount(e) for e in entries}
    tags = {e["id"]: doc_tags(e) for e in entries}
    side = {e["id"]: pnl_side(e, pnl) for e in entries}
    by_amount: dict = {}
    by_tag: dict = {}
    for e in entries:
        if amt[e["id"]] >= LOOKALIKE_MIN and side[e["id"]]:
            by_amount.setdefault(int(round(amt[e["id"]])), []).append(e)
        for t in tags[e["id"]]:
            by_tag.setdefault(t, []).append(e)
    pairs, seen = [], set()

    def consider(a: dict, b: dict, reason: str, why: str) -> None:
        key = tuple(sorted((a["id"], b["id"])))
        if key in seen or a.get("source") == b.get("source") or not side[a["id"]] or side[a["id"]] != side[b["id"]]:
            return                         # a double that matters books the P&L twice: both a cost, or both income
        if not (in_bounds(a, start, end) or in_bounds(b, start, end)):
            return
        if (_days_apart(a, b) > LOOKALIKE_DAYS) if reason == "doc" else not could_be_same(a, b):
            return
        if legit_pair(a, b):
            return
        seen.add(key)
        first, second = sorted((a, b), key=lambda e: (str(e.get("entry_date")), str(e.get("source"))))
        pairs.append({"a": first, "b": second, "amount": max(amt[a["id"]], amt[b["id"]]), "reason": reason, "why": why})

    for t, lst in by_tag.items():          # the same document number from two sources
        for i, a in enumerate(lst):
            for b in lst[i + 1:]:
                consider(a, b, "doc", f"เลขเอกสาร {t} ซ้ำกัน")
    names: dict = {}

    def alike(a: dict, b: dict) -> bool:
        for e in (a, b):
            if e["id"] not in names:
                names[e["id"]] = (e.get("party"), short_desc(e.get("description")))
        (pa, da), (pb, db) = names[a["id"]], names[b["id"]]
        return similar(pa, pb) or similar(da, db)

    for k, lst in by_amount.items():       # the same amount, similar party / description, both booking P&L on the same side
        cands = lst + by_amount.get(k + 1, [])
        for a in lst:
            for b in cands:
                # the cheap tests first: a bucket of 4,000-baht cash-fuel lines has hundreds of entries
                if (a["id"] == b["id"] or a.get("source") == b.get("source") or side[a["id"]] != side[b["id"]]
                        or abs(amt[a["id"]] - amt[b["id"]]) > 1 or not (in_bounds(a, start, end) or in_bounds(b, start, end))
                        or not could_be_same(a, b)):
                    continue
                if alike(a, b):
                    consider(a, b, "amount", f"ยอดเท่ากัน {baht(amt[a['id']])} ชื่อคล้ายกัน")
    return sorted(pairs, key=lambda p: -p["amount"])


def lookalikes_check(pairs: list[dict], branch: str) -> dict:
    """pairs = find_lookalikes(...) for the month (both branches); a pair belongs to a branch when either entry is that branch's."""
    mine = [p for p in pairs if branch in entry_branches(p["a"]) | entry_branches(p["b"])]
    rows = []
    for n, p in enumerate(mine[:MAX_ROWS // 2], 1):
        for e in (p["a"], p["b"]):
            rows.append(row_of(e, entry_amount(e), f"คู่ที่ {n} ({p['why']}) · {source_th(e.get('source'))} · {dmy(e.get('entry_date'))} · "
                                                    f"{e.get('party') or ''} · {short_desc(e.get('description'))}", pair=n, reason=p["reason"]))
    total = round(sum(p["amount"] for p in mine), 2)
    summary = (f"พบ {len(mine)} คู่ที่อาจนับซ้ำ รวม {baht(total)} — ดูว่าเป็นเงินก้อนเดียวกันหรือไม่" if mine
               else "ไม่พบรายการที่ซ้ำกันระหว่างแหล่งข้อมูล")
    return {"key": "lookalikes", "branch": branch, "title": TITLES["lookalikes"], "state": "warn" if mine else "ok", "summary": summary,
            "numbers": {"pairs": len(mine), "total": total, "by_document": sum(1 for p in mine if p["reason"] == "doc")}, "rows": rows}


# ------------------------------------------------------------------ 6: this month vs the usual (pure)
def pnl_amounts(lines: list[dict], pnl: dict, branch: str) -> dict:
    """{month: {account: amount}} for one branch (expense = debit - credit, income = credit - debit). lines = [{account, debit, credit,
    branch, month}]."""
    out: dict = {}
    for ln in lines:
        kind = pnl.get(ln.get("account"))
        if not kind or ln.get("branch") != branch:
            continue
        a = (money(ln.get("credit")) - money(ln.get("debit"))) if kind == "income" else (money(ln.get("debit")) - money(ln.get("credit")))
        m = out.setdefault(ln["month"], {})
        m[ln["account"]] = round(m.get(ln["account"], 0.0) + a, 2)
    return out


def usual_check(month: str, by_month: dict, branch: str, names: dict) -> dict:
    """by_month = pnl_amounts(...) covering this month and the months before it. names = {code: Thai name}."""
    history = [shift_month(month, -i) for i in range(1, HISTORY_MONTHS + 1)]
    have = [m for m in history if by_month.get(m)]
    this = by_month.get(month, {})
    if not have:
        return check("vs_usual", branch, "info", "ยังไม่มีเดือนก่อนหน้าให้เทียบ", {"months": []}, [])
    codes = set(this) | {c for m in have for c in by_month[m]}
    rows, states, per = [], [], {}
    for c in sorted(codes):
        now = this.get(c, 0.0)
        usual = round(sum(by_month[m].get(c, 0.0) for m in have) / len(have), 2)
        change = round(now - usual, 2)
        pct = (change / usual) if usual > 0 else None
        per[c] = {"this": now, "usual": usual, "change": change, "pct": round(pct * 100, 1) if pct is not None else None}
        label = f"{c} {names.get(c, '')}".strip()
        if usual <= 0 and not any(by_month[m].get(c) for m in have):
            if abs(now) >= JUMP_ABS:
                states.append("info")
                rows.append({"text": f"{label}: {baht(now)} — ไม่มีในเดือนก่อนๆ (ใหม่เดือนนี้)", "amount": now, "source": None, "source_id": c,
                             "account": c, "usual": 0.0})
            continue
        if pct is not None and pct > JUMP_PCT and change > JUMP_ABS:
            states.append("warn")
            rows.append({"text": f"{label}: {baht(now)} ปกติ {baht(usual)} (+{pct * 100:.0f}%)", "amount": change, "source": None, "source_id": c,
                         "account": c, "usual": usual})
        elif pct is not None and pct < -JUMP_PCT and usual > JUMP_ABS:
            states.append("warn")
            rows.append({"text": f"{label}: {baht(now)} ปกติ {baht(usual)} ({pct * 100:.0f}%)" + (" — ยังไม่มีเดือนนี้" if not now else ""),
                         "amount": change, "source": None, "source_id": c, "account": c, "usual": usual})
    nw = states.count("warn")
    state = worst("ok", *states)
    summary = (f"เทียบค่าเฉลี่ย {len(have)} เดือนก่อน ({month_label(have[-1])} – {month_label(have[0])}) · "
               + (f"ต่างจากปกติมาก {nw} หมวด" if nw else "ไม่มีหมวดที่ต่างจากปกติมาก")
               + (f" · หมวดใหม่ {states.count('info')}" if "info" in states else ""))
    return {"key": "vs_usual", "branch": branch, "title": TITLES["vs_usual"], "state": state, "summary": summary,
            "numbers": {"months": have, "accounts": per, "warn": nw}, "rows": top_rows(rows)}


# ------------------------------------------------------------------ all checks from loaded data (pure)
def evaluate(month: str, branches: list[str], data: dict) -> list[dict]:
    """data = what _load() read: accounts, window (entries of this month and the months either side), history (P&L lines with their
    company month), owed (2100 entries, all time), elec_bills, later_run, shop_bills, files, booked."""
    from app.accounting import period_bounds
    start, end = period_bounds(month)
    pnl = {c: a["kind"] for c, a in data["accounts"].items() if a.get("kind") in ("income", "cogs", "expense")}
    names = {c: a.get("name_th") or "" for c, a in data["accounts"].items()}
    month_entries = [e for e in data["window"] if in_bounds(e, start, end)]
    pairs = find_lookalikes(data["window"], start, end, pnl)
    out = {k: [] for k in ORDER}
    for b in branches:
        out["petty_cash"].append(petty_cash(data["window"], b, month))
        out["advances"].append(advances(month_entries, b, start, end))
        out["bills"].append(bills_check(data["owed"], b, start, end, elec_bills=data["elec_bills"], later_run=data["later_run"],
                                        shop_bills=data.get("shop_bills")))
        out["file_totals"].append(file_totals_check(data["files"], data["booked"], b))
        out["lookalikes"].append(lookalikes_check(pairs, b))
        out["vs_usual"].append(usual_check(month, pnl_amounts(data["history"], pnl, b), b, names))
    return [c for k in ORDER for c in out[k]]


# ------------------------------------------------------------------ database (read only)
_ENTRY_COLS = "id,entry_date,branch,source,source_id,party,description,doc_no"


def _group(lines: list[dict]) -> list[dict]:
    """acc_lines rows with their entry embedded (entry:acc_entries!inner(...)) -> entries with their lines."""
    out: dict = {}
    for ln in lines:
        e = ln["entry"]
        x = out.setdefault(e["id"], {**e, "lines": []})
        x["lines"].append({k: ln.get(k) for k in ("account", "debit", "credit", "branch", "vehicle_id")})
    return list(out.values())


def _lines_between(start: date, end: date, accounts: Optional[list[str]] = None) -> list[dict]:
    params = {"select": f"id,account,debit,credit,branch,vehicle_id,entry:acc_entries!inner({_ENTRY_COLS})",
              "entry.and": f"(entry_date.gte.{start.isoformat()},entry_date.lte.{end.isoformat()})", "order": "id.asc"}
    if accounts:
        params["account"] = f"in.({','.join(accounts)})"
    return supabase.select_all_parallel("acc_lines", params, 1000, 6)


def _company_month(iso: str) -> str:
    from app.accounting import period_of
    return period_of(date.fromisoformat(iso[:10]))["month"]


def _shop_bills() -> Optional[list[dict]]:
    """#payables' view of every purchase / truck bill (paid / paid on the day / due, changed after payment); None if the payables
    tables are not there."""
    from app import payables
    from app.service_plan import today_th
    try:
        v = payables.view(payables._bills(), *payables._state(), today_th())
    except supabase.SupabaseError:
        return None
    return [b for s in v["shops"] for b in s["bills"]]


def _booked_now(keys: list[str]) -> dict:
    """{batch key: (entries, total debit)} as the books are NOW."""
    out = {}
    for key in keys:
        q = key.replace('"', "")
        rows = supabase.select_all("acc_entries", {"select": "id,lines:acc_lines(debit)", "or": f'(source_id.eq."{q}",source_id.like."{q}:*")',
                                                   "order": "id.asc"})
        out[key] = (len(rows), round(sum(money(ln.get("debit")) for r in rows for ln in r.get("lines") or []), 2))
    return out


def _files(month: str) -> list[dict]:
    try:
        return supabase.select_all("inbox_files", {"select": "id,file_name,status,month,preview,batches,booked_at", "month": f"eq.{month}",
                                                   "status": "eq.booked", "order": "uploaded_at.asc"})
    except supabase.SupabaseError:          # schema_v92 not applied yet: no drop-box files
        return []


def _load(month: str) -> dict:
    """Everything the checks need, read at the same time (about 3-5 s on the live database, most of it #payables' bill list).
    Its own small thread pool: the reads page with supabase.select_all_parallel, which uses the shared pool -- waiting on that pool
    from inside it could starve it when two people open the checks at once."""
    from concurrent.futures import ThreadPoolExecutor
    from app.accounting import _accounts, period_bounds
    accounts = _accounts()
    pnl_codes = sorted(c for c, a in accounts.items() if a.get("kind") in ("income", "cogs", "expense"))
    w_start, w_end = period_bounds(shift_month(month, -1))[0], period_bounds(shift_month(month, 1))[1]
    h_start, h_end = period_bounds(shift_month(month, -HISTORY_MONTHS))[0], period_bounds(month)[1]
    with ThreadPoolExecutor(max_workers=7, thread_name_prefix="checks") as ex:
        f_window = ex.submit(lambda: _group(_lines_between(w_start, w_end)))
        f_history = ex.submit(_lines_between, h_start, h_end, pnl_codes)
        f_owed = ex.submit(lambda: _group(supabase.select_all("acc_lines", {"select": f"id,account,debit,credit,branch,vehicle_id,entry:acc_entries!inner({_ENTRY_COLS})",
                                                                            "account": "eq.2100", "order": "id.asc"})))
        f_elec = ex.submit(lambda: supabase.select_all("elec_bills", {"select": "invoice_no,bill_period,branch,total,deposit_interest",
                                                                      "bill_period": f"eq.{month}", "order": "invoice_no.asc"}))
        # a cheque run of a later month is in (batch keys cheque-YYYY-MM-<branch>): an unpaid PEA bill of this month should be paid by now
        f_later = ex.submit(lambda: supabase.select("acc_entries", {"select": "id", "source": "eq.hq_cheques", "source_id": f"gte.cheque-{shift_month(month, 1)}",
                                                                    "limit": "1"}))
        f_shop = ex.submit(_shop_bills)
        f_files = ex.submit(_files, month)
        files = f_files.result()
        keys = sorted({b["key"] for f in files for b in (f.get("preview") or {}).get("batches") or []})
        f_booked = ex.submit(_booked_now, keys)
        history = [{"account": ln["account"], "debit": ln["debit"], "credit": ln["credit"], "branch": ln.get("branch") or ln["entry"].get("branch"),
                    "month": _company_month(ln["entry"]["entry_date"])} for ln in f_history.result()]
        return {"accounts": accounts, "window": f_window.result(), "history": history, "owed": f_owed.result(), "elec_bills": f_elec.result(),
                "later_run": bool(f_later.result()), "shop_bills": f_shop.result(), "files": files, "booked": f_booked.result()}


def run(month: str, branch: str = "") -> dict:
    """Every check for a company month, one branch or both. Read-only."""
    from app.accounting import InvalidEntry, _require
    if not re.fullmatch(r"\d{4}-\d{2}", month or "") or not 1 <= int(month[5:7]) <= 12:
        raise InvalidEntry("เดือนไม่ถูกต้อง")
    if branch and branch not in BRANCHES:
        raise InvalidEntry("เลือกสาขา")
    _require()
    branches = [branch] if branch else list(BRANCHES)
    return {"month": month, "branch": branch, "checks": evaluate(month, branches, _load(month))}
