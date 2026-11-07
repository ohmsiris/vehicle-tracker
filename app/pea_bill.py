"""
PEA "Smart Invoice" electricity bills (ใบแจ้งค่าไฟฟ้า, the PDF from PEA e-Service): read exactly from the PDF's text layer -- no AI.

The Thai words come out garbled (the PDF's font has no Unicode map), but the English labels and every number are exact and always in
the same order, so the bill is read by position:
  header   #<invoice> ... CA no., invoice no., total, due date ... PEA code, MRU, meter no., tariff, read date, period, voltage, multiplier
  charges  demand Peak kW (rate, amount) / Off Peak kW (rate 0) / energy Peak kWh / Off Peak kWh / Service Charge / Total Based Amount
  usage    demand kW P / OP / H, energy kWh P / OP / H (recent, previous, units), total kWh, max kVAR
  totals   Based Amount, Ft (rate in the label), Discount, Sub Total, VAT, Total
Large-business time-of-use tariff (4.2.2 / 4224 ...): demand is charged on the peak-period kW only; holiday kWh is billed at the
off-peak rate. parse_text is pure and unit-tested; a bill whose parts do not add up to its total is refused (not a PEA bill / new layout).
"""
import re
from datetime import date
from typing import Optional

NUM = re.compile(r"^-?[\d,]+(?:\.\d+)?$")


class NotPeaBill(ValueError):
    pass


def _f(s: str) -> float:
    return float(s.replace(",", ""))


def _after(lines: list[str], label: str, n: int = 1, numeric: bool = True) -> list[str]:
    """The n (numeric) lines after the first line containing `label`."""
    for i, ln in enumerate(lines):
        if label in ln:
            out = [x for x in lines[i + 1:] if (NUM.match(x) if numeric else x)]
            if len(out) >= n:
                return out[:n]
    raise NotPeaBill(f"ไม่พบ {label}")


def _after_opt(lines: list[str], label: str) -> list[str]:
    try:
        return _after(lines, label)
    except NotPeaBill:
        return []


def _be(d: str) -> Optional[date]:
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})$", d)
    if not m:
        return None
    y = int(m.group(3))
    return date(y - 543 if y > 2400 else y, int(m.group(2)), int(m.group(1)))


def _usage(lines: list[str], label: str) -> dict:
    """The usage table: 3 kW rows and 3 kWh rows (each starting with a P / OP / H line: recent reading, previous reading, units), the
    total kWh, then max kVAR (recent, previous, units). A month PEA could not read shows "เฉลี่ย -" instead of the two readings (an
    estimated bill), so each row's units are its LAST number, not a fixed position."""
    try:
        i = next(k for k, ln in enumerate(lines) if label in ln)
    except StopIteration:
        raise NotPeaBill(f"ไม่พบ {label}")
    groups, cur, estimated = [], None, False
    for ln in lines[i + 1:]:
        if "Amount (Baht)" in ln or "(Based Amount)" in ln:
            break
        if "เฉลี่ย" in ln:
            estimated = True
        if re.search(r"(^|\s)(P|OP|H)$", ln):
            cur = []
            groups.append(cur)
        elif NUM.match(ln) and cur is not None:
            cur.append(_f(ln))
    if len(groups) < 6 or any(not g for g in groups[:6]):
        raise NotPeaBill("ไม่พบตารางหน่วยไฟ")
    tail = groups[5]                  # the last kWh row runs on into: its units, the total kWh, kVAR recent / previous / units
    six = [g[-1] for g in groups[:5]]
    k = 2 if len(tail) >= 7 else 0    # an estimated H row has only its units
    if len(tail) < k + 3:
        raise NotPeaBill("ไม่พบตารางหน่วยไฟ")
    h_units, total, kvar = tail[k], tail[k + 1], tail[-1]
    six.append(h_units)
    return {"kw": six[:3], "kwh": six[3:], "total": total, "kvar": kvar, "estimated": estimated}


def parse_text(text: str) -> dict:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines or "Smart Invoice" not in text or "Total Based Amount" not in text:
        raise NotPeaBill("ไม่ใช่ใบแจ้งค่าไฟฟ้า PEA")
    out: dict = {}
    m = re.match(r"#(\d+)", lines[0])
    head = [x for x in lines[lines.index("Due Date") + 1:] if x][:4] if "Due Date" in lines else []
    if len(head) < 3:
        raise NotPeaBill("ไม่พบเลขที่ผู้ใช้ไฟ")
    out["ca_no"], out["invoice_no"], out["total"] = head[0], head[1], _f(head[2])
    # the header shows the grand total; when PEA pays back interest on the deposit (ผลประโยชน์เงินประกัน) that is less than the
    # month's bill -- keep the bill's own total, and the interest apart (other income)
    month_total = next((x for x in _after_opt(lines, "(Total)")), None)
    out["deposit_interest"] = round(_f(month_total) - out["total"], 2) if month_total and _f(month_total) > out["total"] else 0.0
    if out["deposit_interest"]:
        out["total"] = _f(month_total)
    if m and m.group(1) != out["invoice_no"]:
        raise NotPeaBill("เลขที่ใบแจ้งไม่ตรงกัน")
    i = lines.index("Multi")
    meta = lines[i + 1:i + 9]
    out["meter_no"], out["tariff"] = meta[2], meta[3]
    out["read_date"] = _be(meta[4])
    pm = re.match(r"(\d{1,2})/(\d{4})$", meta[5])
    if not pm:
        raise NotPeaBill("ไม่พบประจำเดือน")
    y = int(pm.group(2))
    out["bill_period"] = f"{y - 543 if y > 2400 else y}-{int(pm.group(1)):02d}"
    mult = re.search(r"KV\s+(\d+)", meta[6]) or (re.match(r"(\d+)$", meta[7]) if len(meta) > 7 else None)
    out["multiplier"] = _f(mult.group(1)) if mult else None
    addr = next((ln for ln in lines if "18000" in ln or re.search(r"\s\d{5}\s*$", ln)), "")
    am = re.search(r"\s(\d+(?:/\d+)?)\s", addr)
    out["address"] = am.group(1) if am else None

    # charges: the first Peak / Off Peak pair is the demand (kW), the second the energy (kWh)
    charge = []
    for k, ln in enumerate(lines):
        cm = re.match(r"(Peak|Off Peak)\s+([\d,\.]+)\s", ln + " ")
        if cm:
            rate, amount = [x for x in lines[k + 1:k + 4] if NUM.match(x)][:2]
            charge.append((cm.group(1), _f(cm.group(2)), _f(rate), _f(amount)))
    if len(charge) < 4:
        raise NotPeaBill("ไม่พบรายละเอียดค่าไฟ")
    (_, out["demand_peak_kw"], out["demand_rate"], out["demand_charge"]), _, \
        (_, _, out["rate_peak"], out["energy_peak"]), (_, _, out["rate_offpeak"], out["energy_offpeak"]) = charge[:4]
    out["demand_charge"] += charge[1][3]                                  # off-peak demand is normally 0 baht
    out["service_charge"] = _f(_after(lines, "(Service Charge)")[0])
    base_total = _f(_after(lines, "(Total Based Amount)")[0])

    # usage: demand P / OP / H, energy P / OP / H (recent, previous, units each), total kWh, max kVAR (recent, previous, units)
    # the label is "Comsumption Unit" on the newer bills, "Consumption Unit" on the older ones -- where the usage-history box above
    # uses the same words, so look only after "Recent Reading" (the usage table's own header)
    start = next((i for i, ln in enumerate(lines) if "Recent Reading" in ln), 0)
    label = next((ln for ln in lines[start:] if re.search(r"Co[mn]sumption Unit", ln)), "Comsumption Unit")
    usage = _usage(lines[start:], label)
    out["demand_peak_kw"], out["demand_offpeak_kw"], out["demand_holiday_kw"] = usage["kw"]
    out["kwh_peak"], out["kwh_offpeak"], out["kwh_holiday"] = usage["kwh"]
    out["kwh_total"], out["kvar_max"], out["estimated"] = usage["total"], usage["kvar"], usage["estimated"]

    out["base_amount"] = _f(_after(lines, "(Based Amount)")[0])
    ft_line = next((ln for ln in lines if "Ft" in ln and "=" in ln), "")
    fm = re.search(r"=\s*(-?[\d\.]+)", ft_line)
    out["ft_rate"] = _f(fm.group(1)) if fm else None
    out["ft_amount"] = _f(_after(lines, ft_line)[0]) if ft_line else 0.0
    out["subtotal"] = _f(_after(lines, "(Sub Total)")[0])
    out["vat"] = _f(_after(lines, "(VAT)")[0])
    # ค่าเพาเวอร์แฟคเตอร์ (the power-factor charge: kVAR above 61.97% of kW) sits between the Ft amount and "Discount" when there is one
    out["pf_charge"] = 0.0
    if ft_line:
        k = lines.index(ft_line)
        stop = next((j for j in range(k + 1, len(lines)) if "(Discount)" in lines[j] or "(Sub Total)" in lines[j]), len(lines))
        extra = [_f(x) for x in lines[k + 2:stop] if NUM.match(x)]
        out["pf_charge"] = round(sum(extra), 2)
    out["discount"] = round(out["base_amount"] + out["ft_amount"] + out["pf_charge"] - out["subtotal"], 2)
    if abs(base_total - out["demand_charge"] - out["energy_peak"] - out["energy_offpeak"] - out["service_charge"]) > 1.0:
        raise NotPeaBill("ตัวเลขในใบแจ้งไม่ลงตัว — รูปแบบใบแจ้งอาจเปลี่ยน")

    # it must add up, or it is not read right
    checks = [(out["kwh_peak"] + out["kwh_offpeak"] + out["kwh_holiday"], out["kwh_total"], 1.0),
              (base_total, out["base_amount"], 0.05),
              (out["subtotal"] + out["vat"], out["total"], 0.05),
              (out["rate_peak"] * out["kwh_peak"], out["energy_peak"], 1.0),
              (out["rate_offpeak"] * (out["kwh_offpeak"] + out["kwh_holiday"]), out["energy_offpeak"], 1.0)]
    for got, want, tol in checks:
        if abs(got - want) > tol:
            raise NotPeaBill("ตัวเลขในใบแจ้งไม่ลงตัว — รูปแบบใบแจ้งอาจเปลี่ยน")
    return out


def parse_pdf(data: bytes) -> dict:
    import pymupdf
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception:
        raise NotPeaBill("เปิดไฟล์ PDF ไม่ได้")
    return parse_text("\n".join(p.get_text() for p in doc))
