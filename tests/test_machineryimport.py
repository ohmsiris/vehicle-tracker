import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import machineryimport as mi

MAIN_HEADER = ["สถานที่", "อุปกรณ์", "ชิ้นส่วน", "สเปค", "Qty", "Unit", "รูลูกปืน", "Motor HP", "Delta A", "Star A", "Link", "Breaker", "Magnetic Contactor", "Overload"]
MOTOR_HEADER = ["No", "Location", "มอเตอร์", "Model", "HP", "Delta A", "Star A", "Link", "Breaker", "Magnetic", "Overload", "Status", "Current Magnetic", "Current Overload", "Desired A"]

MACHINES = [{**m, "id": f"m{i}"} for i, m in enumerate(mi.SARABURI_MACHINES, 1)]


# ------------------------------------------------------------------ machines
def test_the_seven_saraburi_machines_match_the_description():
    ms = mi.SARABURI_MACHINES
    assert len(ms) == 7
    assert [(m["number"], m["tube_type"], m["make"], m["capacity_tons"]) for m in ms[:5]] == [
        (1, "หลอดใหญ่", "MYCOM", 50), (2, "หลอดใหญ่", "MYCOM", 50), (3, "หลอดเหลี่ยม", "MYCOM", 50), (4, "หลอดเล็ก", "Sabroe", 30), (5, "หลอดใหญ่", "MYCOM", 50)]
    assert [(m["kind"], m["number"], m["make"]) for m in ms[5:]] == [("บ่อซอง", None, "Sabroe"), ("บ่อซอง", None, "MYCOM")]


@pytest.mark.parametrize("location, expected", [
    ("เครื่อง 3", ("เครื่อง", 3)), ("หลอด 4", ("เครื่อง", 4)), ("เครื่อง  1", ("เครื่อง", 1)), ("ซอง MYCOM", ("บ่อซอง", None)), ("ซอง SABROE", ("บ่อซอง", None)),
    ("ซอง", None), ("โรงซอง 1", None), ("ลานโหลด", None), ("หลอด 1,2", None), ("เครื่อง 9", None), (None, None),
])
def test_which_machine_a_sheet_location_belongs_to(location, expected):
    m = mi.machine_for(location, MACHINES)
    assert ((m["kind"], m["number"]) if m else None) == expected


def test_the_two_brine_pond_machines_are_told_apart_by_make():
    assert mi.machine_for("ซอง SABROE", MACHINES)["make"] == "Sabroe" and mi.machine_for("ซอง MYCOM", MACHINES)["make"] == "MYCOM"


# --------------------------------------------------------------------- parts
def test_parts_rows_are_read_with_numbers_text_and_multiline_cells():
    rows = [MAIN_HEADER,
            ["เครื่อง 1", "ปั๊มน้ำเย็น", "ปั๊มน้ำ", "Calpeda NM65/12C/B", "1", "ตัว", None, 7.5, 10.8, 6.2, "NM, NMS", "Fuji BW50EAG 40A", "Fuji SC-N2 (35) 220V", "Fuji TR-N2/3 18-26A"],
            ["เครื่อง 1", "คอมม์ ", "มอเตอร์", "Elektrim 2Sg315M4B\n380/660V 50Hz", "1", "ตัว", None, 220.0, 296.0, 171.0, "2Sg315M4B", None, "Fuji SC-N8\n\nFuji SC-N6", None],
            ["โรงซอง 1", "โม่", "ลูกปืน", 6307.0, "2", "ตลับ", "หุน"],
            ["โรงซอง 1", "ใบพัดน้ำเกลือ", "ลูกปืน", "N/A", "N/A", "ตลับ", "หุน"],
            [None, None, None], ["หลอด 4", "เครื่องกรอก", "ลูกปืน", "FYJ508", "", None, "หุน"]]
    p = mi.parse_main(rows)
    assert len(p) == 5
    assert (p[0]["spec"], p[0]["qty"], p[0]["motor_hp"], p[0]["delta_a"], p[0]["link"]) == ("Calpeda NM65/12C/B", 1, 7.5, "10.8", "NM, NMS")
    assert p[1]["equipment"] == "คอมม์" and p[1]["spec"] == "Elektrim 2Sg315M4B\n380/660V 50Hz" and p[1]["contactor"] == "Fuji SC-N8\nFuji SC-N6" and p[1]["motor_hp"] == 220
    assert p[2]["spec"] == "6307" and p[2]["qty"] == 2 and p[2]["bearing_bore"] == "หุน"
    assert p[3]["qty"] is None and p[3]["notes"] == "จำนวน: N/A"                      # an odd quantity is kept as a note, not lost
    assert p[4]["qty"] is None and p[4]["notes"] is None


def test_the_motor_index_is_read_and_header_and_summary_rows_are_ignored():
    rows = [MOTOR_HEADER,
            [1, "เครื่อง 1", "ปั๊มน้ำเย็น", "Calpeda NM65/12C/B", 7.5, 10.8, 6.2, "NM, NMS", "Fuji BW50EAG 40A", "Fuji SC-N2", "Fuji TR-N2/3", "New", "Fuji SC-N2", "Fuji TR-N2/3", 10.8],
            [4, "เครื่อง 1", "คัตเตอร์", None, None, None, None, None, "Fuji BW32AAG 30A", "Fuji SC-N1", "Fuji TR-N2/3 9-13A", "?"],
            ["Good", 11, None], ["= Unknown", "?", 15], [None, "Total Hp", 1612.0]]
    m = mi.parse_motors(rows)
    assert len(m) == 2 and m[0]["status"] == "New" and m[0]["desired_a"] == "10.8" and m[1]["status"] == "?" and m[1]["spec"] is None


def test_motor_index_details_are_folded_into_the_matching_part_row_or_added_as_a_new_motor():
    parts = mi.parse_main([MAIN_HEADER,
                           ["เครื่อง 1", "ปั๊มน้ำเย็น", "ปั๊มน้ำ", "Calpeda NM65/12C/B", "1", "ตัว", None, 7.5],
                           ["เครื่อง 1", "ปั๊มน้ำเย็น", "Breaker", "Fuji BW50EAG 40A", "1", "ตัว"]])
    motors = mi.parse_motors([MOTOR_HEADER,
                              [1, "เครื่อง 1", "ปั๊มน้ำเย็น", "Calpeda NM65/12C/B", 7.5, 10.8, 6.2, "NM, NMS", "Fuji BW50EAG 40A", "Fuji SC-N2", "Fuji TR-N2/3", "New", "Fuji SC-N2", "Fuji TR-N2/3", 10.8],
                              [44, "ซอง", "โม่", "Mitsubishi SF-J 160M", 15, "22.5 (380)", "21.5 (415)", "https://x"]])
    merged = mi.merge_motors(parts, motors)
    assert len(merged) == 3                                                           # one folded in, one new
    assert merged[0]["status"] == "New" and merged[0]["desired_a"] == "10.8" and merged[0]["delta_a"] == "10.8" and merged[0]["motor_hp"] == 7.5
    assert merged[1]["part"] == "Breaker" and "status" not in merged[1]                # the breaker row is not a motor
    new = merged[2]
    assert (new["location"], new["equipment"], new["part"], new["spec"], new["motor_hp"], new["qty"]) == ("ซอง", "โม่", "มอเตอร์", "Mitsubishi SF-J 160M", 15, 1)
    assert parts[0].get("status") is None                                              # the inputs are not changed


def test_pressure_relief_valves_become_one_row_each_for_machine_condenser_and_receiver():
    rows = [["เครื่อง", "สเปคเครื่อง"], [None, None, "รุ่น"],
            [1, "หลอดใหญ่ 50 ตัน", 'HANSEN H5602 3/4"-1"', 300, 1, 'HANSEN H5602 3/4"-1"', 275, 2, 'HANSEN H5602 3/4"-1"', 275, 2, "Three way", 225],
            [3, "หลอดเหลี่ยม 50 ตัน", "แบบเก่า", None, None, 'Henry 5601 1/2"-1"', 275, 2, 'Henry 5601 1/2"-1"', 275, 2],
            ["ซอง SABROE", "ซอง 30 ตัน", "ไม่มี/อยู่กับเครื่อง", None, None, 'Henry 5601 1/2"-1"', 275, 2, None, None, None],
            [None, "สรุป"], [None, "รุ่น", "PSI"]]
    prv = mi.parse_prv(rows)
    assert len(prv) == 3 + 3 + 2
    first = prv[0]
    assert (first["location"], first["part"], first["spec"], first["qty"], first["notes"]) == ("เครื่อง 1", "เครื่อง", 'HANSEN H5602 3/4"-1"', 1, "300 PSI")
    assert [p["part"] for p in prv if p["location"] == "ซอง SABROE"] == ["เครื่อง", "คอนเดนเซอร์"]
    old = next(p for p in prv if p["location"] == "เครื่อง 3" and p["part"] == "เครื่อง")
    assert old["spec"] == "แบบเก่า" and old["qty"] is None and old["notes"] is None


# -------------------------------------------------------------------- changes
def test_the_equipment_change_log_becomes_maintenance_reports():
    rows = [["No.", "Equipment", "Brand", "Model", "Serial Number", "Vendor", "Contact", "Date Changed", "Reason for Changing"],
            [1, "ปั๊มสูบจ่ายคลอรีน", "Prominent", "SD0313", 2024241223.0, None, None, datetime(2025, 9, 4), "Old one stopped working"],
            [2, "มาตรวัดน้ำ", "SANWA", 'Woltman WPD80 R100 3"', "0-007056", "SANWA Thailand", "Line: @sanwathailand", datetime(2025, 9, 11), "เศษพลาสติกติด"],
            [5, "มอเตอร์ตู้แพ็ค ตัวขวา", None, None, None, None, None, datetime(2025, 9, 20), None],
            [6, None, None, None, None, None, None, None, None], [7]]
    ev = mi.parse_changes(rows, "สระบุรี")
    assert [e["event_date"] for e in ev] == ["2025-09-04", "2025-09-11", "2025-09-20"]
    assert ev[0]["summary"] == "เปลี่ยน ปั๊มสูบจ่ายคลอรีน" and ev[0]["serial_number"] == "2024241223" and ev[0]["reason"] == "Old one stopped working" and ev[0]["kind"] == "เปลี่ยนอะไหล่"
    assert ev[1]["vendor"] == "SANWA Thailand" and ev[1]["branch"] == "สระบุรี"
    assert ev[2]["brand"] is None


# ----------------------------------------------------------------------- plan
def plan(parts=(), motors=(), prv=(), changes=(), existing_parts=(), existing_events=()):
    return mi.build_plan(parts=list(parts), motors=list(motors), prv=list(prv), changes=list(changes), machines=MACHINES,
                         existing_parts=list(existing_parts), existing_events=list(existing_events))


def P(location, equipment, part, spec, **kw):
    return {"location": location, "equipment": equipment, "part": part, "spec": spec, **kw}


def test_parts_are_linked_to_their_machine_and_areas_are_left_unlinked():
    p = plan(parts=[P("เครื่อง 2", "พัดลม", "Breaker", "Fuji BW50EAG 40A"), P("หลอด 3", "สกรู", "ลูกปืน", "F208"), P("โรงซอง 1", "โม่", "สายพาน", "B-82"),
                    P("หลอด 1,2", "สกรู", "ลูกปืน", "F 208")])
    by = {x["location"]: x for x in p["parts"]}
    assert by["เครื่อง 2"]["machine_id"] == "m2" and by["หลอด 3"]["machine_id"] == "m3"
    assert "machine_id" not in by["โรงซอง 1"] and p["unlinked_locations"] == ["หลอด 1,2", "โรงซอง 1"]
    assert [x["sort_order"] for x in p["parts"]] == [0, 1, 2, 3]


def test_rerunning_adds_nothing_but_a_genuinely_repeated_row_is_kept():
    rows = [P("เครื่อง 1", "คอมม์", "Magnetic Contactor", "Fuji SC-N8"), P("เครื่อง 1", "คอมม์", "Magnetic Contactor", "Fuji SC-N8"), P("เครื่อง 1", "คอมม์", "Breaker", "x")]
    first = plan(parts=rows)
    assert len(first["parts"]) == 3 and first["parts_skipped"] == 0
    again = plan(parts=rows, existing_parts=first["parts"])
    assert again["parts"] == [] and again["parts_skipped"] == 3
    partial = plan(parts=rows, existing_parts=[rows[0]])                              # one of the two identical rows exists already
    assert len(partial["parts"]) == 2


def test_changes_already_recorded_are_not_added_again():
    ev = [{"branch": "สระบุรี", "event_date": "2025-09-04", "equipment": "ปั๊ม", "summary": "เปลี่ยน ปั๊ม", "kind": "เปลี่ยนอะไหล่", "brand": None}]
    p = plan(changes=ev)
    assert p["events"] == [{"branch": "สระบุรี", "event_date": "2025-09-04", "equipment": "ปั๊ม", "summary": "เปลี่ยน ปั๊ม", "kind": "เปลี่ยนอะไหล่"}]
    assert plan(changes=ev, existing_events=ev)["events"] == [] and plan(changes=ev, existing_events=ev)["events_skipped"] == 1
