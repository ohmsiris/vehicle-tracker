from app import unit_cost as uc

P = {"meters": {"A": {"name": "A", "background_kw": 10.0, "kwh_per_bag": 2.0, "block": False},
                "B": {"name": "B", "background_kw": 10.0, "kwh_per_bag": 2.0, "block": True}},
     "block_kw": 100.0, "block_run": 1.0, "cans": 100, "harvest_share": 0.5, "block_kg": 200.0, "bag_kg": 25.0}


def _days(month_days, kw_a, kw_b):
    from datetime import timedelta
    start, _ = uc.month_bounds("2026-02")
    out = []
    for i in range(month_days):
        d = (start + timedelta(days=i)).isoformat()
        out += [{"ca": "A", "day": d, "s": list(range(96)), "k": [kw_a] * 96},
                {"ca": "B", "day": d, "s": list(range(96)), "k": [kw_b] * 96}]
    return out


def test_production_splits_meters_into_tube_bags_and_blocks():
    n = (uc.month_bounds("2026-02")[1] - uc.month_bounds("2026-02")[0]).days + 1      # 26 Jan - 25 Feb = 31 days
    pr = uc.production(_days(n, 110.0, 210.0), "2026-02", P)
    h = n * 24
    assert pr["coverage"] == 1.0
    assert pr["block_kwh"] == 100 * h and pr["background_kwh"] == 20 * h
    assert pr["tube_kwh"] == (100 + 100) * h                      # A: 110 - 10 ; B: 210 - 10 - 100
    assert pr["bags"] == 200 * h / 2.0 and pr["blocks"] == 50 * n


def test_compute_sends_salt_to_block_bags_to_tube_and_shares_the_rest_by_weight():
    prod = {"bags": 800.0, "blocks": 100.0, "block_kwh": 1000.0, "tube_kwh": 1000.0, "background_kwh": 0.0, "metered_kwh": 2000.0}
    # tube 800 x 25 = 20,000 kg ; block 100 x 200 = 20,000 kg -> half each by weight
    out = uc.compute("2026-02", prod, {"5100": 4000.0, "5300": 500.0, "5330": 800.0, "5400": 2000.0, "6120": 1000.0, "5060": 9999.0},
                     {}, {"tube": (10, 300.0)}, P)
    assert out["block_share_kg"] == 0.5
    assert out["tube"]["total"] == {"elec": 2000, "labour": 1000, "pack": 800, "supply": 0, "repair": 0, "admin": 0, "delivery": 500}
    assert out["block"]["total"]["pack"] == 500 and out["block"]["total"]["elec"] == 2000
    assert out["tube"]["plant"] == round((2000 + 1000 + 800) / 800, 2) and out["tube"]["delivered"] == round(3800 / 800 + 500 / 800, 2)
    assert out["tube"]["price"] == 30.0 and out["block"]["price"] is None
