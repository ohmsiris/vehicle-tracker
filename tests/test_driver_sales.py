from app import driver_sales as ds


def _row(code, sales, bag, days, month="2026-09"):
    return {"month": month, "code": code, "sales": sales, "bag_pay": bag, "days": days}


def test_gross_matches_the_commission_rules_examples():
    # commission-rules.md, September 2569 (31-day period)
    assert ds.period_days("2026-09") == 31 and ds.period_days("2026-10") == 30 and ds.period_days("2026-01") == 31
    assert ds.gross(_row("SB-PD001", 19070, 2769, 30), 31740)["gross"] == 180030        # ทวาย: 923 net bags, no bonus
    assert ds.gross(_row("SB-PD002", 73140, 2491, 31), 12500)["gross"] == 155220        # สำเริง: 1,000 bonus + 497 bags x 3
    assert ds.gross(_row("SB-PD006", 135810, 1310, 31), 31610)["gross"] == 176720       # สะอาด: 1,000 bonus + 31 bags x 10
    assert ds.gross(_row("SB-PD007", 99020, 1000, 31), 9999)["gross"] == 99020          # small truck: written sales are gross
    assert ds.gross(_row("SB-PD003", 142014, 0, 28), None)["gross"] is None              # big truck, fuel unknown


def test_sheet_blocks():
    rows = [(None,) * 7 + ("SEP",) + (None,) * 7 + ("OCT",),
            ("No.",) * 23,
            (1.0, "SB-PD007", "นาวิน บุญพยุง", "ต้น", 45.0, "S", "x", 31.0, 10850.0, 350, 99020.0, 1980.0, 0.02, 1000.0, 13830.0)]
    out = ds.parse(rows, 2026)
    assert out == [{"month": "2026-09", "code": "SB-PD007", "name": "นาวิน บุญพยุง", "nick": "ต้น", "truck": "45", "days": 31.0, "wage": 10850.0,
                    "rate": 350.0, "sales": 99020.0, "commission": 1980.0, "bag_pay": 1000.0, "total_pay": 13830.0}]


def test_payroll_pdf_rows_with_and_without_bag_cell():
    text = "\n".join(["SB-PD001 นายทวาย  กำจันทร์", "ขับรถ ซอง (ใหญ่)", "29", "10,672", "17,040", "341", "2,688", "13,701", "4,339",
                      "SB-PD008 นายวินทัย  พรหมประดิษฐ์", "ขับรถหลอด (ใหญ่)", "26", "3,120", "102,930", "5,147", "8,267", "3,501",
                      "SB-PD034 แผน อักษรกิ่ง", "x", "30", "12,900", "1,000", "13,900", "3,000", "10,900",
                      "SB-PD049 สุทธิสาร  หอยสังข์", "x", "12,000", "12,000", "4,000", "8,000", "1,000"])
    out = {r["code"]: (r["days"], r["wage"], r["sales"], r["commission"], r["bag_pay"], r["total_pay"]) for r in ds.parse_payroll_text(text, "2026-10")}
    assert out["SB-PD001"] == (29, 10672, 17040, 341, 2688, 13701)
    assert out["SB-PD008"] == (26, 3120, 102930, 5147, 0, 8267)
    assert out["SB-PD034"] == (30, 12900, 0, 0, 1000, 13900)
    assert out["SB-PD049"] == (0, 12000, 0, 0, 0, 12000)
