from app import hr_suggest as hs


def _s(kind, **p):
    return {"kind": kind, "payload": p}


def test_state_shows_what_confirming_would_change():
    emp = {"id": "e1", "status": "active", "end_date": None}
    assert hs.state(_s("resign", end_date="2026-10-25"), None, {}) == "ไม่พบรหัสนี้ในรายชื่อพนักงาน"
    assert hs.state(_s("resign", end_date="2026-10-25"), emp, {}) == "ในระบบ: ยังทำงานอยู่"
    gone = {**emp, "status": "resigned", "end_date": "2026-10-25"}
    assert "(ตรงกัน)" in hs.state(_s("resign", end_date="2026-10-25"), gone, {})
    loans_of = {"e1": [{"kind": "loan", "amount": "5000", "repay_mode": "flexible", "repay_amount": None}]}
    assert hs.state(_s("loan", loan_kind="loan"), emp, loans_of).startswith("ในระบบมีอยู่แล้ว")      # never a second loan
    assert hs.state(_s("loan", loan_kind="accident"), emp, loans_of) == "ในระบบ: ยังไม่มีเงินกู้ประเภทนี้"
