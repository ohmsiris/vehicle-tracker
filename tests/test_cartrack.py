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


def test_without_a_key_it_says_where_to_put_it(monkeypatch):
    monkeypatch.setattr(cartrack, "configured", lambda: False)
    with pytest.raises(cartrack.CartrackError) as e:
        cartrack._get("/vehicles/status")
    assert "CARTRACK_API_KEY" in str(e.value)
