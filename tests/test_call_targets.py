from app import fleet

DRIVERS = [
    {"driver_id": "d1", "name": "ประสงค์ ใจดี", "nickname": "", "phone": "063-167-8509", "active": True},
    {"driver_id": "d2", "name": "ฉัตรชัย สุขใจ", "nickname": "ไก่", "phone": "090-842-4064", "active": True},
    {"driver_id": "d3", "name": "สมชาย มั่นคง", "nickname": "สม", "phone": "065-047-1656", "active": True},
    {"driver_id": "d4", "name": "สมหมาย แสนดี", "nickname": "", "phone": "081-000-0001", "active": True},
    {"driver_id": "d5", "name": "ประเสริฐ ลาออก", "nickname": "เสริฐ", "phone": "081-000-0002", "active": False},
    {"driver_id": "d6", "name": "วิชัย ไม่มีเบอร์", "nickname": "", "phone": "", "active": True},
]


def who(reporter):
    d = fleet.match_reporter(reporter, DRIVERS)
    return d["driver_id"] if d else None


def test_a_reporter_is_found_by_first_name_nickname_or_full_name():
    assert who("ประสงค์") == "d1"                  # first name
    assert who("ไก่") == "d2"                      # nickname
    assert who("ฉัตรชัย สุขใจ") == "d2"            # full name
    assert who("  ฉัตรชัย ") == "d2"               # spacing


def test_honorifics_are_ignored_on_either_side():
    assert who("คุณฉัตรชัย") == "d2" and who("พี่ประสงค์") == "d1" and who("นาย ฉัตรชัย") == "d2"


def test_the_start_of_a_name_counts_only_from_three_letters():
    assert who("สมช") == "d3" and who("สมห") == "d4"
    assert who("สม") == "d3"                       # "สม" is d3's nickname: an exact match wins over the two names that start with it
    assert who("ปร") is None                       # too short to guess


def test_two_drivers_with_the_same_name_is_not_a_match():
    drivers = DRIVERS + [{"driver_id": "d7", "name": "ประสงค์ อื่น", "nickname": "", "phone": "081-000-0003", "active": True}]
    assert fleet.match_reporter("ประสงค์", drivers) is None


def test_retired_drivers_and_drivers_without_a_number_cannot_be_called():
    assert who("เสริฐ") is None and who("ประเสริฐ") is None          # left the job
    assert who("วิชัย") is None                                       # no phone number
    assert who("") is None and who(None) is None and who("ไม่มีคนนี้") is None


def test_a_normal_truck_rings_its_main_driver():
    t = fleet.call_target("ฉัตรชัย", "รถสาย", DRIVERS[0], DRIVERS)
    assert t == {"kind": "driver", "name": "ประสงค์ ใจดี", "phone": "063-167-8509"}


def test_a_factory_truck_rings_whoever_reported_it():
    t = fleet.call_target("ฉัตรชัย", "รถหน้าโรง", None, DRIVERS)
    assert t == {"kind": "reporter", "name": "ฉัตรชัย", "phone": "090-842-4064"}
    # even if it has a main driver on file: a factory truck's reporter is the one to call
    assert fleet.call_target("ฉัตรชัย", "รถหน้าโรง", DRIVERS[0], DRIVERS)["kind"] == "reporter"


def test_no_driver_and_no_known_reporter_means_nobody_to_call():
    assert fleet.call_target("ใครก็ไม่รู้", "รถหน้าโรง", None, DRIVERS) is None
    assert fleet.call_target(None, "รถสาย", None, DRIVERS) is None


def test_a_truck_whose_driver_has_no_number_falls_back_to_the_reporter():
    t = fleet.call_target("ฉัตรชัย", "รถสาย", DRIVERS[5], DRIVERS)
    assert t["kind"] == "reporter" and t["phone"] == "090-842-4064"


def test_board_rows_get_their_call_target():
    rows = [{"request_id": "r1", "vehicle_id": "v1", "reported_by": "ฉัตรชัย"}, {"request_id": "r2", "vehicle_id": "v2", "reported_by": None},
            {"request_id": "r3", "vehicle_id": "unknown", "reported_by": None}]
    vehicles = [{"id": "v1", "usage": "รถหน้าโรง", "main_driver_id": None}, {"id": "v2", "usage": "รถสาย", "main_driver_id": "d1"}]
    out = fleet.with_call_targets(rows, vehicles, DRIVERS)
    assert out[0]["call"]["kind"] == "reporter" and out[1]["call"] == {"kind": "driver", "name": "ประสงค์ ใจดี", "phone": "063-167-8509"}
    assert out[2]["call"] is None
    assert "call" not in rows[0]                                       # the input is not changed
