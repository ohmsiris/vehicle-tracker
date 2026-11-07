from app.routes_report import month_range, summarize
from datetime import date


def test_month_range():
    assert month_range("2026-01") == (date(2025, 12, 26), date(2026, 1, 25))


def test_summarize_profit_status_and_shared_truck():
    rows = [
        {"month": "2026-07", "vehicle_id": "v1", "name": "ก ข", "gross_sales": 100000, "sales_written": 90000, "fuel_card": 8000, "fuel_cash": 2000,
         "total_pay": 12000, "days": 30, "bags": 0, "fuel_complete": True},
        {"month": "2026-07", "vehicle_id": "v1", "name": "ค ง", "gross_sales": 20000, "sales_written": 20000, "fuel_card": 1000, "fuel_cash": None,
         "total_pay": 3000, "days": 5, "bags": 0, "fuel_complete": True},
        {"month": "2026-02", "vehicle_id": "v1", "name": "ก ข", "gross_sales": 50000, "sales_written": 50000, "fuel_card": 0, "fuel_cash": None,
         "total_pay": 9000, "days": 28, "bags": 0, "fuel_complete": False},
    ]
    out = summarize(rows, {("2026-07", "v1"): 5000}, {("2026-07", "v1"): 3000}, {"v1": {"vehicle_number": "38", "plate_number": "84-7169"}}, {"v1": "สำรวย อสุรินทร์"})
    assert [m["status"] for m in out["months"]] == ["short", "ok"]
    x = out["trucks"][0]["months"]["2026-07"]
    assert (x["sales"], x["fuel"], x["pay"], x["repairs"], x["profit"]) == (120000, 11000, 15000, 5000, 89000)
    assert x["drivers"] == ["ก ข", "ค ง"] and x["fuel_per_km"] == round(11000 / 3000, 2) and out["trucks"][0]["driver_now"] == "สำรวย อสุรินทร์"
