"""Cartrack GPS, step one: the connection check (owner, 29 Nov 2026)."""
import pytest

from app import cartrack


def test_vehicles_match_trucks_by_plate_whatever_the_spacing():
    body = {"data": [{"registration": "1ฒถ-8966", "odometer": 251000, "location": {"latitude": 14.5}},
                     {"registration": "84 4607", "odometer": 9},
                     {"registration": "ZZ 1", "odometer": 1}], "meta": {"total": 3}}
    vehicles = [{"plate_number": "1ฒถ 8966", "vehicle_number": "1", "branch": "สระบุรี", "active": True},
                {"plate_number": "84-4607", "vehicle_number": "31", "branch": "สระบุรี", "active": True},
                {"plate_number": "86-8971", "vehicle_number": "5", "branch": "แก่งคอย", "active": True}]
    got = cartrack.summarize(body, vehicles)
    assert (got["vehicles"], got["matched"], got["not_here_count"]) == (3, 2, 1) and got["matched_by_branch"] == {"สระบุรี": 2}
    assert [x["plate"] for x in got["not_here"]] == ["ZZ 1"] and [x["plate"] for x in got["no_gps"]] == ["86-8971"]
    assert "odometer" in got["fields"] and got["sample"]["registration"] == "1ฒถ-8966"
    assert cartrack.rows_of([{"registration": "a"}]) == [{"registration": "a"}]


def test_province_spacing_and_the_name_field_do_not_hide_a_truck_and_last_digits_are_only_a_guess():
    body = [{"registration": "1ฒถ-8966 สระบุรี"}, {"registration": "", "vehicle_name": "#31 สบ 84 - 4607"},
            {"registration": "2 ฒข 3727"}, {"registration": "1กข 8971"}, {"registration": "70-1234 กรุงเทพมหานคร"}]
    vehicles = [{"plate_number": "1ฒถ 8966", "vehicle_number": "1", "branch": "สระบุรี"},
                {"plate_number": "84-4607", "vehicle_number": "31", "branch": "สระบุรี"},
                {"plate_number": "2ฒข 3727", "vehicle_number": "49", "branch": "สระบุรี"},
                {"plate_number": "86-8971", "vehicle_number": "5", "branch": "แก่งคอย"}]
    got = cartrack.summarize(body, vehicles)
    assert sorted(m["truck"] for m in got["trucks"]) == ["1", "31", "49"]
    assert [x["truck"] for x in got["no_gps"]] == ["5"]
    first = got["not_here"][0]
    assert first["plate"] == "1กข 8971" and first["guess"]["truck"] == "5"
    assert "guess" not in got["not_here"][1]
    assert cartrack.core_plate("สบ 84-4607") == "844607" and cartrack.core_plate("2 ฒข 3727") == "2ฒข3727"


def test_without_a_key_it_says_where_to_put_it(monkeypatch):
    monkeypatch.setattr(cartrack, "configured", lambda: False)
    with pytest.raises(cartrack.CartrackError) as e:
        cartrack._get("/vehicles/status")
    assert "CARTRACK_API_KEY" in str(e.value)
