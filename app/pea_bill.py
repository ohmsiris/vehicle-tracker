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


def _be(d: str) -> Optional[date]:
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})$", d)
    if not m:
        return None
    y = int(m.group(3))
    return date(y - 543 if y > 2400 else y, int(m.group(2)), int(m.group(1)))


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
    u = _after(lines, "Comsumption Unit", 22)
    vals = [_f(x) for x in u]
    out["demand_peak_kw"], out["demand_offpeak_kw"], out["demand_holiday_kw"] = vals[2], vals[5], vals[8]
    out["kwh_peak"], out["kwh_offpeak"], out["kwh_holiday"] = vals[11], vals[14], vals[17]
    out["kwh_total"], out["kvar_max"] = vals[18], vals[21]

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
