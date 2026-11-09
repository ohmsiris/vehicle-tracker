"""Saraburi PEA bills Jan-Apr 2026 (both meters), typed from the owner's photos (9 Nov 2026) -> elec_bills + the books.

    python scripts/add_srb_elec_jan_apr.py [--write]

They were in the books only through HQ's cheques (Dr 5100). With the bills stored, re-importing the cheque runs 02-05/2569 makes those
cheques pay the bills (Dr 2100) instead, so nothing counts twice -- and the electricity page gets kWh / demand / Ft for those months.
"""
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import acc_cheques, electricity  # noqa: E402

A = {"ca_no": "020019229615", "meter_no": "23056197", "address": "72", "multiplier": 1200}
B = {"ca_no": "020019227916", "meter_no": "23062206", "address": "72/9", "multiplier": 800}
COLS = ("invoice_no", "bill_period", "read_date", "due_date", "demand_peak_kw", "demand_offpeak_kw", "demand_holiday_kw", "kwh_peak", "kwh_offpeak",
        "kwh_holiday", "kvar_max", "demand_charge", "energy_peak", "energy_offpeak", "pf_charge", "base_amount", "ft_amount", "subtotal", "vat", "total")
BILLS = [
    (A, "312510944555", "2026-01", "2026-01-31", "2026-02-20", 343.20, 484.80, 481.20, 29688, 81840, 69396, 248.40, 45621.58, 124211.62, 393773.17, 0, 563918.61, 17585.81, 581504.42, 40705.31, 622209.73),
    (B, "864010536831", "2026-01", "2026-01-31", "2026-02-23", 264.80, 428.00, 425.60, 22448, 77648, 68464, 278.40, 35199.86, 93920.18, 380431.81, 728.91, 509864.09, 16384.03, 526977.03, 36888.39, 563865.42),
    (A, "862410658262", "2026-02", "2026-02-28", "2026-03-20", 339.60, 343.20, 343.20, 46476, 62808, 56832, 157.20, 45143.03, 194450.94, 311506.67, 0, 551412.88, 16146.48, 567559.36, 39729.16, 607288.52),
    (B, "312510999872", "2026-02", "2026-02-28", "2026-03-20", 315.20, 436.00, 427.20, 41632, 82040, 71696, 293.60, 41899.54, 174184.13, 400282.42, 1289.61, 616678.33, 18989.77, 636957.71, 44587.04, 681544.75),
    (A, "312511054871", "2026-03", "2026-03-31", "2026-04-20", 344.40, 483.60, 480.00, 59208, 70500, 77676, 240.00, 45781.09, 247720.35, 385805.85, 0, 679619.53, 20157.72, 699777.25, 48984.41, 748761.66),
    (B, "312511054868", "2026-03", "2026-03-31", "2026-04-20", 328.00, 437.60, 428.80, 48056, 86776, 88264, 292.00, 43601.04, 201061.50, 455751.65, 1177.47, 700726.43, 21684.93, 723588.83, 50651.22, 774240.05),
    (A, "860610800857", "2026-04", "2026-04-30", "2026-05-20", 481.20, 493.20, 492.00, 59748, 81276, 122520, 246.00, 63965.92, 249979.65, 530623.65, 0, 844881.46, 25616.48, 870497.94, 60934.86, 931432.80),
    (B, "860610800855", "2026-04", "2026-04-30", "2026-05-20", 425.60, 432.00, 438.40, 59688, 76288, 102312, 287.20, 56575.01, 249728.62, 465020.82, 897.12, 771636.69, 23161.59, 795695.40, 55698.68, 851394.08),
]


def main() -> None:
    bills = []
    for meter, *vals in BILLS:
        b = dict(meter) | dict(zip(COLS, vals)) | {"tariff": "4224", "service_charge": 312.24, "ft_rate": 0.0972, "discount": 0, "estimated": False,
                                                   "deposit_interest": 0}
        b["kwh_total"] = b["kwh_peak"] + b["kwh_offpeak"] + b["kwh_holiday"]
        assert abs(b["energy_peak"] + b["energy_offpeak"] + b["demand_charge"] + 312.24 - b["base_amount"]) < 1, b["invoice_no"]
        assert abs(b["base_amount"] + b["ft_amount"] + b["pf_charge"] - b["subtotal"]) < 1 and abs(b["subtotal"] + b["vat"] - b["total"]) < 0.02, b["invoice_no"]
        bills.append(b)
        print(b["bill_period"], b["address"], f"{b['kwh_total']:,} kWh", f"{b['total']:,.2f}")
    if "--write" not in sys.argv:
        return
    for b in bills:
        electricity.save_bill({k: v for k, v in b.items() if k != "due_date"}, "สระบุรี")
    runs = {"2026-02", "2026-03", "2026-04", "2026-05"}
    for p in sorted(glob.glob(r"E:/Saraburi/ClaudeCode/CP Accounting/จ่ายเช็ค/*.xls*")):
        sheets = acc_cheques.read_xls(p)
        if any(str(acc_cheques.run_month(v))[:7] in runs for v in sheets.values()):
            out = acc_cheques.import_file(p, None, "สระบุรี", write=True)
            paid = [e for e in out["entries"] if e["kind"] == "pays_bill"]
            print(f"cheques {out['month']:%Y-%m}: {len(paid)} now pay a PEA bill")


if __name__ == "__main__":
    main()
