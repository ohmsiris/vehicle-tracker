from app.accounting import year_rows

ACC = {"4100": {"code": "4100", "name_en": "Ice sales", "name_th": "ขาย", "kind": "income", "section": "revenue"},
       "4150": {"code": "4150", "name_en": "To branch", "name_th": "ให้สาขา", "kind": "income", "section": "revenue"},
       "5100": {"code": "5100", "name_en": "Power", "name_th": "ไฟ", "kind": "cogs", "section": "cogs"},
       "6120": {"code": "6120", "name_en": "Fuel", "name_th": "น้ำมัน", "kind": "expense", "section": "selling"},
       "4900": {"code": "4900", "name_en": "Other", "name_th": "อื่น", "kind": "income", "section": "other"},
       "1100": {"code": "1100", "name_en": "Cash", "name_th": "เงินสด", "kind": "asset", "section": "assets"}}


def _l(acc, d, c, day):
    return {"account": acc, "debit": d, "credit": c, "date": day}


def test_year_rows_company_months_and_internal_ice():
    lines = [_l("4100", 0, 1000, "2026-07-25"), _l("4100", 0, 500, "2026-07-26"),      # the 26th is next company month
             _l("5100", 400, 0, "2026-07-10"), _l("6120", 100, 0, "2026-07-20"), _l("4900", 0, 10, "2026-07-20"),
             _l("4150", 0, 300, "2026-07-25"), _l("1100", 999, 0, "2026-07-25")]
    both = year_rows(lines, ACC, ["2026-07", "2026-08"])
    jul = both["months"][0]
    assert (jul["revenue"], jul["cogs"], jul["gross_profit"], jul["selling"], jul["other"], jul["net_profit"]) == (1000, 400, 600, 100, 10, 510)
    assert both["months"][1]["revenue"] == 500
    one = year_rows(lines, ACC, ["2026-07"], branch="แก่งคอย")
    assert one["months"][0]["revenue"] == 1300                       # one branch counts ice it sold the other branch
    assert both["accounts"][0]["code"] == "4100"
