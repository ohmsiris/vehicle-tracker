from app import amr

HTML = """<td colspan='5' class='header'>[ประจำเดือน : มกราคม 2569]</td></tr>
<tr><td class='detail'>บัญชีผู้ใช้ไฟ : </td><td>020019229615&nbsp;</td><td class='detail'>ชื่อผู้ใช้ไฟ : </td><td>x</td><td></td></tr>
<tr><td class='detail'>หมายเลขมิเตอร์ : </td><td>23056197&nbsp;</td></tr> รายงานข้อมูลกิโลวัตต์ชั่วโมงรายเดือน
<tr><td class='leftcenter'>&nbsp;01/01/2026 00.15</td><td class='rightdetail'></td><td class='rightdetail'></td><td class='rightdetail'>106.43</td><td class='rightdetail'>106.43</td></tr>
<tr><td class='leftcenter'>&nbsp;02/01/2026 24.00</td><td class='rightdetail'></td><td class='rightdetail'>50.5</td><td class='rightdetail'></td><td class='rightdetail'>50.5</td></tr>
<tr><td class='leftcenter'>&nbsp;05/01/2026 09.15</td><td class='rightdetail'>80</td><td class='rightdetail'></td><td class='rightdetail'></td><td class='rightdetail'>80</td></tr>
"""


def test_parse_slots_are_start_of_15_minutes_and_rate_column():
    r = amr.parse(HTML)
    assert (r["month"], r["ca_no"], r["meter_no"]) == ("2026-01", "020019229615", "23056197")
    assert r["rows"] == [{"slot_start": "2026-01-01 00:00:00+07", "tou": "C", "kwh": 106.43},
                         {"slot_start": "2026-01-02 23:45:00+07", "tou": "B", "kwh": 50.5},
                         {"slot_start": "2026-01-05 09:00:00+07", "tou": "A", "kwh": 80.0}]


def test_empty_report_has_no_month():
    assert amr.parse("รายงานข้อมูลกิโลวัตต์ชั่วโมงรายเดือน [ประจำเดือน : ]")["month"] is None


def _day(ca, day, kw, tou):
    return {"ca": ca, "day": day, "s": list(range(96)), "k": kw, "t": tou}


WORK_TOU = "B" * 36 + "A" * 52 + "B" * 8          # 00:00-09:00 off-peak, 09:00-22:00 peak, 22:00-24:00 off-peak


def _work(early=False):
    kw = [500.0] * 32 + [300.0] * 4 + [100.0] * 52 + [500.0] * 8     # machines on at night, off 09:00-22:00
    if early:
        kw[80:88] = [500.0] * 8                                       # started at 20:00, two hours early
    return kw


def test_analyse_finds_daytime_level_early_start_and_demand_spike():
    days = [_day("X", "2026-01-05", _work(), WORK_TOU), _day("X", "2026-01-06", _work(early=True), WORK_TOU),
            _day("X", "2026-01-07", _work(), WORK_TOU), _day("X", "2026-01-10", [400.0] * 96, "C" * 96)]
    out = amr.analyse(days, {"X": "72"})
    m = out["by_month"]["2026-01"]
    assert m["work_days"] == 3 and m["day_kw"] == 100
    assert m["early"] == {"days": 1, "kwh": 800, "baht": round(800 * (amr.RATE_PEAK - amr.RATE_OFF))}
    assert m["late"]["days"] == 0
    assert out["events"] == [{"day": "2026-01-06", "kind": "early", "at": "20:00", "kwh": 800, "baht": 1264}]
    assert m["spikes"] == [{"ca_no": "X", "kw": 500.0, "at": "2026-01-06 20:00", "normal_kw": 100.0, "baht": round(400 * amr.RATE_DEMAND)}]
    assert m["work"][0] == 500 and m["work"][50] == 100 and m["off"][50] == 400
    assert m["kwh"]["C"] == 9600 and m["peak_share"] == round(m["kwh"]["A"] / sum(m["kwh"].values()), 3)
