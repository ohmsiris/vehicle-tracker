from app import extraction as ex


def _bill(**kw):
    b = {"shop_name": "บริษัท โชคพัฒนาสระบุรี เซอร์วิส จำกัด", "date": "2026-11-06", "vehicle_license": "2ฒฉ 3732", "vehicle_number": "52",
         "mileage": 400123, "next_service_mileage": None, "total_cost": 5300, "low_confidence_fields": [],
         "line_items": [{"quantity": 2, "unit": "เส้น", "description": "ยางเรเดียล", "cost": 5000, "category": "ยาง", "low_confidence_fields": []},
                        {"quantity": 0, "unit": "", "description": "ค่าแรง", "cost": 300, "category": "ค่าแรง", "low_confidence_fields": []}]}
    b.update(kw)
    return b


def test_two_readings_that_agree_add_nothing():
    out = ex.compare_reads(_bill(), _bill(vehicle_license="2ฒฉ-3732"))          # the same plate written another way
    assert out["second_read"]["agree"] is True and out["low_confidence_fields"] == []
    assert all(ln["low_confidence_fields"] == [] for ln in out["line_items"])


def test_whatever_the_second_reading_saw_differently_is_marked_to_check():
    second = _bill(total_cost=5800, mileage=400128)
    second["line_items"][0]["cost"] = 5500
    second["line_items"][1]["description"] = "ค่าแรงถอดใส่"
    out = ex.compare_reads(_bill(), second)
    assert out["total_cost"] == 5300                                                 # the first reading is kept
    assert set(out["low_confidence_fields"]) == {"mileage", "total_cost"}
    assert out["line_items"][0]["low_confidence_fields"] == ["cost"] and out["line_items"][1]["low_confidence_fields"] == ["description"]
    assert out["second_read"]["agree"] is False and out["second_read"]["lines_differ"] == 2


def test_a_line_only_one_reading_found_is_marked():
    second = _bill()
    second["line_items"] = second["line_items"][:1]
    out = ex.compare_reads(_bill(), second)
    assert "total_cost" in out["low_confidence_fields"] and set(out["line_items"][1]["low_confidence_fields"]) == {"description", "cost"}
    assert out["second_read"]["line_count"] == [2, 1]


def test_reads_twice_and_falls_back_to_one(monkeypatch):
    calls = []

    def fake(data, mime, roster=""):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("declined")
        return _bill(), {"input_tokens": 10, "output_tokens": 5}
    monkeypatch.setattr(ex, "_read_once", fake)
    bill, usage = ex.extract_file(b"x", "image/jpeg")
    assert len(calls) == 2 and bill["second_read"]["agree"] is None and usage["input_tokens"] == 10
